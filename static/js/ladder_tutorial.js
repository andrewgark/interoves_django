(function (global) {
  'use strict';
  var root = document.querySelector('[data-ladder-tutorial]');
  if (!root || !global.InterovesTutorial) return;
  var selected = null, solved = {3: false, 4: false};
  var steps = [
    { title: 'Лесенка', text: 'Это Лесенка. Нужно восстановить цепочку слов от ГРЯЗЬ до КНЯЗЬ. Соседние слова связаны одной из подсказок.', next: true },
    { title: 'Уже решённый пример', text: 'Часть лесенки уже решена. Например: ГРЯЗЬ → УДАРИТЬ → ПАЛЕЦ. Каждая подсказка связывает два соседних слова.', next: true },
    { title: 'Длина слова', text: 'Число в скобках показывает длину слова. Здесь нужно слово из 5 букв, связанное с ПАЛЬЦЕМ.', next: true },
    { title: 'Подсказки', text: 'Подсказки даны не по порядку. Найди ту, которая поможет продолжить цепочку после слова ПАЛЕЦ.', next: false },
    { title: 'Первый ответ', text: 'Теперь восстанови следующее слово. В нём 5 букв.', next: false },
    { title: 'С двух сторон', text: 'Не обязательно идти только сверху вниз. Можно продолжать цепочку и от уже известных слов снизу.', next: true },
    { title: 'Попробуй сам', text: 'Теперь попробуй следующий шаг сам. Выбери подходящую подсказку и введи слово.', next: false }
  ];
  function normalize(value) { return String(value || '').trim().toUpperCase().replace(/Ё/g, 'Е'); }
  function clue(index) { return root.querySelector('[data-tutorial-clue="' + index + '"]'); }
  function slot(index) { return root.querySelector('[data-tutorial-slot="' + index + '"]'); }
  function showFeedback(text) { root.querySelector('[data-tutorial-feedback-global]').textContent = text; }
  function reset() {
    selected = null; solved = {3: false, 4: false}; showFeedback('');
    root.querySelectorAll('[data-tutorial-clue]').forEach(function (el) { el.classList.remove('new-raddle-clue--active'); el.style.pointerEvents = ''; });
    [3, 4].forEach(function (i) { var row = slot(i); row.classList.remove('new-raddle-row--playable', 'new-raddle-row--solved', 'tutorial-slot--correct'); row.classList.add('new-raddle-row--draft'); var status = row.querySelector('.tutorial-slot-status'); if (status) status.textContent = ''; });
    root.querySelector('[data-tutorial-target="clues-used"] ul').querySelectorAll('.tutorial-added-clue').forEach(function (el) { el.remove(); });
  }
  function choose(index) {
    if (![3, 6].includes(tutorial.getStep())) return;
    var expected = tutorial.getStep() === 3 ? 2 : 3;
    if (index !== expected) { showFeedback('Не эта. Попробуй другую.'); return; }
    selected = index; showFeedback('Подсказка выбрана.'); clue(index).classList.add('new-raddle-clue--active');
    if (tutorial.getStep() === 3) tutorial.goTo(4); else { activateSlot(4); tutorial.render(); }
  }
  function activateSlot(index) {
    var row = slot(index); row.classList.remove('new-raddle-row--locked'); row.classList.add('new-raddle-row--playable', 'new-raddle-row--focus');
    var input = row.querySelector('input');
    if (!input) { var line = row.querySelector('.new-raddle-line'); line.innerHTML = '<input class="new-raddle-input" data-tutorial-target="answer-input" data-tutorial-answer-input="' + index + '" maxlength="' + (index === 3 ? 5 : 4) + '" autocomplete="off" aria-label="Ответ"><span class="new-raddle-mask" aria-hidden="true">_____</span>'; input = line.querySelector('input'); }
    input.setAttribute('data-tutorial-target', 'answer-input'); input.setAttribute('data-tutorial-answer-input', index); input.setAttribute('maxlength', index === 3 ? 5 : 4); input.removeAttribute('data-raddle-draft'); input.focus(); input.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); submit(index, input.value); } };
  }
  function markClueUsed(index) {
    var c = clue(index), text = c ? c.querySelector('.new-raddle-clue__text').textContent : '';
    if (c) { c.classList.add('new-raddle-clue--used'); c.remove(); }
    var li = document.createElement('li'); li.className = 'new-raddle-clue new-raddle-clue--used tutorial-added-clue'; li.innerHTML = '<span class="new-raddle-clue__text"></span>';
    li.firstChild.textContent = text;
    root.querySelector('[data-tutorial-target="clues-used"] ul').appendChild(li);
  }
  function submit(index, value) {
    var expected = index === 3 ? 'ВВЕРХ' : 'РУКИ';
    if (normalize(value) !== expected) { showFeedback('Не совсем. Попробуй ещё раз.'); return; }
    solved[index] = true; var row = slot(index); row.classList.remove('new-raddle-row--playable', 'new-raddle-row--focus'); row.classList.add('new-raddle-row--solved', 'tutorial-slot--correct'); row.querySelector('.new-raddle-line').innerHTML = '<span class="new-raddle-line__solved-text"><strong>' + expected + '</strong></span><span class="new-raddle-line__len">(' + (index === 3 ? 5 : 4) + ')</span>'; var status = row.querySelector('.tutorial-slot-status'); if (!status) { status = document.createElement('span'); status.className = 'tutorial-slot-status'; row.querySelector('.new-raddle-block__check').appendChild(status); } status.textContent = '✓'; showFeedback('Верно. Подсказка использована, а лесенка стала на один шаг длиннее.');
    if (selected !== null) markClueUsed(selected); selected = null;
    if (index === 3) { setTimeout(function () { tutorial.goTo(5); }, 350); } else { setTimeout(function () { tutorial.complete(); }, 350); }
  }
  var tutorial = global.InterovesTutorial.create({
    root: root, game: 'ladder', version: root.dataset.tutorialVersion, steps: steps,
    target: function (index) {
      if (index === 0) return root.querySelector('[data-tutorial-target="ladder-board"]');
      if (index === 1) return [slot(0), slot(1), slot(2), clue(0), clue(1)];
      if (index === 2) return slot(3);
      if (index === 4) return root.querySelector('[data-tutorial-answer-input="3"]') || slot(3);
      if (index === 3) return root.querySelector('[data-tutorial-target="clues-unused"]');
      if (index === 6) return selected !== null ? (root.querySelector('[data-tutorial-answer-input="4"]') || slot(4)) : root.querySelector('[data-tutorial-target="clues-unused"]');
      if (index === 5) return [7, 8, 9, 10].map(slot).concat(root.querySelector('[data-tutorial-target="clues-used"]'));
      return null;
    },
    canAdvance: function (index) { return steps[index].next; },
    canGoBack: function (index) { return index < 5; },
    restart: function () { global.location.reload(); },
    render: function (index) { if (index === 4) activateSlot(3); if (index === 6 && selected === null && !solved[4]) showFeedback(''); }
  });
  root.querySelectorAll('[data-tutorial-clue]').forEach(function (el) { el.addEventListener('click', function () { choose(Number(el.dataset.tutorialClue)); }); });
  root.addEventListener('submit', function (e) { e.preventDefault(); });
  root.querySelector('[data-tutorial-start]').addEventListener('click', function () { showFeedback(''); });
})(window);
