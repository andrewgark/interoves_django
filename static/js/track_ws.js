(function (global) {
  'use strict';

  var SYNC_MS = 25000;
  var RECONNECT_MIN_MS = 1000;
  var RECONNECT_MAX_MS = 30000;

  function proto() {
    return window.location.protocol === 'https:' ? 'wss' : 'ws';
  }

  function host() {
    return window.location.host;
  }

  var lastReloadKey = '';
  var lastReloadAt = 0;
  function reloadTrackEventOnce(key) {
    var now = Date.now();
    if (key === lastReloadKey && now - lastReloadAt < 2000) return;
    lastReloadKey = key;
    lastReloadAt = now;
    window.location.reload();
  }

  function handleTrackEvent(msg) {
    if (!msg || msg.type !== 'track.event') return;
    if (
      msg.event === 'game.play_available' ||
      msg.event === 'game.started' ||
      msg.event === 'game.ended'
    ) {
      var gid = (msg.payload && msg.payload.game_id) || '';
      reloadTrackEventOnce(msg.event + ':' + gid);
    }
  }

  function applyTaskUpdates(msg) {
    if (msg.update_task_html_new && typeof global.applyNewUiTaskHtml === 'function') {
      global.applyNewUiTaskHtml(msg.update_task_html_new);
    } else if (msg.update_task_html && typeof global.updateTasks === 'function') {
      global.updateTasks(msg.update_task_html);
    }
    if (msg.raddle_ui && typeof global.applyRaddleUiState === 'function') {
      global.applyRaddleUiState(msg.raddle_ui, {fromRemote: true});
    }
    if (msg.update_task_group_title_html && typeof global.updateTaskGroupTitle === 'function') {
      global.updateTaskGroupTitle(msg.update_task_group_title_html);
    }
    if (msg.update_game_title_html && typeof global.updateGameTitle === 'function') {
      global.updateGameTitle(msg.update_game_title_html);
    }
  }

  function acceptFreshSequence(msg, seen) {
    if (!msg || typeof msg.seq !== 'number') return true;
    var namespace = String(msg.seq_namespace || 'legacy');
    var previous = seen[namespace];
    if (typeof previous === 'number' && msg.seq <= previous) return false;
    seen[namespace] = msg.seq;
    return true;
  }

  function nextGamePhaseAt(root, nowMs) {
    if (!root || typeof root.getAttribute !== 'function') return null;
    var now = typeof nowMs === 'number' ? nowMs : Date.now();
    var candidates = [
      'data-game-start-at',
      'data-game-end-at',
      'data-live-next-transition-at',
    ].map(function (name) {
      var raw = root.getAttribute(name);
      var value = raw ? Date.parse(raw) : NaN;
      return Number.isFinite(value) && value > now ? value : null;
    }).filter(function (value) { return value !== null; });
    if (!candidates.length) return null;
    return Math.min.apply(Math, candidates);
  }

  function scheduleGamePhaseReload(root) {
    var MAX_DELAY_MS = 2147483000;
    function schedule() {
      var now = Date.now();
      var transitionAt = nextGamePhaseAt(root, now);
      if (transitionAt === null) return;
      var remaining = transitionAt - now + 250;
      setTimeout(function () {
        if (remaining > MAX_DELAY_MS) {
          schedule();
          return;
        }
        reloadTrackEventOnce('game-phase:' + transitionAt);
      }, Math.min(remaining, MAX_DELAY_MS));
    }
    schedule();
  }

  function collectTaskIds(root) {
    if (!root || typeof root.querySelectorAll !== 'function') return [];
    var seen = {};
    var ids = [];
    root.querySelectorAll('[id^="new-task-"]').forEach(function (el) {
      var match = String(el.id || '').match(/^new-task-(\d+)$/);
      if (!match || seen[match[1]]) return;
      seen[match[1]] = true;
      ids.push(match[1]);
    });
    return ids;
  }

  function reconcileTaskGroup(root) {
    var doc = root && root.body ? root : null;
    var body = doc ? doc.body : root;
    var endpoint = body && body.getAttribute
      ? body.getAttribute('data-track-live-state-url')
      : '';
    var taskIds = collectTaskIds(doc || root);
    if (!endpoint || !taskIds.length || typeof global.fetch !== 'function') {
      return Promise.reject(new Error('live state unavailable'));
    }
    var joiner = endpoint.indexOf('?') === -1 ? '?' : '&';
    return global.fetch(endpoint + joiner + 'task_ids=' + encodeURIComponent(taskIds.join(',')), {
      credentials: 'same-origin',
      headers: {'X-Requested-With': 'XMLHttpRequest'},
      cache: 'no-store',
    }).then(function (response) {
      if (!response.ok) throw new Error('live state HTTP ' + response.status);
      return response.json();
    }).then(function (data) {
      if (!data || data.status !== 'ok' || data.reload_required) {
        throw new Error('live state requires reload');
      }
      if (typeof global.applyNewUiTaskHtml !== 'function') {
        throw new Error('task projection unavailable');
      }
      global.applyNewUiTaskHtml(data.update_task_html_new || {});
      if (data.raddle_ui && typeof global.applyRaddleUiState === 'function') {
        global.applyRaddleUiState(data.raddle_ui, {fromRemote: true});
      }
      if (doc && typeof doc.dispatchEvent === 'function' && typeof global.CustomEvent === 'function') {
        doc.dispatchEvent(new global.CustomEvent('interoves:reconciled', {detail: data}));
      }
      return data;
    });
  }

  /**
   * Persistent WebSocket with exponential reconnect backoff and revision sync.
   * onMessage(msg) receives parsed JSON except ping/pong.
   */
  function openTrackSocket(url, onMessage, onResync, onSynced) {
    var delay = RECONNECT_MIN_MS;
    var socket = null;
    var pingTimer = null;
    var reconnectTimer = null;
    var stopped = false;
    var seenSequences = {};
    var resyncing = false;
    var queuedMessages = [];
    var doc = global.document;

    function rememberSyncBaseline(versions) {
      if (!versions || typeof versions !== 'object') return;
      Object.keys(versions).forEach(function (namespace) {
        if (typeof seenSequences[namespace] === 'number') return;
        var value = Number(versions[namespace]);
        if (Number.isFinite(value) && value >= 0) seenSequences[namespace] = value;
      });
    }

    function advanceSeenVersions(versions) {
      if (!versions || typeof versions !== 'object') return;
      Object.keys(versions).forEach(function (namespace) {
        var value = Number(versions[namespace]);
        if (!Number.isFinite(value) || value < 0) return;
        if (typeof seenSequences[namespace] !== 'number' || value > seenSequences[namespace]) {
          seenSequences[namespace] = value;
        }
      });
    }

    function flushQueuedMessages() {
      var messages = queuedMessages;
      queuedMessages = [];
      messages.sort(function (left, right) {
        return Number(left.seq || 0) - Number(right.seq || 0);
      });
      messages.forEach(function (msg) {
        if (!acceptFreshSequence(msg, seenSequences)) return;
        if (typeof onMessage === 'function') onMessage(msg);
      });
    }

    function runReconcile(work, fallbackToReload, fallbackKey, versions) {
      resyncing = true;
      Promise.resolve(work).then(function (result) {
        advanceSeenVersions((result && result.versions) || versions);
        resyncing = false;
        flushQueuedMessages();
      }).catch(function () {
        resyncing = false;
        if (fallbackToReload) {
          reloadTrackEventOnce(fallbackKey);
        } else {
          flushQueuedMessages();
        }
      });
    }

    function clearPing() {
      if (pingTimer) {
        clearInterval(pingTimer);
        pingTimer = null;
      }
    }

    function requestSync() {
      if (!socket || socket.readyState !== 1) return;
      try {
        socket.send(JSON.stringify({ type: 'track.sync', seen: seenSequences }));
      } catch (err) {}
    }

    function onVisibilityChange() {
      if (!doc || doc.visibilityState !== 'visible') return;
      requestSync();
    }

    function scheduleReconnect() {
      if (stopped || reconnectTimer) return;
      reconnectTimer = setTimeout(function () {
        reconnectTimer = null;
        connect();
      }, delay);
      delay = Math.min(RECONNECT_MAX_MS, delay * 2);
    }

    function connect() {
      if (stopped) return;
      clearPing();
      try {
        socket = new WebSocket(url);
      } catch (e) {
        scheduleReconnect();
        return;
      }

      socket.onopen = function () {
        delay = RECONNECT_MIN_MS;
        requestSync();
        clearPing();
        pingTimer = setInterval(requestSync, SYNC_MS);
      };

      socket.onmessage = function (ev) {
        try {
          var msg = JSON.parse(ev.data);
          if (!msg || msg.type === 'pong') return;
          if (msg.type === 'track.resync_required') {
            if (resyncing) return;
            if (typeof onResync !== 'function') {
              reloadTrackEventOnce('resync:' + url);
              return;
            }
            runReconcile(
              onResync(msg), true, 'resync:' + url, msg.versions
            );
            return;
          }
          if (msg.type === 'track.synced') {
            rememberSyncBaseline(msg.versions);
            if (typeof onSynced === 'function' && !resyncing) {
              var syncedWork = onSynced(msg);
              if (syncedWork && typeof syncedWork.then === 'function') {
                runReconcile(syncedWork, false, '', msg.versions);
              }
            }
            return;
          }
          if (resyncing) {
            queuedMessages.push(msg);
            return;
          }
          if (!acceptFreshSequence(msg, seenSequences)) return;
          if (typeof onMessage === 'function') onMessage(msg);
        } catch (e) {}
      };

      socket.onerror = function () {
        // onclose follows; reconnect there
      };

      socket.onclose = function () {
        clearPing();
        socket = null;
        scheduleReconnect();
      };
    }

    if (doc && typeof doc.addEventListener === 'function') {
      doc.addEventListener('visibilitychange', onVisibilityChange);
    }
    connect();

    return {
      close: function () {
        stopped = true;
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        clearPing();
        if (doc && typeof doc.removeEventListener === 'function') {
          doc.removeEventListener('visibilitychange', onVisibilityChange);
        }
        if (socket) {
          try {
            socket.close();
          } catch (e) {}
          socket = null;
        }
      },
    };
  }

  function connectUserHub() {
    return openTrackSocket(proto() + '://' + host() + '/ws/track/', function (msg) {
      handleTrackEvent(msg);
    });
  }

  function connectGame(gameId) {
    if (!gameId) return null;
    var url = proto() + '://' + host() + '/games/' + encodeURIComponent(gameId) + '/track';
    var initialReconciled = false;
    var initialReconcileInFlight = false;
    return openTrackSocket(url, function (msg) {
      if (msg.type === 'track.event') {
        handleTrackEvent(msg);
        return;
      }
      applyTaskUpdates(msg);
    }, function () {
      return reconcileTaskGroup(global.document);
    }, function () {
      var body = global.document && global.document.body;
      if (
        initialReconciled || initialReconcileInFlight || !body ||
        !body.getAttribute('data-track-live-state-url')
      ) return;
      initialReconcileInFlight = true;
      return reconcileTaskGroup(global.document).then(function (data) {
        initialReconciled = true;
        initialReconcileInFlight = false;
        return data;
      }).catch(function () {
        // Keep the rendered page usable; retry on the next periodic sync.
        initialReconcileInFlight = false;
        throw new Error('initial live-state reconciliation failed');
      });
    });
  }

  global.InterovesTrack = {
    connectUserHub: connectUserHub,
    connectGame: connectGame,
    openTrackSocket: openTrackSocket,
    acceptFreshSequence: acceptFreshSequence,
    nextGamePhaseAt: nextGamePhaseAt,
    scheduleGamePhaseReload: scheduleGamePhaseReload,
    collectTaskIds: collectTaskIds,
    reconcileTaskGroup: reconcileTaskGroup,
  };
})(window);
