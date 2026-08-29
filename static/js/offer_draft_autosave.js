(function (global) {
  'use strict';

  var DEFAULT_DELAY_MS = 900;

  function jsonClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function payloadJson(value) {
    return JSON.stringify(value);
  }

  function create(options) {
    options = options || {};
    var storage = options.storage || global.localStorage;
    var delayMs = options.delayMs == null ? DEFAULT_DELAY_MS : options.delayMs;
    var setTimer = options.setTimer || global.setTimeout.bind(global);
    var clearTimer = options.clearTimer || global.clearTimeout.bind(global);
    var active = null;
    var generation = 0;

    function keyFor(id) {
      return String(options.storagePrefix || 'interoves_offer_draft_v1') + ':' + String(id);
    }

    function setStatus(session, state, detail) {
      if (active !== session || typeof options.onStatus !== 'function') return;
      options.onStatus(state, detail || {});
    }

    function removeStored(id) {
      try {
        storage.removeItem(keyFor(id));
      } catch (e) {}
    }

    function store(session) {
      try {
        storage.setItem(keyFor(session.id), JSON.stringify({
          version: 1,
          savedAt: new Date().toISOString(),
          payload: session.payload
        }));
        session.storedLocally = true;
        return true;
      } catch (e) {
        session.storedLocally = false;
        return false;
      }
    }

    function readStored(id) {
      var raw;
      try {
        raw = storage.getItem(keyFor(id));
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        if (!parsed || parsed.version !== 1 || !parsed.payload) throw new Error('bad draft');
        return parsed;
      } catch (e) {
        if (raw) removeStored(id);
        return null;
      }
    }

    function canSync(payload) {
      return typeof options.canSync !== 'function' || options.canSync(payload);
    }

    function cancelTimer(session) {
      if (session && session.timer != null) {
        clearTimer(session.timer);
        session.timer = null;
      }
    }

    function schedule(session) {
      cancelTimer(session);
      session.timer = setTimer(function () {
        session.timer = null;
        enqueueSave(session).catch(function () {});
      }, delayMs);
    }

    function currentPayload(session) {
      if (active === session && typeof options.getPayload === 'function') {
        session.payload = jsonClone(options.getPayload());
        session.payloadJson = payloadJson(session.payload);
      }
      return session.payload;
    }

    function performSave(session, requireServer) {
      var payload = jsonClone(currentPayload(session));
      var snapshotJson = payloadJson(payload);
      if (snapshotJson === session.serverJson) {
        if (session.payloadJson === snapshotJson) {
          removeStored(session.id);
          session.storedLocally = false;
        }
        setStatus(session, 'saved');
        return Promise.resolve(null);
      }
      if (!canSync(payload)) {
        var message = typeof options.syncBlockedMessage === 'function'
          ? options.syncBlockedMessage(payload)
          : 'Заполните обязательные поля';
        setStatus(session, session.storedLocally ? 'local' : 'error', { message: message });
        if (requireServer) return Promise.reject(new Error(message));
        return Promise.resolve(null);
      }

      setStatus(session, 'saving');
      return Promise.resolve().then(function () {
        return options.save(session.id, payload);
      }).then(function (result) {
        session.serverJson = snapshotJson;
        if (session.payloadJson === snapshotJson) {
          removeStored(session.id);
          session.storedLocally = false;
          setStatus(session, 'saved', { savedAt: new Date() });
        } else {
          store(session);
          setStatus(session, 'pending');
          if (active === session) schedule(session);
        }
        if (typeof options.onSaved === 'function') options.onSaved(result, session.id);
        return result;
      }).catch(function (error) {
        store(session);
        setStatus(session, session.storedLocally ? 'local' : 'error', { error: error });
        throw error;
      });
    }

    function enqueueSave(session, requireServer) {
      cancelTimer(session);
      var run = session.queue.then(function () {
        return performSave(session, !!requireServer);
      });
      session.queue = run.catch(function () {});
      return run;
    }

    function noteChange() {
      var session = active;
      if (!session) return;
      currentPayload(session);
      if (session.payloadJson === session.serverJson) {
        cancelTimer(session);
        removeStored(session.id);
        session.storedLocally = false;
        setStatus(session, 'saved');
        return;
      }
      var stored = store(session);
      if (!canSync(session.payload)) {
        setStatus(session, stored ? 'local' : 'error', {
          message: typeof options.syncBlockedMessage === 'function'
            ? options.syncBlockedMessage(session.payload)
            : 'Заполните обязательные поля'
        });
        cancelTimer(session);
        return;
      }
      setStatus(session, 'pending');
      schedule(session);
    }

    function close() {
      var session = active;
      if (!session) return Promise.resolve(null);
      currentPayload(session);
      cancelTimer(session);
      var result = enqueueSave(session, false).catch(function () { return null; });
      active = null;
      return result;
    }

    return {
      open: function (id, serverPayload) {
        if (active) close();
        var serverCopy = jsonClone(serverPayload);
        var session = {
          id: id,
          generation: ++generation,
          payload: serverCopy,
          payloadJson: payloadJson(serverCopy),
          serverJson: payloadJson(serverCopy),
          storedLocally: false,
          timer: null,
          queue: Promise.resolve()
        };
        active = session;
        var draft = readStored(id);
        if (draft && payloadJson(draft.payload) !== session.serverJson) {
          session.payload = jsonClone(draft.payload);
          session.payloadJson = payloadJson(session.payload);
          session.storedLocally = true;
          if (typeof options.restorePayload === 'function') {
            options.restorePayload(jsonClone(session.payload));
          }
          setStatus(session, 'restored', { savedAt: draft.savedAt });
          if (canSync(session.payload)) schedule(session);
          return true;
        }
        removeStored(id);
        setStatus(session, 'idle');
        return false;
      },
      changed: noteChange,
      flush: function () {
        if (!active) return Promise.resolve(null);
        currentPayload(active);
        store(active);
        return enqueueSave(active, true);
      },
      close: close,
      discard: function (id) {
        removeStored(id);
      },
      hasActiveDraft: function () {
        return !!active && active.payloadJson !== active.serverJson;
      }
    };
  }

  global.OfferDraftAutosave = {
    DEFAULT_DELAY_MS: DEFAULT_DELAY_MS,
    create: create
  };
})(typeof window !== 'undefined' ? window : globalThis);
