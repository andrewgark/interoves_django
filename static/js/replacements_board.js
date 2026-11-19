/*
 * Замены (replacements_lines): slot editor, word chips and shared drafts.
 * Spec: docs/replacements-redesign.md. The pure helpers at the top are also
 * exported for Node tests (static/js/replacements_board.test.js).
 */
(function (global) {
  'use strict';

  var SCOPE_STORAGE_KEY = 'interoves_repl_scope';

  function normWord(s) {
    return String(s == null ? '' : s)
      .replace(/\u00a0/g, ' ')
      .trim()
      .replace(/\s+/g, ' ')
      .toLocaleUpperCase('ru');
  }

  function keysOf(slots) {
    return slots.map(function (s) { return s.key; });
  }

  function sameKeySet(a, b) {
    if (a.length !== b.length) return false;
    var seen = {};
    a.forEach(function (k) { seen[k] = true; });
    return b.every(function (k) { return seen[k]; });
  }

  function slotByKey(slots, key) {
    for (var i = 0; i < slots.length; i++) {
      if (slots[i].key === key) return slots[i];
    }
    return null;
  }

  function openSlotsOfWord(slots, wordKey) {
    return slots.filter(function (s) { return s.wordKey === wordKey && !s.correct; });
  }

  /**
   * Scope options of the slot editor (spec §5.1). `openValue` is the slot's
   * guess when the editor was opened. Options that would change exactly the
   * same slots as another one are dropped; `sameAlias` tells which option
   * stands in for a dropped "same value" option.
   */
  function scopeOptions(slots, key, openValue) {
    var slot = slotByKey(slots, key);
    var result = [];
    result.sameAlias = null;
    if (!slot || slot.correct) return result;
    var value = normWord(openValue === undefined ? slot.guess : openValue);
    var word = normWord(slot.encoded);
    var mates = openSlotsOfWord(slots, slot.wordKey);
    var same = keysOf(mates.filter(function (s) {
      return s.key === key || normWord(s.guess) === value;
    }));
    var all = keysOf(mates);
    result.push({ id: 'here', keys: [key], label: 'Только здесь' });
    if (sameKeySet(same, [key])) {
      result.sameAlias = 'here';
    } else if (sameKeySet(same, all)) {
      result.sameAlias = 'all';
    } else {
      result.push({
        id: 'same',
        keys: same,
        label: value
          ? 'Все ' + word + ' с «' + value + '» (' + same.length + ')'
          : 'Все пустые ' + word + ' (' + same.length + ')',
      });
    }
    if (!sameKeySet(all, [key])) {
      result.push({
        id: 'all',
        keys: all,
        label: 'Все нерешённые ' + word + ' (' + all.length + ')',
      });
    }
    return result;
  }

  /** Default scope: the remembered "here"/"same" choice; "all" is never remembered. */
  function defaultScope(options, remembered) {
    var ids = options.map(function (o) { return o.id; });
    var wanted = remembered === 'here' ? 'here' : 'same';
    if (wanted === 'same' && ids.indexOf('same') < 0) wanted = options.sameAlias || 'here';
    return ids.indexOf(wanted) >= 0 ? wanted : 'here';
  }

  /** Word list model (spec §7): sorted, deduplicated words with their guesses. */
  function chipModel(slots) {
    var words = {};
    slots.forEach(function (s) {
      var w = words[s.wordKey];
      if (!w) {
        w = words[s.wordKey] = {
          wordKey: s.wordKey, display: normWord(s.encoded), slots: [], guesses: {},
        };
      }
      w.slots.push(s);
      var value = normWord(s.correct ? s.answer : s.guess);
      if (!value) return;
      var g = w.guesses[value] || (w.guesses[value] = { value: value, correct: false, unchecked: false });
      if (s.correct) g.correct = true;
      else g.unchecked = true;
    });
    return Object.keys(words).map(function (k) {
      var w = words[k];
      return {
        wordKey: w.wordKey,
        display: w.display,
        allCorrect: w.slots.every(function (s) { return s.correct; }),
        guesses: Object.keys(w.guesses).map(function (v) { return w.guesses[v]; })
          .sort(function (a, b) { return a.value.localeCompare(b.value, 'ru'); }),
      };
    }).sort(function (a, b) { return a.display.localeCompare(b.display, 'ru'); });
  }

  /** Clicking a word: its empty slots, or else all its unsolved slots. */
  function chipWordTarget(slots, wordKey) {
    var mates = openSlotsOfWord(slots, wordKey);
    if (!mates.length) return null;
    var word = normWord(mates[0].encoded);
    var empty = mates.filter(function (s) { return !normWord(s.guess); });
    if (empty.length) {
      return { keys: keysOf(empty), label: 'Заполнит пустые ' + word + ' (' + empty.length + ')' };
    }
    return { keys: keysOf(mates), label: 'Заменит нерешённые ' + word + ' (' + mates.length + ')' };
  }

  /** Clicking a guess on a chip: the unsolved slots of the word holding it. */
  function chipGuessTarget(slots, wordKey, value) {
    var wanted = normWord(value);
    var hits = openSlotsOfWord(slots, wordKey).filter(function (s) {
      return wanted && normWord(s.guess) === wanted;
    });
    if (!hits.length) return null;
    var word = normWord(hits[0].encoded);
    return { keys: keysOf(hits), label: 'Заменит ' + word + ' с «' + wanted + '» (' + hits.length + ')' };
  }

  /** The guess shared by all `keys`, or '' when they differ. */
  function commonGuess(slots, keys) {
    var value = null;
    for (var i = 0; i < keys.length; i++) {
      var s = slotByKey(slots, keys[i]);
      var g = s ? String(s.guess || '').trim() : '';
      if (value === null) value = g;
      else if (normWord(value) !== normWord(g)) return '';
    }
    return value || '';
  }

  /** Changes map for `keys`; correct slots are skipped and '' clears a slot. */
  function buildChanges(slots, keys, value) {
    var v = String(value == null ? '' : value).trim();
    var changes = {};
    keys.forEach(function (k) {
      var s = slotByKey(slots, k);
      if (!s || s.correct) return;
      changes[k] = v || null;
    });
    return changes;
  }

  function applyChanges(guesses, changes) {
    var out = {};
    Object.keys(guesses || {}).forEach(function (k) { out[k] = guesses[k]; });
    Object.keys(changes || {}).forEach(function (k) {
      if (changes[k]) out[k] = changes[k];
      else delete out[k];
    });
    return out;
  }

  var pure = {
    normWord: normWord,
    scopeOptions: scopeOptions,
    defaultScope: defaultScope,
    chipModel: chipModel,
    chipWordTarget: chipWordTarget,
    chipGuessTarget: chipGuessTarget,
    commonGuess: commonGuess,
    buildChanges: buildChanges,
    applyChanges: applyChanges,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = pure;
  if (!global || !global.document) return;

  var document = global.document;
  var boards = Object.create(null);
  var settings = { post: null, parseLinePaste: null };
  var editor = null;
  var editorEl = null;

  function readGuesses(root) {
    try {
      var parsed = JSON.parse(root.getAttribute('data-repl-guesses') || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function boardSlots(b) {
    var out = [];
    b.root.querySelectorAll('[data-repl-slot-key]').forEach(function (el) {
      var key = el.getAttribute('data-repl-slot-key');
      var correct = el.classList.contains('is-correct');
      var encoded = el.getAttribute('data-encoded') || '';
      out.push({
        key: key,
        el: el,
        encoded: encoded,
        wordKey: normWord(encoded),
        correct: correct,
        answer: el.getAttribute('data-answer') || '',
        guess: correct ? '' : (b.guesses[key] || ''),
      });
    });
    return out;
  }

  function span(cls, text) {
    var el = document.createElement('span');
    el.className = cls;
    el.textContent = text;
    return el;
  }

  function renderSlot(slotEl, guess) {
    if (slotEl.classList.contains('is-correct')) return;
    var encoded = slotEl.getAttribute('data-encoded') || '';
    slotEl.textContent = '';
    if (guess) {
      slotEl.appendChild(span('unsubmitted-replacement', guess));
      slotEl.appendChild(span('encoded-replacement-sub', encoded));
      slotEl.setAttribute('aria-label', normWord(guess) + ' вместо ' + normWord(encoded) + ', изменить');
    } else {
      slotEl.appendChild(span('encoded-replacement', encoded));
      slotEl.setAttribute('aria-label', normWord(encoded) + ', ввести замену');
    }
    slotEl.classList.toggle('is-entered', !!guess);
    var input = slotEl.nextElementSibling;
    if (input && input.name === 'answers[]') input.value = guess || '';
  }

  function renderChips(b, slots) {
    var box = b.root.querySelector('[data-repl-words]');
    if (!box) return;
    box.textContent = '';
    chipModel(slots).forEach(function (w) {
      var chip = document.createElement('div');
      chip.className = 'repl-chip' + (w.allCorrect ? ' is-done' : '');
      chip.setAttribute('data-word-key', w.wordKey);
      var wordBtn = document.createElement('button');
      wordBtn.type = 'button';
      wordBtn.className = 'repl-chip__part repl-chip__word';
      wordBtn.setAttribute('data-repl-chip-word', '');
      wordBtn.appendChild(span('encoded-replacement', w.display));
      wordBtn.disabled = w.allCorrect;
      wordBtn.setAttribute('aria-label', w.allCorrect
        ? w.display + ': все решены'
        : w.display + ': заполнить');
      chip.appendChild(wordBtn);
      w.guesses.forEach(function (g) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'repl-chip__part repl-chip__guess';
        btn.setAttribute('data-repl-chip-guess', g.value);
        btn.appendChild(span(g.correct ? 'correct-replacement' : 'unsubmitted-replacement', g.value));
        if (g.correct && g.unchecked) {
          var dot = span('repl-chip__dot', '');
          dot.setAttribute('aria-hidden', 'true');
          btn.appendChild(dot);
        }
        btn.disabled = !g.unchecked;
        btn.setAttribute('aria-label', g.value + (g.correct ? ' (верно' + (g.unchecked ? ', есть непроверенные)' : ')') : ' (не проверено)'));
        chip.appendChild(btn);
      });
      box.appendChild(chip);
    });
  }

  function renderBoard(b) {
    b.root.querySelectorAll('button[data-repl-slot]').forEach(function (el) {
      renderSlot(el, b.guesses[el.getAttribute('data-repl-slot-key')] || '');
    });
    renderChips(b, boardSlots(b));
    if (editor && editor.taskId === b.taskId) refreshEditor();
  }

  function setHighlight(b, wordKey) {
    b.root.querySelectorAll('[data-repl-slot-key]').forEach(function (el) {
      el.classList.toggle(
        'is-highlighted',
        !!wordKey && normWord(el.getAttribute('data-encoded')) === wordKey,
      );
    });
  }

  /** Save errors share the fill message line; clearing never hides a fill message. */
  function showMessage(b, text) {
    if (!text && !b.saveError) return;
    b.saveError = !!text;
    var msg = b.root.querySelector('[data-repl-fill-msg]');
    if (!msg) return;
    msg.textContent = text || '';
    msg.hidden = !text;
    msg.classList.toggle('is-error', !!text);
  }

  // ─── Shared drafts ───

  function overlayPending(b, guesses) {
    var out = {};
    Object.keys(guesses || {}).forEach(function (k) { out[k] = guesses[k]; });
    return applyChanges(out, b.pending);
  }

  function applyPayload(b, payload, fromOwnResponse) {
    if (!payload || typeof payload !== 'object') return;
    var revision = Number(payload.revision);
    if (Number.isFinite(revision)) {
      if (revision < b.revision || (revision === b.revision && !fromOwnResponse)) return;
      b.revision = revision;
    }
    var guesses = payload.guesses && typeof payload.guesses === 'object' ? payload.guesses : {};
    b.guesses = overlayPending(b, guesses);
    renderBoard(b);
  }

  var RETRY_MIN_MS = 1000;
  var RETRY_MAX_MS = 30000;

  function scheduleFlush(b, delay) {
    if (b.flushTimer) clearTimeout(b.flushTimer);
    var taskId = b.taskId;
    b.flushTimer = setTimeout(function () { flush(taskId); }, delay || 0);
  }

  /** Put back changes of a failed save unless the player changed those slots since. */
  function requeue(b, changes) {
    Object.keys(changes).forEach(function (k) {
      if (!(k in b.outbox) && b.pending[k] === changes[k]) b.outbox[k] = changes[k];
    });
  }

  function flush(taskId) {
    var b = boards[taskId];
    if (!b) return;
    b.flushTimer = null;
    var changes = b.outbox;
    b.outbox = {};
    if (!Object.keys(changes).length || b.refused || typeof settings.post !== 'function') return;
    Promise.resolve(settings.post(taskId, changes)).then(function (data) {
      var cur = boards[taskId];
      if (!cur) return;
      if (!data) {
        // Network error or an unreadable answer: keep the words and retry,
        // so a teammate's update cannot silently replace them.
        requeue(cur, changes);
        cur.retryDelay = Math.min(RETRY_MAX_MS, cur.retryDelay ? cur.retryDelay * 2 : RETRY_MIN_MS);
        showMessage(cur, 'Не удалось сохранить введённые слова. Пробуем ещё раз…');
        if (Object.keys(cur.outbox).length) scheduleFlush(cur, cur.retryDelay);
        return;
      }
      Object.keys(changes).forEach(function (k) {
        if (cur.pending[k] === changes[k]) delete cur.pending[k];
      });
      if (data.status === 'ok') {
        cur.retryDelay = 0;
        showMessage(cur, '');
        applyPayload(cur, data.replacements_ui && data.replacements_ui[String(taskId)], true);
        return;
      }
      if (data.error === 'replay_required' || data.status === 'replay_required') {
        cur.refused = true;
        cur.outbox = {};
        showMessage(cur, 'Игра закончилась: введённые слова больше не сохраняются для команды.');
      } else if (!data.context_mismatch) {
        showMessage(cur, 'Не удалось сохранить введённые слова.');
      }
    });
  }

  function commit(b, changes) {
    if (!Object.keys(changes).length) return 0;
    b.guesses = applyChanges(b.guesses, changes);
    Object.keys(changes).forEach(function (k) {
      b.pending[k] = changes[k];
      if (!b.refused) b.outbox[k] = changes[k];
    });
    renderBoard(b);
    // A new edit is sent right away, also while a failed save waits for its retry.
    scheduleFlush(b, 0);
    return Object.keys(changes).length;
  }

  // ─── Editor ───

  function rememberedScope() {
    try { return global.localStorage.getItem(SCOPE_STORAGE_KEY) || ''; } catch (e) { return ''; }
  }

  function rememberScope(id) {
    if (id !== 'here' && id !== 'same') return;
    try { global.localStorage.setItem(SCOPE_STORAGE_KEY, id); } catch (e) {}
  }

  function ensureEditor() {
    if (editorEl) return editorEl;
    editorEl = document.createElement('div');
    editorEl.className = 'repl-editor';
    editorEl.hidden = true;
    editorEl.setAttribute('role', 'dialog');
    editorEl.setAttribute('aria-label', 'Замена слова');
    editorEl.innerHTML =
      '<div class="repl-editor__head"><span>Замена для</span>' +
      '<span class="repl-editor__word encoded-replacement" data-repl-editor-word></span></div>' +
      '<form class="repl-editor__form" novalidate>' +
      '<input type="text" class="repl-editor__input" data-repl-editor-input autocomplete="off" ' +
      'autocapitalize="characters" spellcheck="false" aria-label="Замена">' +
      '<p class="repl-editor__remote" data-repl-editor-remote hidden></p>' +
      '<fieldset class="repl-editor__scopes" data-repl-editor-scopes hidden>' +
      '<legend class="repl-sr-only">Где заменить</legend></fieldset>' +
      '<p class="repl-editor__target" data-repl-editor-target hidden></p>' +
      '<div class="repl-editor__actions">' +
      '<button type="submit" class="new-btn">OK</button>' +
      '<button type="button" class="new-btn new-btn--text" data-repl-editor-clear>Стереть</button>' +
      '</div></form>';
    document.body.appendChild(editorEl);
    editorEl.querySelector('form').addEventListener('submit', function (e) {
      e.preventDefault();
      saveEditor(editorEl.querySelector('[data-repl-editor-input]').value);
    });
    editorEl.querySelector('[data-repl-editor-input]').addEventListener('paste', onEditorPaste);
    editorEl.querySelector('[data-repl-editor-clear]').addEventListener('click', function () {
      saveEditor('');
    });
    editorEl.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeEditor(true);
      }
    });
    document.addEventListener('mousedown', onOutsidePointer, true);
    document.addEventListener('touchstart', onOutsidePointer, { capture: true, passive: true });
    global.addEventListener('resize', function () { if (editor) positionEditor(); });
    return editorEl;
  }

  /**
   * Pasting a whole line (tabs, quotes or the full restored line) into the
   * slot editor fills every slot of that line, like the old per-word inputs.
   */
  function onEditorPaste(e) {
    if (!editor || editor.mode !== 'slot' || typeof settings.parseLinePaste !== 'function') return;
    var clip = e.clipboardData && e.clipboardData.getData('text/plain');
    if (!clip || !String(clip).trim()) return;
    var anchor = findAnchor();
    var row = anchor && anchor.closest ? anchor.closest('tr[data-line-index]') : null;
    if (!row) return;
    var values = settings.parseLinePaste(row, clip);
    if (!values || values.length < 2) return;
    var lineIndex = parseInt(row.getAttribute('data-line-index'), 10);
    if (isNaN(lineIndex)) return;
    e.preventDefault();
    var taskId = editor.taskId;
    closeEditor(true);
    setLineValues(taskId, lineIndex, values);
  }

  function onOutsidePointer(e) {
    if (!editor || !editorEl || editorEl.hidden) return;
    var t = e.target;
    if (editorEl.contains(t)) return;
    if (t && t.closest && t.closest('button[data-repl-slot], .repl-chip__part')) return;
    closeEditor(false);
  }

  function findAnchor() {
    if (!editor) return null;
    var b = boards[editor.taskId];
    if (!b) return null;
    if (editor.mode === 'slot') {
      var els = b.root.querySelectorAll('[data-repl-slot-key]');
      for (var i = 0; i < els.length; i++) {
        if (els[i].getAttribute('data-repl-slot-key') === editor.key) return els[i];
      }
      return null;
    }
    var chips = b.root.querySelectorAll('.repl-chip');
    for (var j = 0; j < chips.length; j++) {
      if (chips[j].getAttribute('data-word-key') === editor.wordKey) return chips[j];
    }
    return null;
  }

  function isNarrow() {
    return !!(global.matchMedia && global.matchMedia('(max-width: 600px)').matches);
  }

  function positionEditor() {
    var anchor = findAnchor();
    if (!anchor || !editorEl) return;
    if (isNarrow()) {
      editorEl.classList.add('is-sheet');
      editorEl.style.top = '';
      editorEl.style.left = '';
      return;
    }
    editorEl.classList.remove('is-sheet');
    var rect = anchor.getBoundingClientRect();
    var scrollX = global.pageXOffset || 0;
    var scrollY = global.pageYOffset || 0;
    var width = editorEl.offsetWidth;
    var viewport = document.documentElement.clientWidth;
    var left = Math.max(scrollX + 8, Math.min(rect.left + scrollX, scrollX + viewport - width - 8));
    var height = editorEl.offsetHeight;
    var viewportHeight = document.documentElement.clientHeight;
    var fitsBelow = rect.bottom + 6 + height <= viewportHeight;
    var top = !fitsBelow && rect.top - 6 - height >= 0
      ? rect.top - 6 - height
      : rect.bottom + 6;
    editorEl.style.left = left + 'px';
    editorEl.style.top = (top + scrollY) + 'px';
  }

  function markEditing(on) {
    document.querySelectorAll('.repl-slot.is-editing').forEach(function (el) {
      el.classList.remove('is-editing');
    });
    if (on && editor && editor.mode === 'slot') {
      var anchor = findAnchor();
      if (anchor) anchor.classList.add('is-editing');
    }
  }

  function renderScopes(b) {
    var box = editorEl.querySelector('[data-repl-editor-scopes]');
    var checked = box.querySelector('input:checked');
    var current = checked ? checked.value : null;
    box.querySelectorAll('label').forEach(function (el) { el.remove(); });
    var options = scopeOptions(boardSlots(b), editor.key, editor.openValue);
    editor.scopeOptions = options;
    if (options.length <= 1) {
      box.hidden = true;
      return;
    }
    var ids = options.map(function (o) { return o.id; });
    var selected = current && ids.indexOf(current) >= 0 ? current : defaultScope(options, rememberedScope());
    options.forEach(function (o) {
      var label = document.createElement('label');
      label.className = 'repl-editor__scope';
      var radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'repl-editor-scope';
      radio.value = o.id;
      radio.checked = o.id === selected;
      label.appendChild(radio);
      label.appendChild(span('', o.label));
      box.appendChild(label);
    });
    box.hidden = false;
  }

  function chipTarget(slots) {
    return editor.guessValue === null
      ? chipWordTarget(slots, editor.wordKey)
      : chipGuessTarget(slots, editor.wordKey, editor.guessValue);
  }

  function refreshEditor() {
    if (!editor || !editorEl) return;
    var b = boards[editor.taskId];
    var anchor = findAnchor();
    if (!b || !anchor || anchor.classList.contains('is-correct')) {
      closeEditor(false);
      return;
    }
    var remote = editorEl.querySelector('[data-repl-editor-remote]');
    if (editor.mode === 'slot') {
      var now = b.guesses[editor.key] || '';
      if (normWord(now) !== normWord(editor.openValue)) {
        remote.textContent = 'Сейчас в этом месте: ' + (normWord(now) || 'пусто');
        remote.hidden = false;
      } else {
        remote.hidden = true;
      }
      renderScopes(b);
    } else {
      var target = chipTarget(boardSlots(b));
      if (!target) {
        closeEditor(false);
        return;
      }
      var targetEl = editorEl.querySelector('[data-repl-editor-target]');
      targetEl.textContent = target.label;
      remote.hidden = true;
    }
    markEditing(true);
    positionEditor();
  }

  function showEditor(b, word, value) {
    ensureEditor();
    editorEl.querySelector('[data-repl-editor-word]').textContent = normWord(word);
    var input = editorEl.querySelector('[data-repl-editor-input]');
    input.value = value || '';
    editorEl.querySelector('[data-repl-editor-remote]').hidden = true;
    var scopes = editorEl.querySelector('[data-repl-editor-scopes]');
    scopes.querySelectorAll('label').forEach(function (el) { el.remove(); });
    scopes.hidden = true;
    var targetEl = editorEl.querySelector('[data-repl-editor-target]');
    targetEl.hidden = true;
    editorEl.hidden = false;
    refreshEditor();
    if (editor && editor.mode === 'chip') targetEl.hidden = false;
    try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
    input.select();
  }

  function openSlotEditor(b, slotEl) {
    var key = slotEl.getAttribute('data-repl-slot-key');
    var guess = b.guesses[key] || '';
    editor = { taskId: b.taskId, mode: 'slot', key: key, openValue: guess };
    showEditor(b, slotEl.getAttribute('data-encoded') || '', guess);
  }

  function openChipEditor(b, wordKey, guessValue) {
    var slots = boardSlots(b);
    editor = { taskId: b.taskId, mode: 'chip', wordKey: wordKey, guessValue: guessValue };
    var target = chipTarget(slots);
    if (!target) {
      editor = null;
      return;
    }
    showEditor(b, wordKey, commonGuess(slots, target.keys));
  }

  function closeEditor(returnFocus) {
    if (!editor) return;
    var anchor = findAnchor();
    markEditing(false);
    editor = null;
    if (editorEl) editorEl.hidden = true;
    if (returnFocus && anchor) {
      var focusable = anchor.matches('button') ? anchor : anchor.querySelector('button:not(:disabled)');
      if (focusable) focusable.focus();
    }
  }

  function saveEditor(value) {
    if (!editor) return;
    var b = boards[editor.taskId];
    if (!b) {
      closeEditor(false);
      return;
    }
    var slots = boardSlots(b);
    var keys;
    if (editor.mode === 'slot') {
      var options = scopeOptions(slots, editor.key, editor.openValue);
      var checked = editorEl.querySelector('[data-repl-editor-scopes] input:checked');
      var id = checked ? checked.value : 'here';
      var chosen = options.filter(function (o) { return o.id === id; })[0];
      keys = chosen ? chosen.keys : [editor.key];
      if (checked) rememberScope(id);
    } else {
      var target = chipTarget(slots);
      keys = target ? target.keys : [];
    }
    closeEditor(true);
    commit(b, buildChanges(slots, keys, value));
  }

  // ─── Public API ───

  function bindRoot(b) {
    b.root.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var cur = boards[b.taskId];
      if (!cur || cur.root !== b.root) return;
      var slotEl = t.closest('button[data-repl-slot]');
      if (slotEl && b.root.contains(slotEl)) {
        openSlotEditor(cur, slotEl);
        return;
      }
      var part = t.closest('.repl-chip__part');
      if (!part || part.disabled || !b.root.contains(part)) return;
      var chip = part.closest('.repl-chip');
      var wordKey = chip && chip.getAttribute('data-word-key');
      if (!wordKey) return;
      if (part.hasAttribute('data-repl-chip-word')) openChipEditor(cur, wordKey, null);
      else openChipEditor(cur, wordKey, part.getAttribute('data-repl-chip-guess'));
    });
    function chipHover(e) {
      var chip = e.target && e.target.closest ? e.target.closest('.repl-chip') : null;
      setHighlight(b, chip ? chip.getAttribute('data-word-key') : null);
    }
    var box = b.root.querySelector('[data-repl-words]');
    if (box) {
      box.addEventListener('mouseover', chipHover);
      box.addEventListener('focusin', chipHover);
      box.addEventListener('mouseleave', function () { setHighlight(b, null); });
      box.addEventListener('focusout', function () { setHighlight(b, null); });
    }
  }

  /** Bind every `.repl-board` under `root`; safe to call after a card redraw. */
  function init(root, opts) {
    if (opts && typeof opts.post === 'function') settings.post = opts.post;
    if (opts && typeof opts.parseLinePaste === 'function') settings.parseLinePaste = opts.parseLinePaste;
    var scope = root || document;
    var roots = [];
    if (scope.matches && scope.matches('.repl-board[data-task-id]')) roots.push(scope);
    scope.querySelectorAll('.repl-board[data-task-id]').forEach(function (el) { roots.push(el); });
    roots.forEach(function (el) {
      if (el.getAttribute('data-repl-board-bound') === '1') return;
      el.setAttribute('data-repl-board-bound', '1');
      var taskId = el.getAttribute('data-task-id');
      var revision = Number(el.getAttribute('data-repl-ui-revision')) || 0;
      var prev = boards[taskId];
      var b = {
        taskId: taskId,
        root: el,
        guesses: readGuesses(el),
        revision: revision,
        pending: prev ? prev.pending : {},
        outbox: prev ? prev.outbox : {},
        flushTimer: prev ? prev.flushTimer : null,
        retryDelay: prev ? prev.retryDelay : 0,
        saveError: false,
        refused: prev ? prev.refused : false,
      };
      if (prev && prev.revision > revision) {
        b.guesses = prev.guesses;
        b.revision = prev.revision;
      }
      b.guesses = overlayPending(b, b.guesses);
      boards[taskId] = b;
      bindRoot(b);
      renderBoard(b);
    });
  }

  /** Apply a `{task_id: {guesses, revision}}` push from a teammate. */
  function applyRemote(map) {
    if (!map || typeof map !== 'object') return;
    Object.keys(map).forEach(function (taskId) {
      var b = boards[taskId];
      if (b) applyPayload(b, map[taskId], false);
    });
  }

  /** Write parsed line values (one per slot, in order) as one shared change. */
  function setLineValues(taskId, lineIndex, values) {
    var b = boards[taskId];
    if (!b) return 0;
    var slots = boardSlots(b);
    var changes = {};
    (values || []).forEach(function (v, i) {
      var s = slotByKey(slots, lineIndex + ':' + i);
      if (!s || s.correct) return;
      var value = String(v == null ? '' : v).trim();
      changes[s.key] = value || null;
    });
    return commit(b, changes);
  }

  /** The restored line as text: fixed text, answers of solved slots and guesses. */
  function lineText(row) {
    var container = row && row.querySelector('[data-repl-words-wrap]');
    if (!container) return '';
    var parts = [];
    Array.prototype.forEach.call(container.children, function (el) {
      if (el.classList.contains('new-replacements-static')) {
        parts.push(el.textContent || '');
      } else if (el.classList.contains('repl-slot')) {
        if (el.classList.contains('is-correct')) {
          parts.push(el.getAttribute('data-answer') || '');
        } else {
          var input = el.nextElementSibling;
          parts.push(input && input.name === 'answers[]' ? input.value || '' : '');
        }
      }
    });
    return parts.join('');
  }

  function editableSlotsFilled(row) {
    var inputs = row ? row.querySelectorAll('input[name="answers[]"][data-repl-editable]') : [];
    if (!inputs.length) return false;
    return Array.prototype.every.call(inputs, function (inp) { return !!String(inp.value || '').trim(); });
  }

  global.ReplacementsBoard = Object.assign({
    init: init,
    applyRemote: applyRemote,
    setLineValues: setLineValues,
    lineText: lineText,
    editableSlotsFilled: editableSlotsFilled,
    closeEditor: closeEditor,
  }, pure);
})(typeof window !== 'undefined' ? window : globalThis);
