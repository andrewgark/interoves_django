(function($) {
  'use strict';

  var GRID_SIZE = 4;

  function readGrid(value) {
    var letters = value.replace(/\s+/g, '').split('');
    if (letters.length !== GRID_SIZE * GRID_SIZE) {
      return null;
    }
    return Array.from({length: GRID_SIZE}, function(_, row) {
      return letters.slice(row * GRID_SIZE, (row + 1) * GRID_SIZE);
    });
  }

  function writeGrid(grid) {
    return grid.map(function(row) { return row.join(' '); }).join('\n');
  }

  function transformGrid(grid, operation) {
    var result = Array.from({length: GRID_SIZE}, function() {
      return Array(GRID_SIZE);
    });

    for (var row = 0; row < GRID_SIZE; row += 1) {
      for (var col = 0; col < GRID_SIZE; col += 1) {
        var targetRow = row;
        var targetCol = col;
        if (operation === 'rotate-left') {
          targetRow = GRID_SIZE - 1 - col;
          targetCol = row;
        } else if (operation === 'rotate-right') {
          targetRow = col;
          targetCol = GRID_SIZE - 1 - row;
        } else if (operation === 'flip-left-right') {
          targetCol = GRID_SIZE - 1 - col;
        } else if (operation === 'flip-top-bottom') {
          targetRow = GRID_SIZE - 1 - row;
        }
        result[targetRow][targetCol] = grid[row][col];
      }
    }
    return result;
  }

  function addGridControls() {
    var $textarea = $('#id_word_salad_grid_text');
    if (!$textarea.length || $textarea.data('transform-controls-added')) {
      return;
    }
    $textarea.data('transform-controls-added', true);

    var controls = [
      ['rotate-left', '↶', 'Повернуть против часовой стрелки'],
      ['rotate-right', '↷', 'Повернуть по часовой стрелке'],
      ['flip-left-right', '↔', 'Отразить слева направо'],
      ['flip-top-bottom', '↕', 'Отразить сверху вниз']
    ];
    var $toolbar = $('<div class="word-salad-grid-controls" role="toolbar" aria-label="Преобразования сетки"></div>');

    controls.forEach(function(control) {
      var $button = $('<button type="button" class="button word-salad-grid-control"></button>')
        .attr('title', control[2])
        .attr('aria-label', control[2])
        .attr('data-grid-operation', control[0])
        .text(control[1]);
      $toolbar.append($button);
    });

    $textarea.wrap('<div class="word-salad-grid-editor"></div>');
    $textarea.after($toolbar);
    $toolbar.on('click', '[data-grid-operation]', function() {
      var grid = readGrid($textarea.val());
      if (!grid) {
        window.alert('Сетка должна содержать ровно 16 букв.');
        $textarea.trigger('focus');
        return;
      }
      $textarea.val(writeGrid(transformGrid(grid, $(this).data('grid-operation')))).trigger('change');
    });
  }

  function toggleWordSaladFields() {
    var taskType = $('#id_task_type').val();
    var isWordSalad = taskType === 'word_salad';
    var $rows = $(
      '.form-row.field-word_salad_grid_text, ' +
      '.form-row.field-word_salad_words_text, ' +
      '.form-row.field-word_salad_rare_words_text'
    );

    if (!$rows.length) {
      return;
    }

    $rows.toggle(isWordSalad);
  }

  $(function() {
    var $taskType = $('#id_task_type');
    if (!$taskType.length) {
      return;
    }

    toggleWordSaladFields();
    addGridControls();
    $taskType.on('change', toggleWordSaladFields);
  });
})(django.jQuery);
