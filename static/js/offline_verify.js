/**
 * Browser twin of games/puzzles/core/client_spec.py: runs a checker's client
 * spec (normalization steps + salted SHA-256 hashes of accepted answers).
 *
 * Verdicts must match the Python reference interpreter, which
 * contracts/client_spec.json pins. Hashing uses crypto.subtle, so both
 * functions are async and need a secure context (https or localhost).
 * Requires static/js/offline_normalize.js.
 *
 * Local check: node static/js/offline_verify.test.js
 */
(function (global) {
  'use strict';

  var SPEC_VERSION = 1;

  function hex(buffer) {
    var bytes = new Uint8Array(buffer);
    var out = '';
    for (var i = 0; i < bytes.length; i++) {
      out += (bytes[i] + 0x100).toString(16).slice(1);
    }
    return out;
  }

  function answerHash(salt, normalizedAnswer) {
    var data = new TextEncoder().encode(salt + '\n' + normalizedAnswer);
    return global.crypto.subtle.digest('SHA-256', data).then(hex);
  }

  function checkVersion(spec) {
    if (!spec || spec.v !== SPEC_VERSION) {
      throw new Error('unsupported client spec version: ' + (spec && spec.v));
    }
  }

  /** Status of an answer under a hash_set or any spec: Ok, Partial or Wrong. */
  function verify(spec, text) {
    checkVersion(spec);
    if (spec.kind === 'any') return Promise.resolve('Ok');
    if (spec.kind !== 'hash_set') {
      return Promise.reject(new Error('not an answer spec: ' + spec.kind));
    }
    var normalized = global.OfflineNormalize.normalize(text, spec.ops);
    return answerHash(spec.salt, normalized).then(function (digest) {
      return Object.prototype.hasOwnProperty.call(spec.accept, digest) ? spec.accept[digest] : 'Wrong';
    });
  }

  /** Whether word is an accepted spelling of ladder word wordIndex. */
  function verifyWord(spec, wordIndex, word) {
    checkVersion(spec);
    if (spec.kind !== 'raddle') {
      return Promise.reject(new Error('not a ladder spec: ' + spec.kind));
    }
    var accepted = spec.words[wordIndex];
    if (!Number.isInteger(wordIndex) || !accepted) return Promise.resolve(false);
    var normalized = global.OfflineNormalize.normalize(word, spec.ops);
    return answerHash(spec.salt, normalized).then(function (digest) {
      return accepted.indexOf(digest) !== -1;
    });
  }

  global.OfflineVerify = {
    SPEC_VERSION: SPEC_VERSION,
    answerHash: answerHash,
    verify: verify,
    verifyWord: verifyWord,
  };
})(typeof window !== 'undefined' ? window : globalThis);
