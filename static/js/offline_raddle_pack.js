/**
 * Лесенка без сети: ключи слотов карточки — browser twin of
 * games/puzzles/raddle/offline_pack.slot_keys.
 *
 * The server pack holds one piece per slot key; the page computes the keys
 * of the state it reached and assembles the card from those pieces.
 * contracts/raddle_slot_keys.json is generated from the Python keys and
 * pins this file. Requires static/js/offline_raddle_rules.js.
 *
 * Local check: node static/js/offline_raddle_pack.test.js
 */
(function (global) {
  'use strict';

  function solvedSet(state) {
    var out = Object.create(null);
    (state.solved_indices || []).forEach(function (i) { out[i] = true; });
    return out;
  }

  function referenceWord(i, solved, n) {
    if (i > 0 && solved[i - 1]) return i - 1;
    if (i + 1 < n && solved[i + 1]) return i + 1;
    return null;
  }

  function referenceRole(i, solved, n) {
    if (i > 0 && solved[i - 1]) return 'prev';
    if (i + 1 < n && solved[i + 1]) return 'next';
    return '';
  }

  function bothNeighborsSolved(i, solved, n) {
    if (i === null || i <= 0 || i >= n - 1) return false;
    return !!(solved[i - 1] && solved[i + 1]);
  }

  function lastWordRole(i, focus, lastWord) {
    if (!lastWord || focus === null) return '';
    if (i === focus - 1) return 'before';
    if (i === focus) return 'focus';
    if (i === focus + 1) return 'after';
    return '';
  }

  function clueIndexForPlayableWord(i, solved, n) {
    if (i === null || i <= 0 || i >= n - 1) return null;
    if (solved[i - 1]) return i - 1;
    if (solved[i + 1]) return i;
    return null;
  }

  /**
   * Keys of every slot of the card in state: { rows, clues, last_word, complete }.
   * tiers maps a word index to its assist tier (fixed while offline).
   */
  function slotKeys(state, n, tiers) {
    var solved = solvedSet(state);
    var playable = global.OfflineRaddleRules.playableWordIndices(state, n)
      .filter(function (i) { return !solved[i]; });
    var isPlayable = Object.create(null);
    playable.forEach(function (i) { isPlayable[i] = true; });
    var focus = playable.length ? playable[0] : null;
    var lastWord = focus !== null && playable.length === 1 && bothNeighborsSolved(focus, solved, n);
    var focusRef = focus !== null ? referenceWord(focus, solved, n) : null;

    var rows = [];
    for (var i = 0; i < n; i++) {
      var status = 'draft';
      var refRole = '';
      var dual = false;
      if (solved[i]) {
        status = 'solved';
      } else if (isPlayable[i]) {
        status = 'playable';
        refRole = referenceRole(i, solved, n);
        dual = bothNeighborsSolved(i, solved, n);
      }
      rows.push([
        status,
        i === focus ? '1' : '0',
        i === focusRef ? '1' : '0',
        refRole,
        dual ? '1' : '0',
        lastWordRole(i, focus, lastWord),
      ].join('|'));
    }

    var revealed = Object.create(null);
    Object.keys(tiers || {}).forEach(function (key) {
      if (tiers[key] >= 1) {
        var clue = clueIndexForPlayableWord(Number(key), solved, n);
        if (clue !== null) revealed[clue] = true;
      }
    });
    var used = Object.create(null);
    (state.used_hints || []).forEach(function (h) { used[h] = true; });
    var clues = [];
    for (var h = 0; h < n - 1; h++) {
      clues.push(used[h] ? 'used' : [
        'unused',
        solved[h] ? 1 : 0,
        solved[h + 1] ? 1 : 0,
        revealed[h] ? 1 : 0,
      ].join('|'));
    }

    return {
      rows: rows,
      clues: clues,
      last_word: lastWord ? focus : null,
      complete: Object.keys(solved).length >= n,
    };
  }

  function insertItems(sectionHtml, items) {
    var at = sectionHtml.lastIndexOf('</ul>');
    return sectionHtml.slice(0, at) + items.join('') + sectionHtml.slice(at);
  }

  function fragment(doc, html) {
    var t = doc.createElement('template');
    t.innerHTML = String(html).trim();
    return t.content;
  }

  function piece(pieces, key) {
    if (!pieces || !Object.prototype.hasOwnProperty.call(pieces, key)) throw new Error('no piece for ' + key);
    return pieces[key];
  }

  /**
   * HTML of the whole task card (#new-task-<id>) in state, assembled from the
   * pack's pieces on top of the card the pack was built from. Null when the
   * pack lacks a piece: the page then waits for the server instead.
   */
  function assembleCard(pack, state, doc) {
    doc = doc || global.document;
    var n = pack.n_words;
    var keys = slotKeys(state, n, pack.tiers || {});
    try {
      var card = fragment(doc, pack.card_html).firstElementChild;
      var root = card && card.querySelector('.new-raddle-task');
      if (!root) return null;

      var rows = [];
      for (var i = 0; i < n; i++) rows.push(piece(pack.rows[i], keys.rows[i]));
      root.querySelector('.new-raddle-table tbody').innerHTML = rows.join('');

      var sections = [];
      if (keys.last_word !== null) {
        sections.push(piece(pack.last_word, String(keys.last_word)));
      } else {
        var unused = pack.unused_order
          .filter(function (h) { return keys.clues[h] !== 'used'; })
          .map(function (h) { return piece(pack.clues[h], keys.clues[h]); });
        if (unused.length) sections.push(insertItems(pack.sections.unused, unused));
      }
      var used = [];
      keys.clues.forEach(function (key, h) {
        if (key === 'used') used.push(piece(pack.clues[h], key));
      });
      if (used.length) sections.push(insertItems(pack.sections.used, used));
      root.querySelector('aside.new-raddle-clues').innerHTML = sections.join('');

      var oldResult = root.querySelector('[data-raddle-result]');
      if (oldResult) oldResult.parentNode.removeChild(oldResult);
      if (keys.complete && pack.result) {
        var layout = root.querySelector('.new-daily-results-layout');
        var result = fragment(doc, pack.result);
        if (layout) {
          layout.insertBefore(result, layout.firstChild);
        } else {
          var ladderLayout = root.querySelector('.new-raddle-layout');
          ladderLayout.parentNode.insertBefore(result, ladderLayout.nextSibling);
        }
      }

      var focus = null;
      keys.rows.forEach(function (key, idx) {
        if (key.split('|')[1] === '1') focus = idx;
      });
      var focusRow = focus === null ? null
        : root.querySelector('.new-raddle-row[data-word-index="' + focus + '"]');
      root.setAttribute('data-default-focus', focus === null ? '' : String(focus));
      root.setAttribute('data-default-ref-word', focusRow ? (focusRow.getAttribute('data-ref-word') || '') : '');
      root.setAttribute('data-offline-assembled', '1');
      card.setAttribute('data-solved', keys.complete ? '1' : '0');
      return card.outerHTML;
    } catch (e) {
      return null;
    }
  }

  global.OfflineRaddlePack = {
    slotKeys: slotKeys,
    assembleCard: assembleCard,
  };
})(typeof window !== 'undefined' ? window : globalThis);
