(function (global) {
  'use strict';

  var root = document.querySelector('[data-ladder-tutorial]');
  if (!root) return;
  var configNode = document.getElementById('ladder-tutorial-data');
  var config = JSON.parse(configNode.textContent);
  var first = config.steps.first;
  var second = config.steps.second;
  var lowerPair = config.steps.lower_pair;
  var phase = 'locked';
  var solvedIndices = new Set(config.initial_solved_indices);

  function row(index) { return root.querySelector('[data-tutorial-slot="' + index + '"]'); }
  function clue(index) { return root.querySelector('[data-tutorial-clue="' + index + '"]'); }
  function input(index) { return root.querySelector('[data-tutorial-answer-input="' + index + '"]'); }
  function clueTarget(index) { return root.querySelector('[data-tutorial-clue="' + index + '"]'); }
  function region(indices) {
    return global.InterovesTutorial.combineTargets(indices.map(row).filter(Boolean));
  }
  function setLocked(value) { root.dataset.tutorialLocked = value ? '1' : '0'; }
  function setInputsLocked(allowed) {
    var permitted = Array.isArray(allowed) ? new Set(allowed) : null;
    root.querySelectorAll('[data-tutorial-answer-input]').forEach(function (node) {
      var answerIndex = Number(node.dataset.tutorialAnswerInput);
      var format = node.getAttribute('data-raddle-format') || '';
      var slots = (format.match(/#/g) || []).length;
      if (slots) node.maxLength = slots;
      var enabled = allowed === 'all' || (permitted ? permitted.has(answerIndex) : allowed !== null && answerIndex === allowed);
      node.readOnly = !enabled;
      node.setAttribute('aria-disabled', enabled ? 'false' : 'true');
      node.classList.toggle('tutorial-input--locked', !enabled);
    });
  }
  function activateInput(index, shouldFocus) {
    var r = row(index);
    if (!r) return;
    r.classList.add('new-raddle-row--playable', 'new-raddle-row--focus');
    r.classList.remove('new-raddle-row--draft', 'new-raddle-row--locked');
    var actions = r.querySelector('.new-raddle-actions');
    if (actions && !actions.querySelector('[data-tutorial-form]')) {
      actions.innerHTML = '<form class="new-raddle-line-form" action="#" method="post" data-tutorial-form="' + index + '" id="tutorial-raddle-form-' + index + '"><button type="submit" class="new-replacements-check-btn" aria-label="Проверить слово"><i class="ph ph-check" aria-hidden="true"></i></button></form>';
    }
    var form = actions && actions.querySelector('[data-tutorial-form]');
    if (form && !form.querySelector('button[type="submit"]')) {
      var button = document.createElement('button');
      button.type = 'submit';
      button.className = 'new-replacements-check-btn';
      button.setAttribute('aria-label', 'Проверить слово');
      button.innerHTML = '<i class="ph ph-check" aria-hidden="true"></i>';
      form.appendChild(button);
    }
    var field = input(index);
    if (field) {
      if (form && form.id) field.setAttribute('form', form.id);
    }
    setInputsLocked(index);
    if (field && shouldFocus !== false) field.focus();
  }
  function markClueUsed(index) {
    var node = clue(index);
    if (!node) return;
    var used = root.querySelector('[data-tutorial-target="clues-used"] ul');
    if (!used) return;
    var html = (config.used_hint_display && config.used_hint_display[index]) || '';
    node.remove();
    var item = document.createElement('li');
    item.className = 'new-raddle-clue new-raddle-clue--used';
    item.dataset.tutorialClue = String(index);
    item.dataset.hintIndex = String(index);
    item.innerHTML = '<span class="new-raddle-clue__text">' + html + '</span>';
    used.appendChild(item);
  }
  function markSolved(index, answer) {
    var r = row(index);
    if (!r) return;
    solvedIndices.add(index);
    r.classList.remove('new-raddle-row--playable', 'new-raddle-row--focus', 'new-raddle-row--draft');
    r.classList.add('new-raddle-row--solved', 'tutorial-slot--correct');
    var line = r.querySelector('.new-raddle-line');
    if (line) line.innerHTML = '<span class="new-raddle-line__solved-text"><strong>' + answer + '</strong></span>';
    var actions = r.querySelector('.new-raddle-actions');
    if (actions) actions.innerHTML = '<span class="new-replacements-check-btn new-replacements-check-btn--done" data-tooltip="Верно"><i class="ph ph-check" aria-hidden="true"></i></span>';
    setInputsLocked(null);
  }
  function freePlayEdges() {
    var top = null;
    var bottom = null;
    for (var i = 0; i < config.words.length; i += 1) {
      if (!solvedIndices.has(i) && solvedIndices.has(i - 1)) { top = i; break; }
    }
    for (var j = config.words.length - 1; j >= 0; j -= 1) {
      if (!solvedIndices.has(j) && solvedIndices.has(j + 1)) { bottom = j; break; }
    }
    return Array.from(new Set([top, bottom].filter(function (value) { return value !== null; })));
  }
  function startFreePlay() {
    phase = 'freeplay';
    var edges = freePlayEdges();
    edges.forEach(function (index) { activateInput(index, false); });
    setLocked(false);
    setInputsLocked(edges);
  }
  function setFreePlayFeedback(index, message) {
    var r = row(index);
    if (!r) return;
    var cell = r.querySelector('.new-raddle-block__word');
    var status = cell.querySelector('.tutorial-local-feedback');
    if (!status) {
      status = document.createElement('div');
      status.className = 'new-attempt-msg new-raddle-word-msg tutorial-local-feedback';
      cell.appendChild(status);
    }
    status.textContent = message;
  }
  function selectHint(index) {
    if (phase !== 'choose-first' && phase !== 'choose-second') return;
    var expected = phase === 'choose-first' ? first.hint_index : second.hint_index;
    if (index !== expected) {
      tutorial.feedback('Не эта. Попробуй другую.');
      return;
    }
    var selected = clue(index);
    if (selected) selected.classList.add('new-raddle-clue--active');
    phase = phase === 'choose-first' ? 'answer-first' : 'answer-second';
    setLocked(false);
    activateInput(phase === 'answer-first' ? first.word_index : second.word_index);
    tutorial.setStep(phase === 'answer-first' ? 5 : 8);
  }
  function submit(index, value) {
    if (phase === 'freeplay') {
      if (!freePlayEdges().includes(index)) return;
      if (String(value || '').trim().toUpperCase() !== config.words[index]) {
        setFreePlayFeedback(index, 'Не совсем. Попробуй ещё раз.');
        return;
      }
      var edgeHint = solvedIndices.has(index - 1) ? index - 1 : index;
      markSolved(index, config.words[index]);
      markClueUsed(edgeHint);
      if (solvedIndices.size >= config.words.length) {
        phase = 'complete';
        setLocked(true);
        setInputsLocked(null);
        var completion = root.querySelector('[data-ladder-tutorial-complete]');
        if (completion) completion.hidden = false;
        return;
      }
      setTimeout(startFreePlay, 0);
      return;
    }
    if (phase !== 'answer-first' && phase !== 'answer-second') return;
    var expected = phase === 'answer-first' ? first : second;
    if (index !== expected.word_index) return;
    if (String(value || '').trim().toUpperCase() !== expected.answer) {
      tutorial.feedback('Не совсем. Попробуй ещё раз.');
      return;
    }
    markSolved(index, expected.answer);
    markClueUsed(expected.hint_index);
    tutorial.feedback('Верно!');
    if (phase === 'answer-first') {
      phase = 'lower-explain';
      setLocked(true);
      setTimeout(function () { tutorial.setStep(6); }, 250);
    } else {
      phase = 'finished';
      setLocked(true);
      setTimeout(function () { tutorial.finish(); }, 250);
    }
  }

  var tutorial = global.InterovesTutorial.createShell(root, {
    steps: [
      {kind: 'info', title: 'Это Лесенка', text: 'Нужно соединить верхнее слово «' + config.words[0] + '» с нижним «' + config.words[config.words.length - 1] + '».', targets: ['[data-tutorial-target="word-list"]']},
      {kind: 'info', title: 'Соседние слова', text: 'Соседние слова связывает подсказка.\nИщи её для следующей ступеньки после «' + config.words[0] + '».', targets: [function () { return region([0, 1]); }, function () { return clue(first.hint_index); }], calloutTarget: [function () { return clue(first.hint_index); }]},
      {kind: 'info', title: 'Длина слова', text: 'Число в скобках показывает длину слова.\nЗдесь нужно слово из ' + config.lengths[first.word_index] + ' букв.', targets: [function () { return row(first.word_index); }]},
      {kind: 'info', title: 'Подсказки не по порядку', text: 'Подсказки расположены не по порядку.\nНайди ту, что поможет продолжить от «' + config.words[0] + '».', nextLabel: 'Попробовать', targets: ['[data-tutorial-target="clues-unused"]']},
      {kind: 'action', title: 'Выбери подсказку', text: 'Выбери подсказку для следующего слова.', targets: ['[data-tutorial-target="clues-unused"]']},
      {kind: 'action', title: 'Введи слово', text: 'Отлично. Теперь введи следующее слово.\nВ нём ' + config.lengths[first.word_index] + ' букв.', targets: [function () { return row(first.word_index); }, function () { return clueTarget(first.hint_index); }], calloutTarget: [function () { return input(first.word_index); }], calloutSide: 'below'},
      {kind: 'info', title: 'Можно идти с двух сторон', text: 'Лесенку можно собирать с обоих концов.\nЗдесь нужно найти слово перед «' + config.words[lowerPair.word_indices[1]] + '».', targets: [function () { return region(lowerPair.word_indices); }]},
      {kind: 'info', title: 'Подсказка снизу', text: 'Та же подсказка связывает два соседних слова — теперь у нижнего края.', nextLabel: 'Теперь сам', targets: [function () { return region(lowerPair.word_indices); }, function () { return clue(lowerPair.hint_index); }]},
      {kind: 'action', title: 'Попробуй сам', text: 'Теперь выбери подходящую подсказку и введи следующее слово.', targets: ['[data-tutorial-target="clues-unused"]']},
    ],
    onFinish: function () {
      root.classList.add('tutorial-complete');
      setInputsLocked(null);
      setLocked(true);
    },
    onStep: function (stepIndex) {
      if (stepIndex === 4) phase = 'choose-first';
      if (stepIndex === 8) phase = 'choose-second';
      if (stepIndex === 3 || stepIndex === 6 || stepIndex === 7) {
        setLocked(true);
        setInputsLocked(null);
      }
    },
    onClose: function (eventName) {
      startFreePlay();
    },
  });

  root.querySelectorAll('[data-tutorial-clue]').forEach(function (node) {
    node.setAttribute('role', 'button');
    node.tabIndex = -1;
    node.addEventListener('click', function (event) {
      if (event.target.closest('[data-tutorial-close]')) return;
      selectHint(Number(node.dataset.tutorialClue));
    });
    node.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      node.click();
    });
  });
  root.addEventListener('submit', function (event) {
    var form = event.target.closest('[data-tutorial-form]');
    if (!form) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    var field = input(Number(form.dataset.tutorialForm));
    submit(Number(form.dataset.tutorialForm), field && global.RaddleMaskedInput
      ? global.RaddleMaskedInput.getSubmitValue(field) : field && field.value);
  }, true);
  root.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter') return;
    var field = event.target.closest('[data-tutorial-answer-input]');
    if (!field) return;
    event.preventDefault();
    submit(Number(field.dataset.tutorialAnswerInput), global.RaddleMaskedInput
      ? global.RaddleMaskedInput.getSubmitValue(field) : field.value);
  }, true);
  if (global.RaddleMaskedInput && global.RaddleMaskedInput.bindAll) {
    global.RaddleMaskedInput.bindAll(root, {
      onComplete: function (field, letters) {
        if (!field || !field.matches('[data-tutorial-answer-input]')) return;
        submit(Number(field.dataset.tutorialAnswerInput), letters);
      },
    });
  }
  root.addEventListener('click', function (event) {
    if (event.target.closest('[data-tutorial-clue]')) return;
    if (root.dataset.tutorialLocked === '1' && event.target.closest('button[type="submit"]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  root.addEventListener('tutorial:step', function (event) {
    var selectable = event.detail === 4 || event.detail === 8;
    root.querySelectorAll('[data-tutorial-clue]').forEach(function (node) {
      if (node.closest('[data-tutorial-target="clues-unused"]')) node.tabIndex = selectable ? 0 : -1;
    });
  });
  setLocked(true);
  setInputsLocked(null);
}(window));
