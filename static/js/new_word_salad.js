/**
 * Word salad grid selection.
 * Local check: node static/js/new_word_salad.test.js
 */
(function (global) {
  'use strict';

  var activeDragRoot = null;
  var lastSaladRoot = null;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  function cellsAreAdjacent(left, right) {
    var rowA = Math.floor(left / 4);
    var colA = left % 4;
    var rowB = Math.floor(right / 4);
    var colB = right % 4;
    return Math.max(Math.abs(rowA - rowB), Math.abs(colA - colB)) <= 1;
  }

  function neighborPairs(activeIndices) {
    var indices = [];
    var seen = {};
    (activeIndices || []).forEach(function (raw) {
      var index = Number(raw);
      if (!isFinite(index) || index < 0 || index > 15 || seen[index]) return;
      seen[index] = true;
      indices.push(index);
    });
    indices.sort(function (a, b) { return a - b; });
    var pairs = [];
    for (var i = 0; i < indices.length; i += 1) {
      for (var j = i + 1; j < indices.length; j += 1) {
        if (cellsAreAdjacent(indices[i], indices[j])) pairs.push([indices[i], indices[j]]);
      }
    }
    return pairs;
  }

  function cellIsSelectable(isActive, index) {
    if (typeof isActive === 'function') return !!isActive(index);
    if (isActive && typeof isActive === 'object') return !!isActive[index];
    return true;
  }

  // Repeat events on the current cell must not toggle it off: pointermove/enter
  // fire continuously while the pointer is held on the first letter.
  function nextWordSaladPath(path, index, isActive) {
    path = path || [];
    if (index < 0) return path;
    if (!cellIsSelectable(isActive, index)) return path;
    var existing = path.indexOf(index);
    if (existing >= 0) {
      if (existing === path.length - 1) return path;
      return path.slice(0, existing + 1);
    }
    if (path.length && !cellsAreAdjacent(path[path.length - 1], index)) return path;
    return path.concat([index]);
  }

  function startWordSaladPress(path, index, isActive) {
    path = path || [];
    if (!cellIsSelectable(isActive, index)) {
      return { path: path, clearOnRelease: false };
    }
    if (path.length === 1 && path[0] === index) {
      return { path: path, clearOnRelease: true };
    }
    return { path: nextWordSaladPath(path, index, isActive), clearOnRelease: false };
  }

  function moveWordSaladPress(path, index, clearOnRelease, isActive) {
    var next = nextWordSaladPath(path, index, isActive);
    if (next === path) return { path: path, clearOnRelease: !!clearOnRelease };
    return { path: next, clearOnRelease: false };
  }

  function endWordSaladPress(path, clearOnRelease) {
    if (clearOnRelease && path && path.length === 1) return [];
    return path || [];
  }

  var EXTRA_MIN_LENGTH = 3;

  function rememberExtraWord(words, word, answers) {
    word = normalizeWord(word);
    if (!word || word.length < EXTRA_MIN_LENGTH) {
      return { words: words ? words.slice() : [], latest: '', changed: false };
    }
    if (answers && answers[word]) {
      return { words: words ? words.slice() : [], latest: '', changed: false };
    }
    words = (words || []).filter(function (item) { return item !== word; });
    words.push(word);
    return { words: words, latest: word, changed: true };
  }

  function isTypingTarget(target) {
    if (!target) return false;
    var tag = (target.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
    if (target.isContentEditable) return true;
    return !!(target.closest && target.closest('input, textarea, select, [contenteditable="true"]'));
  }

  function hasOpenOverlay(doc) {
    if (!doc || !doc.querySelector) return false;
    return !!(
      doc.querySelector('.new-rules-modal.is-open') ||
      doc.querySelector('[role="dialog"].is-open') ||
      doc.querySelector('[aria-modal="true"].is-open')
    );
  }

  function shouldHandleWordSaladEscape(event, doc) {
    if (!event || (event.key !== 'Escape' && event.key !== 'Esc')) return false;
    if (event.defaultPrevented) return false;
    if (event.altKey || event.ctrlKey || event.metaKey) return false;
    if (isTypingTarget(event.target)) return false;
    var owner = doc || (event.target && event.target.ownerDocument) || null;
    if (hasOpenOverlay(owner)) return false;
    return true;
  }

  function normalizeWord(value) {
    return String(value || '')
      .toUpperCase()
      .replace(/Ё/g, 'Е')
      .replace(/[^А-ЯA-Z]/g, '');
  }

  function formSubmitUrl(form) {
    // <input name="action"> shadows HTMLFormElement.action with the input node.
    if (!form) return '';
    var attr = form.getAttribute('action');
    if (attr) return attr;
    return typeof form.action === 'string' ? form.action : '';
  }

  function renderMaskEl(el, text) {
    if (!el) return;
    el.textContent = '';
    Array.prototype.forEach.call(String(text || ''), function (ch) {
      var span = document.createElement('span');
      var blank = ch === '⬜';
      span.className = 'new-word-salad__glyph' + (blank ? ' is-blank' : '');
      span.textContent = ch;
      if (blank) span.setAttribute('aria-hidden', 'true');
      el.appendChild(span);
    });
  }

  var TOAST_HOLD_MS = 1600;
  var TOAST_FADE_MS = 280;

  function clearToastTimers(toast) {
    if (!toast) return;
    window.clearTimeout(toast._toastHoldTimer);
    window.clearTimeout(toast._toastHideTimer);
    toast._toastHoldTimer = 0;
    toast._toastHideTimer = 0;
  }

  function fadeToast(toast) {
    if (!toast || !toast.isConnected) return;
    clearToastTimers(toast);
    toast.classList.remove('is-in');
    toast.classList.add('is-out');
    toast._toastHideTimer = window.setTimeout(function () {
      toast.remove();
    }, TOAST_FADE_MS);
  }

  function feedbackForResult(result, word) {
    var duplicate = result === 'duplicate';
    var displayWord = String(word || '').trim().toUpperCase();
    return {
      word: displayWord,
      message: duplicate ? 'Уже было!' : 'Верно!',
      icon: duplicate ? 'ph-arrow-counter-clockwise' : 'ph-check-circle',
      duplicate: duplicate
    };
  }

  function showAnswerToast(root, word, result) {
    var feedback = feedbackForResult(result, word);
    var toast = document.querySelector('.new-word-salad__toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'new-word-salad__toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      toast.setAttribute('aria-atomic', 'true');
      document.body.appendChild(toast);
    }
    clearToastTimers(toast);
    toast.classList.remove('is-in', 'is-out', 'is-duplicate');
    toast.classList.toggle('is-duplicate', feedback.duplicate);
    toast.textContent = '';

    var mark = document.createElement('span');
    mark.className = 'new-word-salad__toast-mark';
    mark.setAttribute('aria-hidden', 'true');
    var icon = document.createElement('i');
    icon.className = 'ph ' + feedback.icon;
    mark.appendChild(icon);

    var copy = document.createElement('span');
    copy.className = 'new-word-salad__toast-copy';
    if (feedback.word) {
      var answer = document.createElement('span');
      answer.className = 'new-word-salad__toast-word';
      answer.textContent = feedback.word;
      copy.appendChild(answer);
    }
    var message = document.createElement('span');
    message.className = 'new-word-salad__toast-ok';
    message.textContent = feedback.message;
    copy.appendChild(message);

    toast.appendChild(mark);
    toast.appendChild(copy);
    toast.setAttribute('aria-label', (feedback.word ? feedback.word + '. ' : '') + feedback.message);
    toast.offsetWidth;
    toast.classList.add('is-in');
    toast._toastHoldTimer = window.setTimeout(function () {
      fadeToast(toast);
    }, TOAST_HOLD_MS);
  }

  function maskedAnswer(answer, revealCount) {
    var normalized = normalizeWord(answer);
    var letterIndex = 0;
    var normalizedIndex = 0;
    return Array.prototype.map.call(String(answer || '').toUpperCase(), function (character) {
      if (!/[А-ЯЁA-Z]/.test(character)) return character;
      var letter = normalized.charAt(normalizedIndex++);
      return letterIndex++ < revealCount ? letter : '⬜';
    }).join('');
  }

  function extrasStorageKey(root) {
    try {
      return 'interoves_word_salad_extras_v1:' + (root.getAttribute('data-task-id') || '');
    } catch (error) {
      return '';
    }
  }

  function previewStorageKey(root) {
    try {
      var url = new URL(window.location.href);
      var actor = ['actor', 'team', 'user', 'anon', 'anon_key', 'mode'].map(function (name) {
        return name + '=' + (url.searchParams.get(name) || '');
      }).join('&');
      return 'interoves_word_salad_preview_v2:' + url.pathname + ':' + actor + ':' +
        (root.getAttribute('data-task-id') || '');
    } catch (error) {
      return '';
    }
  }

  function initWordSalad(scope) {
    var container = scope && scope.querySelectorAll ? scope : document;
    var roots = [];
    if (container.matches && container.matches('[data-word-salad-root]')) roots.push(container);
    Array.prototype.push.apply(
      roots,
      Array.prototype.slice.call(container.querySelectorAll('[data-word-salad-root]'))
    );

    roots.forEach(function (root) {
      if (root.getAttribute('data-word-salad-bound') === '1') return;
      root.setAttribute('data-word-salad-bound', '1');

      var currentPath = [];
      var attemptedPaths = {};
      var busy = false;
      var lastRejectedPathKey = '';
      var clearSingleOnRelease = false;
      var cells = Array.prototype.slice.call(root.querySelectorAll('[data-word-salad-cell]'));
      var cellByIndex = {};
      var currentEl = root.querySelector('[data-word-salad-current]');
      var pathInput = root.querySelector('[data-word-salad-path]');
      var form = root.querySelector('.new-word-salad__form');
      var resetBtn = root.querySelector('[data-word-salad-reset]');
      var resetWrap = root.querySelector('[data-word-salad-reset-wrap]');
      var extrasEl = root.querySelector('[data-word-salad-extras]');
      var extrasListEl = root.querySelector('[data-word-salad-extras-list]');
      var extrasKey = extrasStorageKey(root);
      var extraWords = [];
      var latestExtra = '';
      var extrasAnimateWord = '';
      var gridEl = root.querySelector('[data-word-salad-grid]');
      var pathSvg = root.querySelector('[data-word-salad-path-svg]');
      var pathLine = root.querySelector('[data-word-salad-path-line]');
      var linksSvg = root.querySelector('[data-word-salad-links-svg]');
      var solvedEl = root.querySelector('[data-word-salad-solved]');
      var wordPoints = Number(root.getAttribute('data-word-points'));
      if (!isFinite(wordPoints) || wordPoints < 0) wordPoints = 0;
      var hintPenalty = Number(root.getAttribute('data-hint-penalty'));
      if (!isFinite(hintPenalty) || hintPenalty < 0) hintPenalty = 0;
      var isPreview = !!root.closest('.support-preview-readonly');
      var storageKey = isPreview ? previewStorageKey(root) : '';

      function cellIndex(cell) {
        var value = parseInt(cell && cell.getAttribute('data-index'), 10);
        return isNaN(value) ? -1 : value;
      }

      function wordIndex(wordRow) {
        var value = parseInt(wordRow && wordRow.getAttribute('data-word-index'), 10);
        return isNaN(value) ? -1 : value;
      }

      function wordLength(wordRow) {
        return parseInt(wordRow && wordRow.getAttribute('data-word-length'), 10) || 0;
      }

      function isActiveIndex(index) {
        var cell = cellByIndex[index];
        return !!(cell && cell.classList.contains('is-active'));
      }

      function selectedWord(path) {
        return (path || currentPath).map(function (index) {
          var cell = cellByIndex[index];
          if (!cell || !cell.classList.contains('is-active')) return '';
          return (cell.getAttribute('data-letter') || '').trim();
        }).join('');
      }

      function cellCenter(cell, gridRect) {
        var rect = cell.getBoundingClientRect();
        return [
          rect.left - gridRect.left + rect.width / 2,
          rect.top - gridRect.top + rect.height / 2
        ];
      }

      function renderNeighborLinks() {
        if (!gridEl || !linksSvg) return;
        var gridRect = gridEl.getBoundingClientRect();
        if (!gridRect.width || !gridRect.height) return;
        linksSvg.setAttribute('viewBox', '0 0 ' + gridRect.width + ' ' + gridRect.height);
        var active = cells.filter(function (cell) { return cell.classList.contains('is-active'); });
        var centers = {};
        active.forEach(function (cell) {
          centers[cellIndex(cell)] = cellCenter(cell, gridRect);
        });
        var pairs = neighborPairs(Object.keys(centers));
        while (linksSvg.firstChild) linksSvg.removeChild(linksSvg.firstChild);
        pairs.forEach(function (pair) {
          var from = centers[pair[0]];
          var to = centers[pair[1]];
          if (!from || !to) return;
          var line = document.createElementNS(SVG_NS, 'line');
          line.setAttribute('x1', String(from[0]));
          line.setAttribute('y1', String(from[1]));
          line.setAttribute('x2', String(to[0]));
          line.setAttribute('y2', String(to[1]));
          linksSvg.appendChild(line);
        });
      }

      function renderPath() {
        if (!gridEl || !pathSvg || !pathLine) return;
        var gridRect = gridEl.getBoundingClientRect();
        if (!gridRect.width || !gridRect.height) return;
        var points = currentPath.map(function (index) {
          var cell = cellByIndex[index];
          if (!cell || !cell.classList.contains('is-active')) return null;
          return cellCenter(cell, gridRect);
        }).filter(Boolean);
        pathSvg.setAttribute('viewBox', '0 0 ' + gridRect.width + ' ' + gridRect.height);
        var serialized = points.map(function (point) { return point[0] + ',' + point[1]; });
        if (serialized.length === 1) serialized.push(serialized[0]);
        pathLine.setAttribute('points', serialized.join(' '));
        var sample = cellByIndex[currentPath[0]] || cells[0];
        if (sample) {
          var cellRect = sample.getBoundingClientRect();
          var svgRect = pathSvg.getBoundingClientRect();
          var scale = svgRect.width ? (gridRect.width / svgRect.width) : 1;
          var stroke = Math.min(cellRect.width, cellRect.height) * 0.55 * scale;
          pathLine.style.strokeWidth = String(stroke);
        }
      }

      function renderGridOverlays() {
        renderNeighborLinks();
        renderPath();
      }

      function syncSolvedState() {
        if (!solvedEl) return;
        var complete = wordRows().length > 0 && wordRows('.new-word-salad__word:not(.is-solved)').length === 0;
        solvedEl.hidden = !complete;
      }

      function setCellActive(cell, active) {
        if (!cell) return;
        cell.classList.toggle('is-active', active);
        cell.disabled = !active;
        cell.classList.remove('is-selected');
        cell.setAttribute('aria-pressed', 'false');
        var letter = cell.getAttribute('data-letter') || '';
        var letterEl = cell.querySelector('[data-word-salad-letter]');
        if (letterEl) letterEl.textContent = active ? letter : '';
        cell.setAttribute(
          'aria-label',
          active ? 'Клетка ' + (cellIndex(cell) + 1) + ': ' + letter : 'Пустая клетка ' + (cellIndex(cell) + 1)
        );
        window.requestAnimationFrame(renderNeighborLinks);
      }

      function renderSelection() {
        cells.forEach(function (cell) {
          cell.classList.remove('is-selected');
          cell.setAttribute('aria-pressed', 'false');
        });
        currentPath.forEach(function (index) {
          var cell = cellByIndex[index];
          if (!cell || !cell.classList.contains('is-active')) return;
          cell.classList.add('is-selected');
          cell.setAttribute('aria-pressed', 'true');
        });
        if (currentEl) currentEl.textContent = selectedWord();
        if (pathInput) pathInput.value = JSON.stringify(currentPath);
        if (resetBtn) resetBtn.disabled = currentPath.length === 0;
        if (resetWrap) resetWrap.classList.toggle('is-idle', currentPath.length === 0);
        window.requestAnimationFrame(renderGridOverlays);
      }

      function clearSelection() {
        currentPath = [];
        attemptedPaths = {};
        lastRejectedPathKey = '';
        clearSingleOnRelease = false;
        renderSelection();
      }

      function applyPath(next, nextClearOnRelease) {
        var changed = next !== currentPath;
        clearSingleOnRelease = !!nextClearOnRelease;
        if (!changed) return;
        if (next.length < currentPath.length) attemptedPaths = {};
        currentPath = next;
        renderSelection();
        if (activeDragRoot === root) maybeCheck();
      }

      function appendCell(cell) {
        if (!cell.classList.contains('is-active')) return;
        var moved = moveWordSaladPress(
          currentPath,
          cellIndex(cell),
          clearSingleOnRelease,
          isActiveIndex
        );
        applyPath(moved.path, moved.clearOnRelease);
      }

      function finishPress() {
        var next = endWordSaladPress(currentPath, clearSingleOnRelease);
        clearSingleOnRelease = false;
        if (next !== currentPath) {
          currentPath = next;
          attemptedPaths = {};
          lastRejectedPathKey = '';
          renderSelection();
          return;
        }
        maybeCheck({ fromRelease: true });
      }

      function wordRows(selector) {
        return Array.prototype.slice.call(root.querySelectorAll(selector || '.new-word-salad__word'));
      }

      function unsolvedLengths() {
        return wordRows('.new-word-salad__word:not(.is-solved)[data-word-length]').map(wordLength)
          .filter(function (length) { return length > 0; });
      }

      function answerWordSet() {
        var answers = {};
        wordRows().forEach(function (wordRow) {
          var preview = normalizeWord(wordRow.getAttribute('data-preview-normalized') || '');
          if (preview) answers[preview] = true;
          if (!wordRow.classList.contains('is-solved')) return;
          var mask = wordRow.querySelector('.new-word-salad__mask');
          var solved = normalizeWord(mask ? mask.textContent : '');
          if (solved) answers[solved] = true;
        });
        return answers;
      }

      function saveExtraWords() {
        if (!extrasKey) return;
        try {
          localStorage.setItem(extrasKey, JSON.stringify({
            words: extraWords,
            latest: latestExtra
          }));
        } catch (error) {}
      }

      function renderExtraWords() {
        if (!extrasEl || !extrasListEl) return;
        extrasListEl.textContent = '';
        if (!extraWords.length) {
          extrasEl.hidden = true;
          return;
        }
        extrasEl.hidden = false;
        extraWords.forEach(function (word) {
          var item = document.createElement('li');
          item.className = 'new-word-salad__extra';
          item.textContent = word;
          if (word === latestExtra) {
            item.classList.add('is-latest');
            if (word === extrasAnimateWord) item.classList.add('is-fresh');
          }
          extrasListEl.appendChild(item);
        });
        extrasAnimateWord = '';
      }

      function restoreExtraWords() {
        if (!extrasKey) return;
        var state = null;
        try { state = JSON.parse(localStorage.getItem(extrasKey) || 'null'); } catch (error) {}
        if (!state || typeof state !== 'object') return;
        extraWords = Array.isArray(state.words)
          ? state.words.map(normalizeWord).filter(Boolean)
          : [];
        latestExtra = normalizeWord(state.latest || extraWords[extraWords.length - 1] || '');
        extraWords = extraWords.filter(function (word, index) {
          return word.length >= EXTRA_MIN_LENGTH && extraWords.indexOf(word) === index;
        });
        applyAnswerFilterToExtras();
        renderExtraWords();
      }

      function applyAnswerFilterToExtras(persist) {
        var answers = answerWordSet();
        var next = extraWords.filter(function (word) { return !answers[word]; });
        if (next.length === extraWords.length) return;
        extraWords = next;
        if (answers[latestExtra]) {
          latestExtra = extraWords[extraWords.length - 1] || '';
        }
        if (persist !== false) saveExtraWords();
      }

      function addExtraWord(word) {
        var remembered = rememberExtraWord(extraWords, word, answerWordSet());
        if (!remembered.changed) return;
        extraWords = remembered.words;
        latestExtra = remembered.latest;
        extrasAnimateWord = latestExtra;
        saveExtraWords();
        renderExtraWords();
      }

      function setChecking(checking) {
        root.classList.toggle('is-checking', !!checking);
        if (checking) root.setAttribute('aria-busy', 'true');
        else root.removeAttribute('aria-busy');
      }

      function finishWrong(pathKey, extraWord) {
        busy = false;
        setChecking(false);
        if (extraWord) addExtraWord(extraWord);
        lastRejectedPathKey = pathKey || currentPath.join(',');
        if (currentPath.join(',') !== lastRejectedPathKey) {
          renderSelection();
          maybeCheck();
          return;
        }
        renderSelection();
      }

      function renderPreviewHint(wordRow, count) {
        var answer = wordRow.getAttribute('data-preview-answer') || '';
        var length = wordLength(wordRow);
        count = Math.max(0, Math.min(length, parseInt(count, 10) || 0));
        wordRow.setAttribute('data-hint-count', String(count));
        wordRow.classList.toggle('is-hinted', count > 0);
        var mask = wordRow.querySelector('.new-word-salad__mask');
        if (mask) {
          renderMaskEl(
            mask,
            wordRow.classList.contains('is-solved') ? answer : maskedAnswer(answer, count)
          );
        }
        var hintForm = wordRow.querySelector('.new-word-salad__hint-form');
        if (wordRow.classList.contains('is-solved') || count >= length) {
          if (hintForm) hintForm.remove();
          return;
        }
        if (!hintForm) return;
        var number = count + 1;
        var numberInput = hintForm.querySelector('input[name="hint_number"]');
        var button = hintForm.querySelector('.new-word-salad__hint-btn');
        if (numberInput) numberInput.value = String(number);
        if (button) {
          var label = 'Узнать ' + number + ' букву';
          button.title = label;
          button.setAttribute('aria-label', label);
        }
      }

      function collectPreviewState() {
        var hintCounts = {};
        var solved = [];
        wordRows().forEach(function (wordRow) {
          var index = wordIndex(wordRow);
          var count = parseInt(wordRow.getAttribute('data-hint-count'), 10) || 0;
          if (index < 0) return;
          if (count > 0) hintCounts[index] = count;
          if (wordRow.classList.contains('is-solved')) solved.push(index);
        });
        return {
          solved_indices: solved,
          hint_counts: hintCounts,
          active: cells.filter(function (cell) { return cell.classList.contains('is-active'); }).map(cellIndex)
        };
      }

      function syncPreviewPoints() {
        if (!isPreview) return;
        var state = collectPreviewState();
        var hintTotal = Object.keys(state.hint_counts).reduce(function (total, index) {
          return total + (parseInt(state.hint_counts[index], 10) || 0);
        }, 0);
        var points = Math.max(0, state.solved_indices.length * wordPoints - hintTotal * hintPenalty);
        var card = root.closest('.new-taskcard');
        var value = card && card.querySelector('.new-proportions-compact-points-pill .new-proportions-compact-stat__nums');
        if (value) value.textContent = String(points).replace('.', ',');
      }

      function savePreviewState() {
        if (!storageKey) return;
        try { localStorage.setItem(storageKey, JSON.stringify(collectPreviewState())); } catch (error) {}
      }

      function restorePreviewState() {
        if (!storageKey) return;
        var state = null;
        try { state = JSON.parse(localStorage.getItem(storageKey) || 'null'); } catch (error) {}
        if (!state || typeof state !== 'object') return;
        var solved = Array.isArray(state.solved_indices) ? state.solved_indices.map(Number) : [];
        var active = Array.isArray(state.active) ? state.active.map(Number) : null;
        var hintCounts = state.hint_counts && typeof state.hint_counts === 'object' ? state.hint_counts : {};
        if (active) {
          cells.forEach(function (cell) {
            setCellActive(
              cell,
              cell.classList.contains('is-active') && active.indexOf(cellIndex(cell)) >= 0
            );
          });
        }
        wordRows().forEach(function (wordRow) {
          var index = wordIndex(wordRow);
          if (solved.indexOf(index) >= 0) wordRow.classList.add('is-solved');
          renderPreviewHint(wordRow, hintCounts[index] || 0);
        });
        syncSolvedState();
        syncPreviewPoints();
      }

      function pathExists(word, activeIndices) {
        var target = normalizeWord(word);
        var active = {};
        activeIndices.forEach(function (index) { active[index] = true; });

        function visit(index, position, used) {
          var cell = cellByIndex[index];
          if (!cell || !active[index] || used[index]) return false;
          if ((cell.getAttribute('data-letter') || '') !== target.charAt(position)) return false;
          if (position === target.length - 1) return true;
          var nextUsed = Object.assign({}, used);
          nextUsed[index] = true;
          for (var next = 0; next < 16; next += 1) {
            if (cellsAreAdjacent(index, next) && next !== index && visit(next, position + 1, nextUsed)) return true;
          }
          return false;
        }

        if (!target) return false;
        return activeIndices.some(function (index) { return visit(index, 0, {}); });
      }

      function prunePreviewGrid() {
        while (true) {
          var active = cells.filter(function (cell) { return cell.classList.contains('is-active'); }).map(cellIndex);
          var remaining = wordRows('.new-word-salad__word:not(.is-solved)').map(function (wordRow) {
            return wordRow.getAttribute('data-preview-normalized') || '';
          });
          var removable = active.find(function (candidate) {
            var candidateActive = active.filter(function (index) { return index !== candidate; });
            return remaining.every(function (word) { return pathExists(word, candidateActive); });
          });
          if (removable === undefined) return;
          setCellActive(cellByIndex[removable], false);
        }
      }

      function markPreviewSolved(wordRow) {
        activeDragRoot = null;
        var solvedWord = wordRow.getAttribute('data-preview-answer') || selectedWord();
        wordRow.classList.add('is-solved');
        renderPreviewHint(wordRow, parseInt(wordRow.getAttribute('data-hint-count'), 10) || 0);
        prunePreviewGrid();
        savePreviewState();
        syncSolvedState();
        syncPreviewPoints();
        applyAnswerFilterToExtras();
        renderExtraWords();
        clearSelection();
        showAnswerToast(root, solvedWord, 'correct');
      }

      function checkPreview(path, pathKey) {
        var selected = normalizeWord(selectedWord(path));
        var match = wordRows('.new-word-salad__word:not(.is-solved)').find(function (wordRow) {
          return wordRow.getAttribute('data-preview-normalized') === selected;
        });
        if (match) markPreviewSolved(match);
        else finishWrong(pathKey);
      }

      function submitPath(path, pathKey) {
        if (!form || busy) return;
        busy = true;
        setChecking(true);
        renderSelection();

        var body = new FormData(form);
        body.set('path', JSON.stringify(path));
        body.set('correct_only', '1');
        fetch(formSubmitUrl(form), {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: body,
          credentials: 'same-origin'
        }).then(function (response) {
          return response.json();
        }).then(function (data) {
          var solvedWord = selectedWord(path);
          if (data && data.status === 'duplicate') {
            finishWrong(pathKey);
            showAnswerToast(root, solvedWord, 'duplicate');
            return;
          }
          if (!data || data.status !== 'ok' || !data.word_salad_correct) {
            finishWrong(pathKey, data && data.word_salad_extra);
            return;
          }
          var taskId = root.getAttribute('data-task-id') || '';
          activeDragRoot = null;
          if (data.update_task_html_new && typeof window.applyNewUiTaskHtml === 'function') {
            window.applyNewUiTaskHtml(data.update_task_html_new);
            showAnswerToast(
              document.querySelector('[data-word-salad-root][data-task-id="' + taskId + '"]'),
              solvedWord,
              'correct'
            );
          } else {
            window.location.reload();
          }
        }).catch(function () { finishWrong(pathKey); });
      }

      function maybeCheck(opts) {
        opts = opts || {};
        if (!currentPath.length) return;
        var length = currentPath.length;
        var lengths = unsolvedLengths();
        var matchesAnswerLength = lengths.indexOf(length) >= 0;
        if (!matchesAnswerLength && (!opts.fromRelease || length < EXTRA_MIN_LENGTH)) return;
        var pathKey = currentPath.join(',');
        if (attemptedPaths[pathKey]) return;
        if (busy) return;
        attemptedPaths[pathKey] = true;
        var path = currentPath.slice();
        if (isPreview) {
          if (matchesAnswerLength) checkPreview(path, pathKey);
          return;
        }
        submitPath(path, pathKey);
      }

      cells.forEach(function (cell) {
        var index = cellIndex(cell);
        if (index >= 0) cellByIndex[index] = cell;
        cell.addEventListener('pointerdown', function (event) {
          if (!cell.classList.contains('is-active')) return;
          event.preventDefault();
          lastSaladRoot = root;
          activeDragRoot = root;
          var started = startWordSaladPress(currentPath, cellIndex(cell), isActiveIndex);
          applyPath(started.path, started.clearOnRelease);
        }, { passive: false });
        cell.addEventListener('pointerenter', function () {
          if (activeDragRoot === root) appendCell(cell);
        });
      });

      root.__appendWordSaladCell = appendCell;
      root.__finishWordSaladPath = finishPress;
      root.__cancelWordSaladPress = function () { clearSingleOnRelease = false; };
      root.__clearWordSaladSelection = function () {
        var hadSelection = currentPath.length > 0 || !!clearSingleOnRelease;
        if (activeDragRoot === root) {
          clearSingleOnRelease = false;
          activeDragRoot = null;
        }
        if (!hadSelection) return false;
        clearSelection();
        return true;
      };
      root.__revealWordSaladHint = function (button) {
        var wordRow = button.closest('.new-word-salad__word');
        if (!wordRow || wordRow.classList.contains('is-solved')) return;
        var count = (parseInt(wordRow.getAttribute('data-hint-count'), 10) || 0) + 1;
        renderPreviewHint(wordRow, count);
        savePreviewState();
        syncPreviewPoints();
      };

      if (resetBtn) resetBtn.addEventListener('click', clearSelection);
      if (isPreview) restorePreviewState();
      restoreExtraWords();
      if (window.ResizeObserver && gridEl) {
        var resizeObserver = new ResizeObserver(renderGridOverlays);
        resizeObserver.observe(gridEl);
      }
      syncSolvedState();
      renderSelection();
    });
  }

  function cellFromPoint(x, y) {
    var el = global.document && global.document.elementFromPoint(x, y);
    if (!el || !el.closest) return null;
    var cell = el.closest('[data-word-salad-cell]');
    if (!cell || !cell.classList.contains('is-active')) return null;
    return cell;
  }

  function bindDocumentListeners(doc) {
    doc.addEventListener('pointermove', function (event) {
      if (!activeDragRoot) return;
      if (event.cancelable) event.preventDefault();
      var cell = cellFromPoint(event.clientX, event.clientY);
      if (!cell || !activeDragRoot.contains(cell)) return;
      if (typeof activeDragRoot.__appendWordSaladCell === 'function') {
        activeDragRoot.__appendWordSaladCell(cell);
      }
    }, { capture: true, passive: false });

    doc.addEventListener('touchmove', function (event) {
      if (!activeDragRoot) return;
      if (event.cancelable) event.preventDefault();
    }, { capture: true, passive: false });

    doc.addEventListener('pointerup', function () {
      var root = activeDragRoot;
      activeDragRoot = null;
      if (root && root.isConnected && typeof root.__finishWordSaladPath === 'function') {
        root.__finishWordSaladPath();
      }
    }, true);

    doc.addEventListener('pointercancel', function () {
      if (activeDragRoot && typeof activeDragRoot.__cancelWordSaladPress === 'function') {
        activeDragRoot.__cancelWordSaladPress();
      }
      activeDragRoot = null;
    }, true);

    doc.addEventListener('click', function (event) {
      var button = event.target && event.target.closest
        ? event.target.closest('.support-preview-readonly .new-word-salad__hint-btn')
        : null;
      if (!button) return;
      event.preventDefault();
      var root = button.closest('[data-word-salad-root]');
      if (root && typeof root.__revealWordSaladHint === 'function') root.__revealWordSaladHint(button);
    }, true);

    doc.addEventListener('keydown', function (event) {
      if (!shouldHandleWordSaladEscape(event, doc)) return;
      var root = (activeDragRoot && activeDragRoot.isConnected) ? activeDragRoot : null;
      if (!root && lastSaladRoot && lastSaladRoot.isConnected) root = lastSaladRoot;
      if (!root) {
        var roots = doc.querySelectorAll('[data-word-salad-root]');
        if (roots.length === 1) root = roots[0];
      }
      if (!root || typeof root.__clearWordSaladSelection !== 'function') return;
      if (root.__clearWordSaladSelection()) event.preventDefault();
    }, true);
  }

  global.WordSaladPath = {
    cellsAreAdjacent: cellsAreAdjacent,
    neighborPairs: neighborPairs,
    nextPath: nextWordSaladPath,
    startPress: startWordSaladPress,
    movePress: moveWordSaladPress,
    endPress: endWordSaladPress,
    shouldHandleEscape: shouldHandleWordSaladEscape,
    rememberExtra: rememberExtraWord,
    feedbackForResult: feedbackForResult,
    EXTRA_MIN_LENGTH: EXTRA_MIN_LENGTH
  };
  global.initWordSalad = initWordSalad;
  if (global.document) {
    bindDocumentListeners(global.document);
    initWordSalad(global.document);
  }
})(typeof window !== 'undefined' ? window : globalThis);
