(function () {
  'use strict';

  var activeDragRoot = null;

  function normalizeWord(value) {
    return String(value || '')
      .toUpperCase()
      .replace(/Ё/g, 'Е')
      .replace(/[^А-ЯA-Z]/g, '');
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
      var cells = Array.prototype.slice.call(root.querySelectorAll('[data-word-salad-cell]'));
      var cellByIndex = {};
      var currentEl = root.querySelector('[data-word-salad-current]');
      var pathInput = root.querySelector('[data-word-salad-path]');
      var form = root.querySelector('.new-word-salad__form');
      var resetBtn = root.querySelector('[data-word-salad-reset]');
      var msg = root.querySelector('.new-word-salad__msg');
      var isPreview = !!root.closest('.support-preview-readonly');

      function cellIndex(cell) {
        var value = parseInt(cell && cell.getAttribute('data-index'), 10);
        return isNaN(value) ? -1 : value;
      }

      function isAdjacent(left, right) {
        var rowA = Math.floor(left / 4);
        var colA = left % 4;
        var rowB = Math.floor(right / 4);
        var colB = right % 4;
        return Math.max(Math.abs(rowA - rowB), Math.abs(colA - colB)) <= 1;
      }

      function selectedWord() {
        return currentPath.map(function (index) {
          var cell = cellByIndex[index];
          return cell ? (cell.getAttribute('data-letter') || cell.textContent || '').trim() : '';
        }).join('');
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
        if (currentEl) currentEl.textContent = selectedWord() || '—';
        if (pathInput) pathInput.value = JSON.stringify(currentPath);
        if (resetBtn) resetBtn.disabled = currentPath.length === 0 || busy;
      }

      function clearSelection(keepMessage) {
        currentPath = [];
        attemptedPaths = {};
        if (!keepMessage && msg) msg.textContent = '';
        renderSelection();
      }

      function appendCell(cell) {
        if (busy || !cell.classList.contains('is-active')) return;
        var index = cellIndex(cell);
        if (index < 0) return;
        var existing = currentPath.indexOf(index);
        if (existing >= 0) {
          currentPath = currentPath.slice(0, existing + 1);
          attemptedPaths = {};
          renderSelection();
          return;
        }
        if (currentPath.length && !isAdjacent(currentPath[currentPath.length - 1], index)) return;
        currentPath.push(index);
        renderSelection();
      }

      function unsolvedLengths() {
        return Array.prototype.map.call(
          root.querySelectorAll('.new-word-salad__word:not(.is-solved)[data-word-length]'),
          function (word) { return parseInt(word.getAttribute('data-word-length'), 10) || 0; }
        ).filter(function (length) { return length > 0; });
      }

      function hasLongerWord(length) {
        return unsolvedLengths().some(function (other) { return other > length; });
      }

      function finishWrong(comment) {
        busy = false;
        root.classList.remove('is-checking');
        if (msg) msg.textContent = comment || 'Такого слова нет.';
        if (!hasLongerWord(currentPath.length)) clearSelection(true);
        else renderSelection();
      }

      function markPreviewSolved(wordRow) {
        var answer = wordRow.getAttribute('data-preview-answer') || selectedWord();
        wordRow.classList.add('is-solved');
        var mask = wordRow.querySelector('.new-word-salad__mask');
        if (mask) mask.textContent = answer;
        var hint = wordRow.querySelector('.new-word-salad__hint-btn');
        if (hint) hint.disabled = true;
        if (!wordRow.querySelector('.new-word-salad__answer')) {
          var answerEl = document.createElement('div');
          answerEl.className = 'new-word-salad__answer';
          answerEl.textContent = answer;
          wordRow.appendChild(answerEl);
        }
        if (msg) msg.textContent = 'Слово найдено (preview, без сохранения).';
        clearSelection(true);
      }

      function checkPreview() {
        var selected = normalizeWord(selectedWord());
        var match = null;
        root.querySelectorAll('.new-word-salad__word:not(.is-solved)').forEach(function (wordRow) {
          if (match) return;
          if (wordRow.getAttribute('data-preview-normalized') === selected) match = wordRow;
        });
        if (match) markPreviewSolved(match);
        else finishWrong('Такого слова нет (preview).');
      }

      function responseError(data) {
        var messages = {
          no_profile: 'Нужно заполнить профиль.',
          no_team: 'Для командного режима нужна команда.',
          no_anon: 'Не удалось сохранить анонимный ключ.',
          duplicate: 'Это слово уже отправлялось.',
          attempt_limit_exceeded: 'Попытки закончились.',
          no_access: 'Нет доступа к отправке.'
        };
        finishWrong(messages[data && data.status] || 'Не удалось проверить слово.');
      }

      function submitPath() {
        if (!form || busy) return;
        busy = true;
        root.classList.add('is-checking');
        renderSelection();
        if (msg) msg.textContent = 'Проверяем…';

        var body = new FormData(form);
        body.set('path', JSON.stringify(currentPath));
        body.set('correct_only', '1');
        fetch(form.action, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: body,
          credentials: 'same-origin'
        }).then(function (response) {
          return response.json();
        }).then(function (data) {
          if (!data || data.status !== 'ok') {
            responseError(data);
            return;
          }
          if (!data.word_salad_correct) {
            finishWrong(data.word_salad_comment || 'Такого слова нет.');
            return;
          }
          activeDragRoot = null;
          if (data.update_task_html_new && typeof window.applyNewUiTaskHtml === 'function') {
            window.applyNewUiTaskHtml(data.update_task_html_new);
          } else {
            window.location.reload();
          }
        }).catch(function () {
          finishWrong('Ошибка сети.');
        });
      }

      function maybeCheck() {
        if (busy || !currentPath.length) return;
        var lengths = unsolvedLengths();
        if (!lengths.length) return;
        var length = currentPath.length;
        var maxLength = Math.max.apply(Math, lengths);
        if (lengths.indexOf(length) < 0) {
          if (length > maxLength) finishWrong('Слов такой длины нет.');
          return;
        }
        var pathKey = currentPath.join(',');
        if (attemptedPaths[pathKey]) return;
        attemptedPaths[pathKey] = true;
        if (isPreview) checkPreview();
        else submitPath();
      }

      cells.forEach(function (cell) {
        var index = cellIndex(cell);
        if (index >= 0) cellByIndex[index] = cell;
        cell.addEventListener('pointerdown', function (event) {
          event.preventDefault();
          activeDragRoot = root;
          appendCell(cell);
        });
        cell.addEventListener('pointerenter', function () {
          if (activeDragRoot === root) appendCell(cell);
        });
      });

      root.__finishWordSaladPath = function () {
        maybeCheck();
      };

      if (resetBtn) {
        resetBtn.addEventListener('click', function () { clearSelection(false); });
      }
      renderSelection();
    });
  }

  function revealPreviewHint(button) {
    var wordRow = button.closest('.new-word-salad__word');
    var root = button.closest('[data-word-salad-root]');
    if (!wordRow || !root || wordRow.classList.contains('is-solved')) return;
    var firstLetter = wordRow.getAttribute('data-first-letter') || '';
    var mask = wordRow.querySelector('.new-word-salad__mask');
    if (mask && firstLetter && mask.textContent.indexOf('⬜') >= 0) {
      mask.textContent = mask.textContent.replace('⬜', firstLetter);
    }
    wordRow.classList.add('is-hinted');
    button.disabled = true;
    root.querySelectorAll('[data-word-salad-cell]').forEach(function (cell) {
      if (cell.getAttribute('data-letter') === firstLetter) cell.classList.add('is-hinted');
    });
    var msg = root.querySelector('.new-word-salad__msg');
    if (msg) msg.textContent = 'Подсказка показана локально (preview, без сохранения).';
  }

  document.addEventListener('pointerup', function () {
    var root = activeDragRoot;
    activeDragRoot = null;
    if (root && root.isConnected && typeof root.__finishWordSaladPath === 'function') {
      root.__finishWordSaladPath();
    }
  }, true);

  document.addEventListener('pointercancel', function () {
    activeDragRoot = null;
  }, true);

  document.addEventListener('click', function (event) {
    var button = event.target && event.target.closest
      ? event.target.closest('.support-preview-readonly .new-word-salad__hint-btn')
      : null;
    if (!button) return;
    event.preventDefault();
    revealPreviewHint(button);
  }, true);

  window.initWordSalad = initWordSalad;
  initWordSalad(document);
})();
