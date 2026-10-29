/**
 * Client-generated event ids (UUID v4) for idempotent gameplay requests.
 *
 * The server parses these ids as UUIDs and ignores anything else, so the
 * fallback for browsers without crypto.randomUUID (older Safari/Chrome) must
 * still produce a well-formed v4 UUID.
 *
 * Local check: node static/js/client_event_id.test.js
 */
(function (root) {
  'use strict';

  var UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function isUuid(value) {
    return typeof value === 'string' && UUID_RE.test(value);
  }

  function create() {
    try {
      if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    } catch (e) {}
    var bytes = new Array(16);
    try {
      if (root.crypto && root.crypto.getRandomValues) {
        bytes = Array.prototype.slice.call(root.crypto.getRandomValues(new Uint8Array(16)));
      }
    } catch (e2) {}
    for (var i = 0; i < 16; i++) {
      if (typeof bytes[i] !== 'number') bytes[i] = Math.floor(Math.random() * 256);
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = bytes.map(function (b) { return (b + 0x100).toString(16).slice(1); }).join('');
    return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16)
      + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
  }

  root.InterovesClientEventId = {
    create: create,
    isUuid: isUuid,
  };
})(typeof window !== 'undefined' ? window : globalThis);
