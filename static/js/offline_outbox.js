/**
 * Queue of gameplay moves made without the network (docs/offline-play-plan.md,
 * phase 2).
 *
 * Each entry is a POST the page could not send: its url, its form fields as
 * [name, value] pairs and its client_event_id. Entries are kept in
 * localStorage, so a reload or a crash does not lose them, and are sent one by
 * one, in order, when the network is back. Sending twice is harmless: the
 * server answers a known client_event_id with the move it already recorded,
 * so another tab flushing the same queue cannot double a move.
 *
 * Local check: node static/js/offline_outbox.test.js
 */
(function (global) {
  'use strict';

  var STORAGE_KEY = 'interoves:offline-outbox:v1';

  function memoryStorage() {
    var data = Object.create(null);
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
      setItem: function (k, v) { data[k] = String(v); },
      removeItem: function (k) { delete data[k]; },
    };
  }

  function createOutbox(options) {
    options = options || {};
    var storage = options.storage || memoryStorage();
    var key = options.key || STORAGE_KEY;

    function read() {
      try {
        var raw = storage.getItem(key);
        var list = raw ? JSON.parse(raw) : [];
        return Array.isArray(list) ? list : [];
      } catch (e) {
        return [];
      }
    }

    function write(list) {
      try {
        if (list.length) storage.setItem(key, JSON.stringify(list));
        else storage.removeItem(key);
        return true;
      } catch (e) {
        return false;
      }
    }

    return {
      /** Append a move; false if storage refused it (the move is then lost on reload). */
      add: function (entry) {
        var list = read();
        if (list.some(function (e) { return e.id === entry.id; })) return true;
        list.push(entry);
        return write(list);
      },
      entries: function (scope) {
        return read().filter(function (e) { return scope == null || String(e.scope) === String(scope); });
      },
      size: function (scope) {
        return this.entries(scope).length;
      },
      remove: function (id) {
        write(read().filter(function (e) { return e.id !== id; }));
      },
      clear: function (scope) {
        write(read().filter(function (e) { return scope != null && String(e.scope) !== String(scope); }));
      },
    };
  }

  /**
   * Send the queued moves in order. send(entry) resolves to one of
   *   'sent'  — the server took the move (or already had it): drop it;
   *   'retry' — no network or a server hiccup: stop, keep it and the rest;
   *   'drop'  — the server will never take it (e.g. another account): drop it.
   * Resolves to { sent, dropped, left }.
   */
  function flush(outbox, scope, send) {
    var result = { sent: 0, dropped: 0, left: 0 };
    var queue = outbox.entries(scope);

    function next(i) {
      if (i >= queue.length) return Promise.resolve(result);
      var entry = queue[i];
      return Promise.resolve()
        .then(function () { return send(entry); })
        .catch(function () { return 'retry'; })
        .then(function (verdict) {
          if (verdict === 'retry') {
            result.left = queue.length - i;
            return result;
          }
          outbox.remove(entry.id);
          if (verdict === 'drop') result.dropped++;
          else result.sent++;
          return next(i + 1);
        });
    }

    return next(0);
  }

  /** [[name, value], ...] of a FormData without files. */
  function formFields(formData) {
    var out = [];
    formData.forEach(function (value, name) {
      if (typeof value === 'string') out.push([name, value]);
    });
    return out;
  }

  global.InterovesOutbox = {
    STORAGE_KEY: STORAGE_KEY,
    createOutbox: createOutbox,
    flush: flush,
    formFields: formFields,
  };
})(typeof window !== 'undefined' ? window : globalThis);
