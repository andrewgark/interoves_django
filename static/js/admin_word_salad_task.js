(function($) {
  'use strict';

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
    $taskType.on('change', toggleWordSaladFields);
  });
})(django.jQuery);
