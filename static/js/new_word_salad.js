/**
 * Word salad grid selection.
 * Local check: node static/js/new_word_salad.test.js
 */
(function (global) {
  'use strict';

  var activeDragRoot = null;

  function cellsAreAdjacent(left, right) {
    var rowA = Math.floor(left / 4);
    var colA = left % 4;
    var rowB = Math.floor(right / 4);
    var colB = right % 4;
    return Math.max(Math.abs(rowA - rowB), Math.abs(colA - colB)) <= 1;
  }

  // Repeat events on the current cell must not toggle it off: pointermove/enter
  // fire continuously while the pointer is held on the first letter.
  function nextWordSaladPath(path, index) {
    path = path || [];
    if (index < 0) return path;
    var existing = path.indexOf(index);
    if (existing >= 0) {
      if (existing === path.length - 1) return path;
      return path.slice(0, existing + 1);
    }
    if (path.length && !cellsAreAdjacent(path[path.length - 1], index)) return path;
    return path.concat([index]);
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

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
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

  function showSolvedToast(root, word) {
    if (!word) return;
    Array.prototype.forEach.call(document.querySelectorAll('.new-word-salad__toast'), function (el) {
      el.remove();
    });
    var toast = document.createElement('div');
    toast.className = 'new-word-salad__toast';
    toast.setAttribute('role', 'status');
    toast.innerHTML =
      '<span class="new-word-salad__toast-mark" aria-hidden="true"><i class="ph ph-check-circle"></i></span>' +
      '<span class="new-word-salad__toast-word">' + escapeHtml(word) + '</span>' +
      '<span class="new-word-salad__toast-ok">Верно!</span>';
    document.body.appendChild(toast);
    toast.offsetWidth;
    toast.classList.add('is-in');
    window.setTimeout(function () {
      toast.classList.remove('is-in');
      toast.classList.add('is-out');
      window.setTimeout(function () { toast.remove(); }, 320);
    }, 2400);
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
      var cells = Array.prototype.slice.call(root.querySelectorAll('[data-word-salad-cell]'));
      var cellByIndex = {};
      var currentEl = root.querySelector('[data-word-salad-current]');
      var pathInput = root.querySelector('[data-word-salad-path]');
      var form = root.querySelector('.new-word-salad__form');
      var resetBtn = root.querySelector('[data-word-salad-reset]');
      var gridEl = root.querySelector('[data-word-salad-grid]');
      var pathSvg = root.querySelector('[data-word-salad-path-svg]');
      var pathLine = root.querySelector('[data-word-salad-path-line]');
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

      function selectedWord(path) {
        return (path || currentPath).map(function (index) {
          var cell = cellByIndex[index];
          return cell ? (cell.getAttribute('data-letter') || '').trim() : '';
        }).join('');
      }

      function renderPath() {
        if (!gridEl || !pathSvg || !pathLine) return;
        var gridRect = gridEl.getBoundingClientRect();
        if (!gridRect.width || !gridRect.height) return;
        var points = currentPath.map(function (index) {
          var cell = cellByIndex[index];
          if (!cell) return null;
          var rect = cell.getBoundingClientRect();
          return [
            rect.left - gridRect.left + rect.width / 2,
            rect.top - gridRect.top + rect.height / 2
          ];
        }).filter(Boolean);
        pathSvg.setAttribute('viewBox', '0 0 ' + gridRect.width + ' ' + gridRect.height);
        var serialized = points.map(function (point) { return point[0] + ',' + point[1]; });
        if (serialized.length === 1) serialized.push(serialized[0]);
        pathLine.setAttribute('points', serialized.join(' '));
        var sample = cellByIndex[currentPath[0]] || cells[0];
        if (sample) {
          var cellRect = sample.getBoundingClientRect();
          var stroke = Math.min(cellRect.width, cellRect.height) * 0.52;
          pathLine.style.strokeWidth = stroke + 'px';
        }
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
      }

      function renderSelection() {
        cells.forEach(function (cell) {
          cell.classList.remove('is-selected');
          cell.setAttribute('aria-pressed', 'false');
        });
        currentPath.forEach(function (index) {
          var cell = cellByIndex[index];
          if (!cell) return;
          cell.classList.add('is-selected');
          cell.setAttribute('aria-pressed', 'true');
        });
        if (currentEl) currentEl.textContent = selectedWord();
        if (pathInput) pathInput.value = JSON.stringify(currentPath);
        if (resetBtn) resetBtn.disabled = currentPath.length === 0;
        window.requestAnimationFrame(renderPath);
      }

      function clearSelection() {
        currentPath = [];
        attemptedPaths = {};
        lastRejectedPathKey = '';
        renderSelection();
      }

      function appendCell(cell) {
        if (!cell.classList.contains('is-active')) return;
        var index = cellIndex(cell);
        var next = nextWordSaladPath(currentPath, index);
        if (next === currentPath) return;
        if (next.length < currentPath.length) attemptedPaths = {};
        currentPath = next;
        renderSelection();
        if (activeDragRoot === root) maybeCheck();
      }

      function wordRows(selector) {
        return Array.prototype.slice.call(root.querySelectorAll(selector || '.new-word-salad__word'));
      }

      function unsolvedLengths() {
        return wordRows('.new-word-salad__word:not(.is-solved)[data-word-length]').map(wordLength)
          .filter(function (length) { return length > 0; });
      }

      function hasLongerWord(length) {
        return unsolvedLengths().some(function (other) { return other > length; });
      }

      function finishWrong(pathKey) {
        busy = false;
        root.classList.remove('is-checking');
        lastRejectedPathKey = pathKey || currentPath.join(',');
        if (currentPath.join(',') !== lastRejectedPathKey) {
          renderSelection();
          maybeCheck();
          return;
        }
        if (activeDragRoot !== root && !hasLongerWord(currentPath.length)) clearSelection();
        else renderSelection();
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
        clearSelection();
        showSolvedToast(root, solvedWord);
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
        root.classList.add('is-checking');
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
          if (!data || data.status !== 'ok' || !data.word_salad_correct) {
            finishWrong(pathKey);
            return;
          }
          var solvedWord = selectedWord(path);
          var taskId = root.getAttribute('data-task-id') || '';
          activeDragRoot = null;
          if (data.update_task_html_new && typeof window.applyNewUiTaskHtml === 'function') {
            window.applyNewUiTaskHtml(data.update_task_html_new);
            showSolvedToast(
              document.querySelector('[data-word-salad-root][data-task-id="' + taskId + '"]'),
              solvedWord
            );
          } else {
            window.location.reload();
          }
        }).catch(function () { finishWrong(pathKey); });
      }

      function maybeCheck() {
        if (!currentPath.length) return;
        var lengths = unsolvedLengths();
        if (!lengths.length) return;
        var length = currentPath.length;
        var maxLength = Math.max.apply(Math, lengths);
        if (lengths.indexOf(length) < 0) {
          if (length > maxLength) finishWrong();
          return;
        }
        var pathKey = currentPath.join(',');
        if (attemptedPaths[pathKey]) {
          if (!busy && activeDragRoot !== root && lastRejectedPathKey === pathKey && !hasLongerWord(length)) {
            clearSelection();
          }
          return;
        }
        if (busy) return;
        attemptedPaths[pathKey] = true;
        var path = currentPath.slice();
        if (isPreview) checkPreview(path, pathKey);
        else submitPath(path, pathKey);
      }

      cells.forEach(function (cell) {
        var index = cellIndex(cell);
        if (index >= 0) cellByIndex[index] = cell;
        cell.addEventListener('pointerdown', function (event) {
          event.preventDefault();
          activeDragRoot = root;
          appendCell(cell);
        }, { passive: false });
        cell.addEventListener('pointerenter', function () {
          if (activeDragRoot === root) appendCell(cell);
        });
      });

      root.__appendWordSaladCell = appendCell;
      root.__finishWordSaladPath = maybeCheck;
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
      if (window.ResizeObserver && gridEl) {
        var resizeObserver = new ResizeObserver(renderPath);
        resizeObserver.observe(gridEl);
      }
      syncSolvedState();
      renderSelection();
    });
  }

  function cellFromPoint(x, y) {
    var el = global.document && global.document.elementFromPoint(x, y);
    if (!el || !el.closest) return null;
    return el.closest('[data-word-salad-cell]');
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

    doc.addEventListener('pointercancel', function () { activeDragRoot = null; }, true);

    doc.addEventListener('click', function (event) {
      var button = event.target && event.target.closest
        ? event.target.closest('.support-preview-readonly .new-word-salad__hint-btn')
        : null;
      if (!button) return;
      event.preventDefault();
      var root = button.closest('[data-word-salad-root]');
      if (root && typeof root.__revealWordSaladHint === 'function') root.__revealWordSaladHint(button);
    }, true);
  }

  global.WordSaladPath = {
    cellsAreAdjacent: cellsAreAdjacent,
    nextPath: nextWordSaladPath
  };
  global.initWordSalad = initWordSalad;
  if (global.document) {
    bindDocumentListeners(global.document);
    initWordSalad(global.document);
  }
})(typeof window !== 'undefined' ? window : globalThis);
