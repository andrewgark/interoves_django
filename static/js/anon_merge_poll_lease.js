/* Coordinate anonymous-merge polling between tabs.
 * Local check: node static/js/anon_merge_poll_lease.test.js
 */
(function (global) {
  'use strict';

  function create(options) {
    options = options || {};
    var storage = options.storage || null;
    var now = options.now || function () { return Date.now(); };
    var key = options.key || 'interoves_anon_merge_poll';
    var owner = options.owner || Math.random().toString(36).slice(2);
    var leaseMs = options.leaseMs || 5000;

    function read() {
      if (!storage) return null;
      try {
        var value = storage.getItem(key);
        return value ? JSON.parse(value) : null;
      } catch (e) {
        return null;
      }
    }

    function write(expiresAt) {
      if (!storage) return true;
      try {
        storage.setItem(key, JSON.stringify({ owner: owner, expiresAt: expiresAt }));
        var value = read();
        return !!value && value.owner === owner;
      } catch (e) {
        return true;
      }
    }

    function acquire() {
      var current = read();
      if (current && current.owner !== owner && Number(current.expiresAt) > now()) return false;
      return write(now() + leaseMs);
    }

    function renew() {
      var current = read();
      if (!current || current.owner !== owner) return false;
      return write(now() + leaseMs);
    }

    function release() {
      if (!storage) return;
      try {
        var current = read();
        if (current && current.owner === owner) storage.removeItem(key);
      } catch (e) {}
    }

    return { acquire: acquire, renew: renew, release: release };
  }

  global.InterovesAnonMergePollLease = { create: create };
})(typeof window !== 'undefined' ? window : global);
