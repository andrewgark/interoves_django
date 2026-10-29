/**
 * Named text normalization steps — browser twin of
 * games/puzzles/core/normalize_ops.py.
 *
 * Step names and behaviour must match the Python registry exactly: a server
 * checker's pipeline is shipped to the page and run here. Shared cases live in
 * contracts/normalize.json and run on both sides.
 *
 * Python and JS disagree on what "whitespace" is (str.strip / re \s include
 * \x1c-\x1f and \x85; JS trim and \s include ﻿ instead), so the Python set
 * is spelled out below. String.prototype.toLowerCase matches str.lower except
 * for letters added in Unicode versions newer than the server's Python.
 *
 * Local check: node static/js/offline_normalize.test.js
 */
(function (global) {
  'use strict';

  var PY_SPACE = '\\t\\n\\u000b\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a' +
    '\\u2028\\u2029\\u202f\\u205f\\u3000';
  var STRIP_RE = new RegExp('^[' + PY_SPACE + ']+|[' + PY_SPACE + ']+$', 'g');
  var INNER_SPACES_RE = new RegExp('[' + PY_SPACE.replace('\\n', '').replace('\\r', '') + ']+', 'g');
  var PUNCT_RE = /[.,\/#!?$%\^&\*;:{}="\-_`~()—–]+/g;
  var PUNCT_METAGRAM_RE = /[.,\/#!?$%\^&\*;:{}="\-_`~()—]+/g;
  var SPACE_RUN_RE = / +/g;
  // Python's re.I on [^0-9a-zа-яё] also keeps the letters that case-fold into
  // that range (İ ı ſ K, old Cyrillic forms); JS /i does not, so they are listed.
  var NON_WORD_CHAR_RE = /[^0-9a-zA-Zа-яА-ЯёЁİıſKᲀ-ᲆ]/g;

  function strip(text) {
    return text.replace(STRIP_RE, '');
  }

  function dropPunct(text) {
    return text.split('\n').map(function (line) {
      line = strip(line);
      if (line.length !== 1) {
        line = line.replace(PUNCT_RE, '').replace(SPACE_RUN_RE, ' ');
      }
      return line;
    }).join('\n');
  }

  var OPS = {
    lower: function (text) { return text.toLowerCase(); },
    strip: strip,
    yo_to_e: function (text) { return text.replace(/ё/g, 'е'); },
    drop_inner_spaces: function (text) { return text.replace(INNER_SPACES_RE, ''); },
    drop_punct: dropPunct,
    drop_punct_metagram: function (text) {
      return text.replace(PUNCT_METAGRAM_RE, ' ').replace(SPACE_RUN_RE, ' ');
    },
    keep_word_chars: function (text) { return text.replace(NON_WORD_CHAR_RE, ''); },
  };

  var CLEAN = ['lower', 'strip', 'yo_to_e'];

  function validateOps(ops) {
    var unknown = ops.filter(function (op) { return !Object.prototype.hasOwnProperty.call(OPS, op); });
    if (unknown.length) {
      throw new Error('unknown normalization steps: ' + unknown.join(', '));
    }
    return ops;
  }

  function normalize(text, ops) {
    validateOps(ops);
    var result = String(text == null ? '' : text);
    for (var i = 0; i < ops.length; i++) {
      result = OPS[ops[i]](result);
    }
    return result;
  }

  global.OfflineNormalize = {
    OPS: OPS,
    CLEAN: CLEAN,
    CLEAN_KEEP_YO: ['lower', 'strip'],
    RADDLE_WORD: ['keep_word_chars'].concat(CLEAN),
    validateOps: validateOps,
    normalize: normalize,
  };
})(typeof window !== 'undefined' ? window : globalThis);
