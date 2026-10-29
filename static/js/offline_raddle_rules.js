/**
 * Лесенка: rules of one submitted word — browser twin of
 * games/puzzles/raddle/transition.py (refuse_word, advance, progress_status)
 * and engine.playable_word_indices.
 *
 * The ladder shape comes from transition.rules_config(parsed):
 * { n_words, n_hints, lengths: [letters | null] }. Points are server-only and
 * are not mirrored. contracts/raddle_rules.json is generated from the Python
 * rules and pins this file. Requires static/js/offline_normalize.js.
 *
 * Local check: node static/js/offline_raddle_rules.test.js
 */
(function (global) {
  'use strict';

  function solvedSet(state) {
    var out = Object.create(null);
    (state.solved_indices || []).forEach(function (i) { out[i] = true; });
    return out;
  }

  function sortedNumbers(values) {
    return values.slice().sort(function (a, b) { return a - b; });
  }

  /** Unsolved words next to a solved one: the first and last unsolved index. */
  function playableWordIndices(state, nWords) {
    var solved = solvedSet(state);
    var open = [];
    for (var i = 0; i < nWords; i++) {
      if (!solved[i]) open.push(i);
    }
    if (!open.length) return [];
    if (open.length === 1) return [open[0]];
    return [open[0], open[open.length - 1]];
  }

  function wordLengthMatches(word, length) {
    if (length === null || length === undefined) return true;
    return global.OfflineNormalize.normalize(word, global.OfflineNormalize.RADDLE_WORD).length === length;
  }

  /** Why the word is not checked at all, or null when it may be checked. */
  function refuseWord(state, ladder, wordIndex, word) {
    var n = ladder.n_words;
    if (!word) return 'Пустой ответ';
    if (wordIndex < 0 || wordIndex >= n) return 'Неверный индекс слова';
    if (solvedSet(state)[wordIndex]) return 'Это слово уже решено';
    if (playableWordIndices(state, n).indexOf(wordIndex) === -1) {
      return 'Сейчас можно сдавать только крайние нерешённые слова';
    }
    if (!wordLengthMatches(word, ladder.lengths[wordIndex])) return 'Длина слова не совпадает с маской';
    return null;
  }

  /** State after a correct word: solved, and clues to solved neighbours used. */
  function advance(state, ladder, wordIndex) {
    var solvedBefore = solvedSet(state);
    var used = Object.create(null);
    (state.used_hints || []).forEach(function (i) { used[i] = true; });
    if (solvedBefore[wordIndex - 1] && wordIndex - 1 >= 0 && wordIndex - 1 < ladder.n_hints) {
      used[wordIndex - 1] = true;
    }
    if (solvedBefore[wordIndex + 1] && wordIndex >= 0 && wordIndex < ladder.n_hints) {
      used[wordIndex] = true;
    }
    solvedBefore[wordIndex] = true;
    var next = {};
    Object.keys(state).forEach(function (key) { next[key] = state[key]; });
    next.solved_indices = sortedNumbers(Object.keys(solvedBefore).map(Number));
    next.used_hints = sortedNumbers(Object.keys(used).map(Number));
    return next;
  }

  /** [status, tournamentStatus] of the ladder after a checked word. */
  function progressStatus(state, nWords, wordCorrect) {
    var solvedCount = (state.solved_indices || []).length;
    var middleTotal = Math.max(0, nWords - 2);
    var solvedMiddle = solvedCount - (nWords >= 2 ? 2 : solvedCount);
    if (middleTotal && solvedMiddle >= middleTotal) return ['Ok', 'Ok'];
    var status = solvedMiddle > 0 ? 'Partial' : 'Wrong';
    return [status, wordCorrect ? status : 'Pending'];
  }

  global.OfflineRaddleRules = {
    playableWordIndices: playableWordIndices,
    refuseWord: refuseWord,
    advance: advance,
    progressStatus: progressStatus,
  };
})(typeof window !== 'undefined' ? window : globalThis);
