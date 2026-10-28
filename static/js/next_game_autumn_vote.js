(function () {
  'use strict';

  var root = document.querySelector('[data-vote-page]');
  if (!root) return;

  var ANSWERS = {
    'powders-1': 'убит',
    'powders-2': 'физрук',
    'powders-3': 'ицу',
    'cryptic-1': 'раскол',
    'cryptic-2': 'молебен',
    'cryptic-3': 'токсин'
  };

  var LOGIC = {
    'logic-castle': { state: 'loop', on: [6, 7, 8, 11, 13, 16, 17, 18] },
    'logic-nurikabe': {
      sea: [0, 1, 3, 4, 6, 7, 8, 11, 13, 16, 17, 18, 20, 21, 23, 24],
      island: [5, 9, 15, 19]
    },
    'logic-cave': {
      wall: [4, 5, 6, 9, 14, 15, 20, 21, 22],
      cave: [1, 2, 3, 8, 13, 17, 18, 19, 23]
    }
  };

  var CYCLES = {
    castle: ['', 'loop'],
    nurikabe: ['', 'sea', 'island'],
    cave: ['', 'wall', 'cave']
  };

  function pad(value) {
    return value < 10 ? '0' + value : String(value);
  }

  function formatRemaining(ms) {
    if (ms <= 0) return '0:00:00';
    var totalSec = Math.floor(ms / 1000);
    var days = Math.floor(totalSec / 86400);
    var hours = Math.floor((totalSec % 86400) / 3600);
    var minutes = Math.floor((totalSec % 3600) / 60);
    var seconds = totalSec % 60;
    var clock = pad(hours) + ':' + pad(minutes) + ':' + pad(seconds);
    if (days > 0) return days + ' д. ' + clock;
    return clock;
  }

  function tickCountdown() {
    var el = root.querySelector('[data-vote-countdown-value]');
    var deadlineRaw = root.getAttribute('data-vote-deadline') || '';
    if (!el || !deadlineRaw) return;
    var end = Date.parse(deadlineRaw);
    if (!isFinite(end)) return;
    el.textContent = formatRemaining(end - Date.now());
  }

  if ((root.getAttribute('data-vote-phase') || '') === 'live') {
    tickCountdown();
    window.setInterval(tickCountdown, 1000);
  }

  function normalizeWord(value) {
    return String(value || '').toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9]/g, '');
  }

  function showMsg(step, text, ok) {
    var msg = step.querySelector('[data-vote-msg]');
    if (!msg) return;
    msg.hidden = false;
    msg.textContent = text;
    msg.classList.toggle('is-ok', !!ok);
    msg.classList.toggle('is-bad', !ok);
  }

  function lockSolved(step) {
    step.querySelectorAll('input, button, [data-vote-check]').forEach(function (el) {
      if (el.hasAttribute('data-vote-next') || el.hasAttribute('data-vote-rules')) return;
      el.disabled = true;
    });
    var next = step.querySelector('[data-vote-next]');
    if (next) next.hidden = false;
    showMsg(step, 'Верно!', true);
  }

  function paintBoxes(step) {
    var boxes = step.querySelector('[data-vote-boxes]');
    var input = step.querySelector('input[name="answer"]');
    if (!boxes || !input) return;
    var len = parseInt(step.getAttribute('data-vote-len') || '0', 10);
    var letters = normalizeWord(input.value).slice(0, len).split('');
    if (!boxes.childElementCount) {
      for (var i = 0; i < len; i += 1) {
        boxes.appendChild(document.createElement('span'));
      }
    }
    Array.prototype.forEach.call(boxes.children, function (cell, index) {
      cell.textContent = letters[index] || '';
    });
  }

  root.querySelectorAll('[data-vote-len]').forEach(function (step) {
    var input = step.querySelector('input[name="answer"]');
    if (!input) return;
    paintBoxes(step);
    input.addEventListener('input', function () {
      paintBoxes(step);
    });
  });

  function logicSolved(step) {
    var spec = LOGIC[step.getAttribute('data-vote-key')];
    if (!spec) return false;
    var cells = {};
    step.querySelectorAll('button[data-vote-index]').forEach(function (cell) {
      cells[cell.getAttribute('data-vote-index')] = cell.getAttribute('data-vote-state') || '';
    });
    var indexes = Object.keys(cells);
    function marked(list, state) {
      return list.every(function (index) {
        return cells[String(index)] === state;
      });
    }
    if (spec.on) {
      return marked(spec.on, spec.state) && indexes.every(function (index) {
        var onLoop = spec.on.indexOf(parseInt(index, 10)) >= 0;
        return onLoop ? cells[index] === spec.state : cells[index] === '';
      });
    }
    if (spec.sea) {
      return marked(spec.sea, 'sea') && marked(spec.island, 'island') && indexes.length === spec.sea.length + spec.island.length;
    }
    return marked(spec.wall, 'wall') && marked(spec.cave, 'cave') && indexes.length === spec.wall.length + spec.cave.length;
  }

  root.addEventListener('click', function (event) {
    var cell = event.target.closest ? event.target.closest('.vote-cell[data-vote-state]') : null;
    if (cell && root.contains(cell)) {
      var step = cell.closest('[data-vote-step]');
      if (!step || step.querySelector('[data-vote-next]:not([hidden])')) return;
      var kind = step.getAttribute('data-vote-logic');
      var cycle = CYCLES[kind] || [''];
      var current = cell.getAttribute('data-vote-state') || '';
      var nextIndex = (cycle.indexOf(current) + 1) % cycle.length;
      cell.setAttribute('data-vote-state', cycle[nextIndex]);
      return;
    }

    var check = event.target.closest ? event.target.closest('[data-vote-check]') : null;
    if (check && root.contains(check)) {
      var logicStep = check.closest('[data-vote-step]');
      if (!logicStep || logicStep.querySelector('[data-vote-next]:not([hidden])')) return;
      if (logicSolved(logicStep)) lockSolved(logicStep);
      else showMsg(logicStep, 'Неверно', false);
      return;
    }

    var next = event.target.closest ? event.target.closest('[data-vote-next]') : null;
    if (next && root.contains(next)) {
      var currentStep = next.closest('[data-vote-step]');
      var play = next.closest('[data-vote-play]');
      var steps = play ? play.querySelectorAll('[data-vote-step]') : [];
      var index = Array.prototype.indexOf.call(steps, currentStep);
      if (index >= 0 && index + 1 < steps.length) {
        currentStep.hidden = true;
        steps[index + 1].hidden = false;
      } else if (play) {
        var done = play.querySelector('[data-vote-done]');
        if (done) done.hidden = false;
        next.hidden = true;
      }
      return;
    }

    var rulesBtn = event.target.closest ? event.target.closest('[data-vote-rules]') : null;
    if (rulesBtn && root.contains(rulesBtn)) {
      openRules(rulesBtn.getAttribute('data-vote-rules'));
      return;
    }

    var link = event.target.closest ? event.target.closest('[data-vote-cta]') : null;
    if (!link || !root.contains(link)) return;
    var candidate = link.getAttribute('data-vote-cta') || '';
    var goal = root.getAttribute('data-vote-click-goal') || '';
    if (!candidate || !goal || !window.interovesAnalytics) return;
    if (typeof window.interovesAnalytics.trackYandexGoalOnce !== 'function') return;
    window.interovesAnalytics.trackYandexGoalOnce(goal + ':' + candidate, goal, { candidate: candidate });
  });

  root.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.hasAttribute || !form.hasAttribute('data-vote-form')) return;
    event.preventDefault();
    var step = form.closest('[data-vote-step]');
    if (!step || step.querySelector('[data-vote-next]:not([hidden])')) return;
    var key = step.getAttribute('data-vote-key');
    var expected = ANSWERS[key];
    var input = form.querySelector('input[name="answer"]');
    if (expected && normalizeWord(input && input.value) === expected) lockSolved(step);
    else showMsg(step, 'Неверно', false);
  });

  var modal = document.getElementById('vote-rules-modal');
  var modalTitle = document.getElementById('vote-rules-title');
  var modalBody = modal ? modal.querySelector('[data-vote-rules-body]') : null;
  var RULE_TITLES = { castle: 'Castle Wall', nurikabe: 'Nurikabe', cave: 'Cave' };

  function openRules(kind) {
    var source = document.getElementById('vote-rules-' + kind);
    if (!modal || !source || !modalBody || !modalTitle) return;
    modalTitle.textContent = RULE_TITLES[kind] || 'Правила';
    modalBody.innerHTML = source.innerHTML;
    modal.hidden = false;
    modal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
  }

  function closeRules() {
    if (!modal) return;
    modal.classList.remove('is-open');
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  document.addEventListener('click', function (event) {
    if (event.target && event.target.closest && event.target.closest('[data-vote-rules-close]')) closeRules();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal && !modal.hidden) closeRules();
  });
})();
