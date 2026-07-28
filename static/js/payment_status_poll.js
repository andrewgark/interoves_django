/**
 * Long-running payment status poller (crypto can take 20+ minutes).
 *
 * Usage:
 *   InterovesPaymentPoll.start({
 *     statusUrl: '...',
 *     storageKey: 'interoves_donation_poll',
 *     onPending: function () {},
 *     onConfirmed: function (data) {},  // donation Confirmed / ticket Accepted
 *     onRejected: function (data) {},
 *     isConfirmed: function (data) { return data.status === 'Confirmed'; },
 *     isRejected: function (data) { return data.status === 'Rejected'; },
 *   });
 */
(function (global) {
  'use strict';

  var MAX_MS = 2 * 60 * 60 * 1000; // 2 hours
  var INITIAL_DELAY_MS = 3000;
  var MAX_DELAY_MS = 15000;

  function readStorage(key) {
    try {
      var raw = sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      if (value == null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, JSON.stringify(value));
    } catch (e) { /* ignore */ }
  }

  function start(opts) {
    if (!opts || !opts.statusUrl) return null;

    var storageKey = opts.storageKey || null;
    var startedAt = Date.now();
    var delay = INITIAL_DELAY_MS;
    var timer = null;
    var stopped = false;
    var isConfirmed = opts.isConfirmed || function (d) {
      return d && (d.status === 'Confirmed' || d.status === 'Accepted');
    };
    var isRejected = opts.isRejected || function (d) {
      return d && d.status === 'Rejected';
    };

    if (storageKey) {
      writeStorage(storageKey, {
        statusUrl: opts.statusUrl,
        startedAt: startedAt,
        meta: opts.meta || null,
      });
    }

    function stop() {
      stopped = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    }

    function clearPersisted() {
      if (storageKey) writeStorage(storageKey, null);
    }

    function schedule() {
      if (stopped) return;
      timer = setTimeout(tick, delay);
      delay = Math.min(MAX_DELAY_MS, Math.round(delay * 1.4));
    }

    function tick() {
      if (stopped) return;
      if (Date.now() - startedAt > MAX_MS) {
        stop();
        if (typeof opts.onTimeout === 'function') opts.onTimeout();
        return;
      }
      fetch(opts.statusUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (res) {
          if (stopped) return;
          var data = res.data || {};
          if (!res.ok) {
            schedule();
            return;
          }
          if (isConfirmed(data)) {
            stop();
            clearPersisted();
            if (typeof opts.onConfirmed === 'function') opts.onConfirmed(data);
            return;
          }
          if (isRejected(data)) {
            stop();
            clearPersisted();
            if (typeof opts.onRejected === 'function') opts.onRejected(data);
            return;
          }
          if (typeof opts.onPending === 'function') opts.onPending(data);
          schedule();
        })
        .catch(function () {
          if (!stopped) schedule();
        });
    }

    if (typeof opts.onPending === 'function') opts.onPending({ status: 'Pending' });
    schedule();
    return { stop: stop, clearPersisted: clearPersisted };
  }

  function resume(storageKey, buildOpts) {
    var saved = readStorage(storageKey);
    if (!saved || !saved.statusUrl) return null;
    var elapsed = Date.now() - (saved.startedAt || Date.now());
    if (elapsed > MAX_MS) {
      writeStorage(storageKey, null);
      return null;
    }
    var opts = typeof buildOpts === 'function' ? buildOpts(saved) : (buildOpts || {});
    opts.statusUrl = saved.statusUrl;
    opts.storageKey = storageKey;
    opts.meta = saved.meta;
    return start(opts);
  }

  global.InterovesPaymentPoll = {
    start: start,
    resume: resume,
    readStorage: readStorage,
    writeStorage: writeStorage,
    MAX_MS: MAX_MS,
  };
})(typeof window !== 'undefined' ? window : this);
