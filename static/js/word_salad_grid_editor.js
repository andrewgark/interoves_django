(function (global) {
  'use strict';

  var LETTER_RE = /[А-ЯЁA-Z]/g;
  var CELL_COUNT = 16;

  function normalizeLetter(value) {
    var letters = String(value || '').toUpperCase().replace(/Ё/g, 'Е').match(LETTER_RE);
    return letters && letters.length ? letters[0].replace('Ё', 'Е') : '';
  }

  function normalizeWord(value) {
    return String(value || '').toUpperCase().replace(/Ё/g, 'Е').replace(/[^А-ЯA-Z]/g, '');
  }

  function parseGrid(value) {
    var cells;
    if (Array.isArray(value)) {
      cells = value.map(function (cell) { return normalizeLetter(cell); });
    } else {
      cells = (String(value || '').toUpperCase().replace(/Ё/g, 'Е').match(LETTER_RE) || [])
        .map(function (ch) { return ch.replace('Ё', 'Е'); });
    }
    var result = [];
    var i;
    for (i = 0; i < CELL_COUNT; i += 1) {
      result.push(cells[i] || '');
    }
    return result;
  }

  function formatGridText(grid) {
    var cells = parseGrid(grid);
    var rows = [];
    var row;
    for (row = 0; row < 4; row += 1) {
      rows.push(cells.slice(row * 4, row * 4 + 4).join(' '));
    }
    return rows.join('\n');
  }

  function transformGrid(grid, operation) {
    var source = parseGrid(grid);
    var result = Array.from({ length: CELL_COUNT }, function () { return ''; });
    var row;
    var col;
    var targetRow;
    var targetCol;
    for (row = 0; row < 4; row += 1) {
      for (col = 0; col < 4; col += 1) {
        targetRow = row;
        targetCol = col;
        if (operation === 'rotate-counterclockwise') {
          targetRow = 3 - col;
          targetCol = row;
        } else if (operation === 'rotate-clockwise') {
          targetRow = col;
          targetCol = 3 - row;
        } else if (operation === 'flip-horizontal') {
          targetCol = 3 - col;
        } else if (operation === 'flip-vertical') {
          targetRow = 3 - row;
        }
        result[targetRow * 4 + targetCol] = source[row * 4 + col];
      }
    }
    return result;
  }

  function parseWords(value) {
    if (Array.isArray(value)) {
      return value.map(function (word) { return String(word || '').trim(); }).filter(Boolean);
    }
    return String(value || '').split(/\r?\n/).map(function (line) {
      return line.trim();
    }).filter(Boolean);
  }

  function neighbours(index) {
    var row = Math.floor(index / 4);
    var col = index % 4;
    var result = [];
    var other;
    for (other = 0; other < CELL_COUNT; other += 1) {
      if (other === index) continue;
      var otherRow = Math.floor(other / 4);
      var otherCol = other % 4;
      if (Math.max(Math.abs(row - otherRow), Math.abs(col - otherCol)) <= 1) {
        result.push(other);
      }
    }
    return result;
  }

  function findPaths(grid, word, active, limit) {
    var target = normalizeWord(word);
    var activeSet = {};
    var source = active == null ? Array.from({ length: CELL_COUNT }, function (_, i) { return i; }) : active;
    source.forEach(function (index) { activeSet[index] = true; });
    var result = [];
    limit = limit == null ? 1 : limit;

    function visit(index, position, used, path) {
      if (result.length >= limit) return;
      if (grid[index] !== target[position]) return;
      used = used.concat([index]);
      path = path.concat([index]);
      if (position === target.length - 1) {
        result.push(path);
        return;
      }
      neighbours(index).forEach(function (nxt) {
        if (activeSet[nxt] && used.indexOf(nxt) === -1) {
          visit(nxt, position + 1, used, path);
        }
      });
    }

    if (!target) return result;
    Object.keys(activeSet).map(Number).sort(function (a, b) { return a - b; }).forEach(function (index) {
      if (result.length < limit) visit(index, 0, [], []);
    });
    return result;
  }

  function allWordsSolvable(grid, words, active) {
    return words.every(function (word) {
      return findPaths(grid, word, active, 1).length > 0;
    });
  }

  function validateLive(gridValue, wordsValue, rareWordsValue) {
    var grid = parseGrid(gridValue);
    var words = parseWords(wordsValue);
    var errors = [];
    var missingWords = [];
    var removableCells = [];
    if (grid.some(function (cell) { return !cell; })) {
      errors.push('Сетка должна содержать ровно 16 букв (4×4).');
      return { ok: false, errors: errors, missingWords: missingWords, removableCells: removableCells };
    }
    if (!words.length) {
      errors.push('Добавьте хотя бы одно слово.');
      return { ok: false, errors: errors, missingWords: missingWords, removableCells: removableCells };
    }
    var normalized = words.map(normalizeWord);
    if (normalized.some(function (word) { return !word; })) {
      errors.push('Каждая строка слов должна содержать буквы.');
      return { ok: false, errors: errors, missingWords: missingWords, removableCells: removableCells };
    }
    if (new Set(normalized).size !== normalized.length) {
      errors.push('Загаданные слова не должны повторяться.');
      return { ok: false, errors: errors, missingWords: missingWords, removableCells: removableCells };
    }
    words.forEach(function (word) {
      if (!findPaths(grid, word, null, 1).length) missingWords.push(word);
    });
    if (missingWords.length) {
      errors.push('Для каждого слова должна существовать хотя бы одна дорожка.');
    }
    var cell;
    for (cell = 0; cell < CELL_COUNT; cell += 1) {
      var active = [];
      var other;
      for (other = 0; other < CELL_COUNT; other += 1) {
        if (other !== cell) active.push(other);
      }
      if (allWordsSolvable(grid, words, active)) removableCells.push(cell);
    }
    if (removableCells.length) {
      errors.push('Букву в клетке ' + (removableCells[0] + 1) + ' можно убрать уже в начальной сетке.');
    }
    var rares = parseWords(rareWordsValue);
    var rareNormalized = rares.map(normalizeWord);
    if (rareNormalized.some(function (word) { return !word; })) {
      errors.push('Каждая строка редких слов должна содержать буквы.');
    } else if (new Set(rareNormalized).size !== rareNormalized.length) {
      errors.push('Редкие слова не должны повторяться.');
    } else {
      var overlap = rareNormalized.filter(function (word) { return normalized.indexOf(word) >= 0; });
      if (overlap.length) {
        errors.push('Редкое слово не должно совпадать с обязательным ответом: ' + overlap.join(', ') + '.');
      }
      var missingRares = [];
      rares.forEach(function (word) {
        if (!findPaths(grid, word, null, 1).length) missingRares.push(word);
      });
      if (missingRares.length) {
        errors.push('Для каждого редкого слова должна существовать хотя бы одна дорожка.');
        missingWords = missingWords.concat(missingRares);
      }
    }
    return {
      ok: !errors.length,
      errors: errors,
      missingWords: missingWords,
      removableCells: removableCells
    };
  }

  function mount(host, options) {
    options = options || {};
    if (!host) return null;
    host.innerHTML = '';
    host.classList.add('ws-grid-editor');
    var cells = parseGrid(options.grid);
    var inputs = [];
    var destroyed = false;
    var transformButtons = [];
    var row;
    var board = document.createElement('div');
    board.className = 'ws-grid-editor__board';
    host.appendChild(board);
    for (row = 0; row < 4; row += 1) {
      var rowEl = document.createElement('div');
      rowEl.className = 'ws-grid-editor__row';
      var col;
      for (col = 0; col < 4; col += 1) {
        var index = row * 4 + col;
        var input = document.createElement('input');
        input.type = 'text';
        input.maxLength = 1;
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.autocapitalize = 'characters';
        input.inputMode = 'text';
        input.className = 'ws-grid-editor__cell';
        input.setAttribute('aria-label', 'Клетка ' + (index + 1));
        input.value = cells[index] || '';
        input.dataset.index = String(index);
        rowEl.appendChild(input);
        inputs.push(input);
      }
      board.appendChild(rowEl);
    }

    var toolbar = document.createElement('div');
    toolbar.className = 'ws-grid-editor__controls';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Преобразования сетки');
    [
      ['rotate-clockwise', 'ph-arrow-clockwise', 'Повернуть по часовой стрелке'],
      ['rotate-counterclockwise', 'ph-arrow-counter-clockwise', 'Повернуть против часовой стрелки'],
      ['flip-horizontal', 'ph-arrows-left-right', 'Отразить горизонтально'],
      ['flip-vertical', 'ph-arrows-down-up', 'Отразить вертикально']
    ].forEach(function (control) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'ws-grid-editor__control';
      button.dataset.operation = control[0];
      button.title = control[2];
      button.setAttribute('aria-label', control[2]);
      button.innerHTML = '<i class="ph ' + control[1] + '" aria-hidden="true"></i>';
      button.addEventListener('click', function () {
        writeGrid(transformGrid(readGrid(), control[0]));
        emitChange();
      });
      toolbar.appendChild(button);
      transformButtons.push(button);
    });
    host.appendChild(toolbar);

    function readGrid() {
      return inputs.map(function (input) { return normalizeLetter(input.value); });
    }

    function writeGrid(next) {
      parseGrid(next).forEach(function (letter, index) {
        inputs[index].value = letter;
      });
    }

    function emitChange() {
      if (destroyed || typeof options.onChange !== 'function') return;
      options.onChange(readGrid(), formatGridText(readGrid()));
    }

    function focusIndex(index) {
      if (index < 0 || index >= inputs.length) return;
      inputs[index].focus();
      inputs[index].select();
    }

    function onInput(event) {
      var input = event.target;
      var index = Number(input.dataset.index);
      var letter = normalizeLetter(input.value);
      input.value = letter;
      if (letter && index < inputs.length - 1) focusIndex(index + 1);
      emitChange();
    }

    function onKeydown(event) {
      var input = event.target;
      var index = Number(input.dataset.index);
      if (event.key === 'Backspace' && !input.value && index > 0) {
        event.preventDefault();
        inputs[index - 1].value = '';
        focusIndex(index - 1);
        emitChange();
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        focusIndex(index - 1);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        focusIndex(index + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        focusIndex(index - 4);
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        focusIndex(index + 4);
      }
    }

    function onPaste(event) {
      var text = (event.clipboardData && event.clipboardData.getData('text')) || '';
      var letters = (String(text).toUpperCase().replace(/Ё/g, 'Е').match(LETTER_RE) || [])
        .map(function (ch) { return ch.replace('Ё', 'Е'); });
      if (!letters.length) return;
      event.preventDefault();
      var start = Number(event.target.dataset.index) || 0;
      if (letters.length >= CELL_COUNT && start === 0) {
        writeGrid(letters.slice(0, CELL_COUNT));
        focusIndex(CELL_COUNT - 1);
      } else {
        var next = readGrid();
        letters.slice(0, CELL_COUNT - start).forEach(function (ch, offset) {
          next[start + offset] = ch;
        });
        writeGrid(next);
        focusIndex(Math.min(start + letters.length, CELL_COUNT - 1));
      }
      emitChange();
    }

    function onFocus(event) {
      event.target.select();
    }

    inputs.forEach(function (input) {
      input.addEventListener('input', onInput);
      input.addEventListener('keydown', onKeydown);
      input.addEventListener('paste', onPaste);
      input.addEventListener('focus', onFocus);
    });

    return {
      getGrid: readGrid,
      getGridText: function () { return formatGridText(readGrid()); },
      setGrid: function (value) {
        writeGrid(value);
        emitChange();
      },
      highlightRemovable: function (indexes) {
        var marked = {};
        (indexes || []).forEach(function (index) { marked[index] = true; });
        inputs.forEach(function (input, index) {
          input.classList.toggle('is-removable', !!marked[index]);
        });
      },
      setDisabled: function (value) {
        inputs.forEach(function (input) { input.disabled = !!value; });
        transformButtons.forEach(function (button) { button.disabled = !!value; });
      },
      destroy: function () {
        destroyed = true;
        inputs.forEach(function (input) {
          input.removeEventListener('input', onInput);
          input.removeEventListener('keydown', onKeydown);
          input.removeEventListener('paste', onPaste);
          input.removeEventListener('focus', onFocus);
        });
        host.innerHTML = '';
      }
    };
  }

  global.WordSaladGridEditor = {
    mount: mount,
    validateLive: validateLive,
    parseGrid: parseGrid,
    parseWords: parseWords,
    formatGridText: formatGridText,
    transformGrid: transformGrid,
    findPaths: findPaths,
    normalizeWord: normalizeWord
  };
})(typeof window !== 'undefined' ? window : global);
