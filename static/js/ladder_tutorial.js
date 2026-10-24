(function (global) {
  'use strict';

  // Popup layer of the tutorial Ladder. The board under it is the regular
  // play page (task_group.html): answers go through /send_attempt/ and the
  // card is re-rendered by the server, so this file only reads that DOM.
  var root = document.querySelector('[data-ladder-tutorial]');
  if (!root) return;
  var config = JSON.parse(document.getElementById('ladder-tutorial-data').textContent);
  var first = config.steps.first;
  var second = config.steps.second;
  var lowerPair = config.steps.lower_pair;
  var phase = 'intro';
  // Indices into the steps list below.
  var STEP = {chooseFirst: 3, answerFirst: 4, lowerPair: 5, chooseSecond: 8, answerSecond: 9};
  var currentStep = 0;

  function board() {
    return document.querySelector('.new-raddle-task[data-task-id="' + config.task_id + '"]');
  }
  function query(selector) {
    var node = board();
    return node ? node.querySelector(selector) : null;
  }
  function row(index) { return query('.new-raddle-row[data-word-index="' + index + '"]'); }
  function input(index) {
    var node = row(index);
    return node ? node.querySelector('input.new-raddle-input') : null;
  }
  function wordList() { return query('.new-raddle-ladder'); }
  function unusedClues() { return query('.new-raddle-clues__section--unused'); }
  function assistButton(index) {
    var node = row(index);
    return node ? node.querySelector('.new-raddle-assist-btn') : null;
  }
  function usedClues() { return query('.new-raddle-clues__section--used'); }
  function clue(index) { return query('[data-raddle-unused-clues] .new-raddle-clue[data-hint-index="' + index + '"]'); }
  function region(indices) {
    return global.InterovesTutorial.combineTargets(indices.map(row).filter(Boolean));
  }
  function isSolved(index) {
    var node = row(index);
    return !!(node && node.classList.contains('new-raddle-row--solved'));
  }
  function remainingIndex() {
    for (var i = 0; i < config.words.length; i += 1) {
      if (!isSolved(i)) return i;
    }
    return null;
  }

  // Point the production pair highlight and clue rendering at a row without
  // focusing its input (no mobile keyboard under an info popup).
  function showPair(index) {
    if (typeof global.setRaddleFocusForTask === 'function') {
      global.setRaddleFocusForTask(config.task_id, index);
    }
  }
  // After a tap outside the row the board treats an immediate programmatic
  // focus as stolen by a closing keyboard (raddle_keyboard_dismiss.js), so
  // the caret is placed once that window is over. Touch players tap the
  // field themselves, as in a normal game.
  var FOCUS_GUARD_MS = 750;
  function focusInputSoon(index, stepIndex) {
    if (global.matchMedia && global.matchMedia('(pointer: coarse)').matches) return;
    setTimeout(function () {
      var field = input(index);
      if (!field || !tutorial.isOpen() || currentStep !== stepIndex) return;
      if (document.activeElement === field) return;
      field.focus({preventScroll: true});
    }, FOCUS_GUARD_MS);
  }
  function releaseBoardFocus() {
    var active = document.activeElement;
    if (active && board() && board().contains(active) && active.blur) active.blur();
  }

  // «Вот эта» buttons exist only while the player has to pick a clue.
  function addPickButtons() {
    var list = query('[data-raddle-unused-clues]');
    if (!list) return null;
    var firstButton = null;
    list.querySelectorAll('.new-raddle-clue[data-hint-index]').forEach(function (item) {
      if (item.querySelector('[data-tutorial-pick]')) return;
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'new-btn new-btn--mini tutorial-clue-pick';
      button.dataset.tutorialPick = item.getAttribute('data-hint-index');
      button.textContent = 'Вот эта';
      item.appendChild(button);
      if (!firstButton) firstButton = button;
    });
    return firstButton;
  }
  function removePickButtons() {
    document.querySelectorAll('[data-tutorial-pick]').forEach(function (node) { node.remove(); });
  }
  function pickClue(index) {
    var expected = phase === 'choose-first' ? first : second;
    if (index !== expected.hint_index) {
      tutorial.feedback('Не эта. Попробуй другую.');
      return;
    }
    removePickButtons();
    phase = phase === 'choose-first' ? 'answer-first' : 'answer-second';
    tutorial.setStep(phase === 'answer-first' ? STEP.answerFirst : STEP.answerSecond);
  }

  // The way onward: back to the Ladder the player left for the tutorial,
  // otherwise today's Ladder.
  var onward = config.return_url
    ? {url: config.return_url, label: config.return_label, name: 'Твоя Лесенка'}
    : {url: config.today_url, label: 'Решить сегодняшнюю Лесенку', name: 'Сегодняшняя Лесенка'};
  var returnOffered = false;
  function trackReturn() {
    try {
      if (global.interovesAnalytics && typeof global.interovesAnalytics.trackYandexGoal === 'function') {
        global.interovesAnalytics.trackYandexGoal('ladder_tutorial_return', {game: 'ladder', solved: phase === 'solved'});
      }
    } catch (_) {}
  }

  // The page's own result card stays; the tutorial adds the way onward:
  // a congratulation popup once, and a button under the card after it.
  function showNextStep() {
    var next = root.querySelector('[data-ladder-tutorial-next]');
    var card = document.getElementById('new-task-' + config.task_id);
    if (!next || !next.hidden || !card) return;
    var link = next.querySelector('a');
    link.href = onward.url;
    link.textContent = onward.label;
    link.addEventListener('click', trackReturn);
    card.insertAdjacentElement('afterend', next);
    next.hidden = false;
    phase = 'solved';
    returnOffered = true;
    tutorial.showMessage({
      title: 'Поздравляем!',
      text: 'Обучающая Лесенка решена.\n' + onward.name + ' длиннее и может оказаться сложной. Не бойся брать подсказки 💡 — решение с ними тоже засчитывается.',
      label: onward.label,
      href: onward.url,
      onAction: trackReturn,
    });
  }

  // The card is replaced after every accepted word; react to the new state.
  function onBoardChange() {
    if (remainingIndex() === null) showNextStep();
    if (phase === 'answer-first' && isSolved(first.word_index)) {
      phase = 'lower-explain';
      if (tutorial.isOpen()) tutorial.setStep(STEP.lowerPair);
    } else if (phase === 'answer-second' && isSolved(second.word_index)) {
      phase = 'finished';
      if (tutorial.isOpen()) tutorial.finish();
    }
  }

  var tutorial = global.InterovesTutorial.createShell(root, {
    // On touch devices the player should choose when to open the keyboard.
    // Desktop input focus is handled separately by focusInputSoon().
    autoFocusInteractive: false,
    finish: {
      text: 'Теперь ты знаешь, как играть.\nОсталось одно слово посередине — оно связано подсказками и с верхним, и с нижним соседом.',
      label: 'Доиграть',
      calloutTarget: [function () { return row(remainingIndex()); }],
    },
    steps: [
      {kind: 'info', title: 'Это Лесенка', text: 'Нужно соединить верхнее слово «' + config.words[0] + '» с нижним «' + config.words[config.words.length - 1] + '».', targets: [wordList]},
      {kind: 'info', title: 'Длина слова', text: 'Число в скобках показывает длину слова.\nЗдесь нужно слово из ' + config.lengths[first.word_index] + ' букв.', targets: [function () { return row(first.word_index); }]},
      {kind: 'info', title: 'Подсказки расположены не по порядку', text: 'Соседние слова связывает подсказка, но подсказки перемешаны.\nОдна из них загадывает слово, идущее после «' + config.words[0] + '».', nextLabel: 'Попробовать', targets: [function () { return region([0, 1]); }, unusedClues], calloutTarget: [function () { return region([0, 1]); }], calloutSide: 'below'},
      {kind: 'action', title: 'Выбери подсказку', text: 'Какая подсказка ведёт от «' + config.words[0] + '» к следующему слову? Нажми «Вот эта» рядом с ней.', targets: [unusedClues]},
      {kind: 'action', title: 'Введи слово', text: 'Отлично. Теперь введи следующее слово.\nВ нём ' + config.lengths[first.word_index] + ' букв — ответ проверится автоматически.', targets: [function () { return row(first.word_index); }, function () { return clue(first.hint_index); }], interactive: [function () { return row(first.word_index); }], calloutTarget: [function () { return input(first.word_index); }], calloutSide: 'below'},
      {kind: 'info', title: 'Можно идти с двух сторон', text: 'Лесенку можно собирать с обоих концов.\nЗдесь нужно найти слово перед «' + config.words[lowerPair.word_indices[1]] + '».', targets: [function () { return region(lowerPair.word_indices); }]},
      {kind: 'info', title: 'Подсказка снизу', text: 'Если угадываешь снизу, нижнее слово сразу подставляется в подсказки зелёным.', targets: [function () { return region(lowerPair.word_indices); }, unusedClues]},
      {kind: 'info', title: 'Если не получается', text: 'Нажми 💡 у слова. Первое нажатие подсветит подходящую подсказку, второе откроет само слово.\nСлово с подсказкой приносит меньше баллов, но решение всё равно засчитается.', nextLabel: 'Теперь сам', targets: [function () { return assistButton(second.word_index); }], calloutSide: 'below'},
      {kind: 'action', title: 'Попробуй сам', text: 'Найди подсказку, которая ведёт к «' + config.words[lowerPair.word_indices[1]] + '», и нажми «Вот эта».', targets: [unusedClues]},
      {kind: 'action', title: 'Введи слово', text: 'Верно. Введи слово перед «' + config.words[lowerPair.word_indices[1]] + '».', targets: [function () { return row(second.word_index); }, function () { return clue(second.hint_index); }], interactive: [function () { return row(second.word_index); }], calloutTarget: [function () { return global.innerWidth > 720 ? usedClues() : input(second.word_index); }], calloutSide: 'over'},
    ],
    onStep: function (stepIndex) {
      currentStep = stepIndex;
      var choosing = stepIndex === STEP.chooseFirst || stepIndex === STEP.chooseSecond;
      if (!choosing) removePickButtons();
      if (stepIndex >= STEP.lowerPair) showPair(lowerPair.word_indices[0]);
      if (choosing) {
        phase = stepIndex === STEP.chooseFirst ? 'choose-first' : 'choose-second';
        releaseBoardFocus();
        var button = addPickButtons();
        // Focus now so the shell does not move it onto a clue's mark circle.
        if (button) button.focus();
      } else if (stepIndex === STEP.answerFirst) {
        if (isSolved(first.word_index)) {
          phase = 'lower-explain';
          global.requestAnimationFrame(function () {
            if (tutorial.isOpen() && currentStep === STEP.answerFirst) tutorial.setStep(STEP.lowerPair);
          });
        } else {
          phase = 'answer-first';
          focusInputSoon(first.word_index, stepIndex);
        }
      } else if (stepIndex === STEP.answerSecond) {
        if (isSolved(second.word_index)) {
          phase = 'finished';
          global.requestAnimationFrame(function () {
            if (tutorial.isOpen() && currentStep === STEP.answerSecond) tutorial.finish();
          });
        } else {
          phase = 'answer-second';
          focusInputSoon(second.word_index, stepIndex);
        }
      } else if (stepIndex >= STEP.lowerPair && stepIndex < STEP.chooseSecond) {
        phase = 'lower-explain';
        releaseBoardFocus();
      } else if (stepIndex < STEP.chooseFirst) {
        phase = 'intro';
        releaseBoardFocus();
      } else {
        releaseBoardFocus();
      }
    },
    onClose: function (eventName) {
      removePickButtons();
      var resumeWrap = document.querySelector('[data-ladder-tutorial-resume-wrap]');
      if (resumeWrap) resumeWrap.hidden = phase === 'solved';
      // Leaving the tutorial early must not strand a player who came from a
      // Ladder: offer the way back once.
      if (eventName === 'tutorial_skip' && config.return_url && !returnOffered && phase !== 'solved') {
        returnOffered = true;
        setTimeout(function () {
          tutorial.showMessage({
            title: 'Вернуться к Лесенке?',
            text: 'Обучение можно пройти позже — кнопка рядом с правилами или ссылка в самих правилах.',
            label: onward.label,
            href: onward.url,
            onAction: trackReturn,
          });
        }, 0);
        return;
      }
      phase = 'freeplay';
    },
  });

  var resumeButton = document.querySelector('[data-ladder-tutorial-resume]');
  if (resumeButton) {
    resumeButton.addEventListener('click', function () {
      var resumeWrap = document.querySelector('[data-ladder-tutorial-resume-wrap]');
      if (resumeWrap) resumeWrap.hidden = true;
      tutorial.resume();
    });
  }

  // Capture phase: runs before the production handler that would strike the
  // clue through, so its mark circle counts as a choice too.
  document.addEventListener('click', function (event) {
    if (phase !== 'choose-first' && phase !== 'choose-second') return;
    var trigger = event.target.closest && event.target.closest('[data-tutorial-pick], .new-raddle-clue__mark');
    var item = trigger && trigger.closest('[data-raddle-unused-clues] .new-raddle-clue[data-hint-index]');
    if (!item || !board() || !board().contains(item)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    pickClue(Number(item.getAttribute('data-hint-index')));
  }, true);

  new MutationObserver(function (mutations) {
    // The spotlight itself lives in the popup layer; only board changes count.
    var boardChanged = mutations.some(function (mutation) { return !root.contains(mutation.target); });
    if (!boardChanged) return;
    onBoardChange();
    tutorial.refresh();
  }).observe(document.body, {childList: true, subtree: true});
}(window));
