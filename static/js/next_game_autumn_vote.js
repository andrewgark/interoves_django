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

  // Pilot minis: loop edges as "a-b" (a < b) or shaded cell indexes.
  var LOGIC = {
    'logic-castle': {
      edges: ['8-9', '8-14', '9-10', '10-11', '11-17', '13-14', '13-19', '16-17', '16-22', '18-19', '18-24',
        '22-23', '23-29', '24-30', '26-27', '26-32', '27-33', '29-35', '30-31', '31-32', '33-34', '34-35']
    },
    'logic-nurikabe': { shaded: [1, 2, 4, 6, 8, 9, 11, 14, 16, 17, 18, 19, 20, 21, 24] },
    'logic-cave': { shaded: [4, 5, 6, 9, 14, 15, 20, 21, 22] }
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

  function hideMsg(step) {
    var msg = step.querySelector('[data-vote-msg]');
    if (msg) msg.hidden = true;
  }

  function lockSolved(step) {
    step.querySelectorAll('input, button').forEach(function (el) {
      if (el.hasAttribute('data-vote-next') || el.hasAttribute('data-vote-rules')) return;
      el.disabled = true;
    });
    var next = step.querySelector('[data-vote-next]');
    if (next) next.hidden = false;
    showMsg(step, 'Верно!', true);
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var CELL = 40;
  var SHADE_CYCLE = ['', 'shade', 'dot'];
  var WRONG_DELAY_MS = 900;

  function svgEl(name, attrs) {
    var el = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) { el.setAttribute(key, attrs[key]); });
    return el;
  }

  function edgeKey(a, b) {
    return a < b ? a + '-' + b : b + '-' + a;
  }

  function sameSet(list, wanted) {
    if (list.length !== wanted.length) return false;
    var sorted = list.slice().sort();
    var target = wanted.map(String).sort();
    return sorted.every(function (value, index) { return value === target[index]; });
  }

  // Penpa-style mini board: shade puzzles cycle shaded → dot → empty and paint
  // by dragging; loop puzzles draw segments between centres of adjacent cells.
  function Board(step, host) {
    var spec = JSON.parse(host.getAttribute('data-vote-grid') || '{}');
    this.step = step;
    this.kind = spec.kind;
    this.size = spec.size || 5;
    this.clues = {};
    (spec.clues || []).forEach(function (clue) { this.clues[clue.i] = clue; }, this);
    this.cells = {};
    this.edges = {};
    this.drag = null;
    this.build(host);
    this.render();
  }

  Board.prototype.locked = function () {
    return !!this.step.querySelector('[data-vote-next]:not([hidden])');
  };

  Board.prototype.build = function (host) {
    var full = this.size * CELL;
    var svg = svgEl('svg', { viewBox: '-2 -2 ' + (full + 4) + ' ' + (full + 4), class: 'vote-board__svg' });
    this.fillLayer = svgEl('g');
    this.markLayer = svgEl('g');
    this.lineLayer = svgEl('g', { class: 'vote-board__loop' });
    var grid = svgEl('g', { class: 'vote-board__grid' });
    for (var k = 1; k < this.size; k += 1) {
      grid.appendChild(svgEl('line', { x1: k * CELL, y1: 0, x2: k * CELL, y2: full }));
      grid.appendChild(svgEl('line', { x1: 0, y1: k * CELL, x2: full, y2: k * CELL }));
    }
    svg.appendChild(this.fillLayer);
    svg.appendChild(grid);
    svg.appendChild(this.markLayer);
    svg.appendChild(this.lineLayer);
    svg.appendChild(svgEl('rect', { x: 0, y: 0, width: full, height: full, class: 'vote-board__frame' }));
    host.appendChild(svg);
    this.svg = svg;

    var self = this;
    svg.addEventListener('pointerdown', function (event) { self.onDown(event); });
    svg.addEventListener('pointermove', function (event) { self.onMove(event); });
    svg.addEventListener('pointerup', function () { self.drag = null; });
    svg.addEventListener('pointercancel', function () { self.drag = null; });
    svg.addEventListener('contextmenu', function (event) { event.preventDefault(); });
  };

  Board.prototype.cellAt = function (event) {
    var rect = this.svg.getBoundingClientRect();
    if (!rect.width) return -1;
    var scale = (this.size * CELL + 4) / rect.width;
    var x = (event.clientX - rect.left) * scale - 2;
    var y = (event.clientY - rect.top) * scale - 2;
    var col = Math.floor(x / CELL);
    var row = Math.floor(y / CELL);
    if (col < 0 || row < 0 || col >= this.size || row >= this.size) return -1;
    return row * this.size + col;
  };

  Board.prototype.changed = function () {
    this.render();
    if (this.solved()) {
      this.drag = null;
      lockSolved(this.step);
    } else if (this.complete()) {
      showMsg(this.step, 'Неверно', false);
    } else {
      hideMsg(this.step);
    }
  };

  // "Неверно" only once the attempt looks finished: a single closed loop, or as
  // many shaded cells as the answer has.
  Board.prototype.complete = function () {
    var spec = LOGIC[this.step.getAttribute('data-vote-key')];
    if (!spec) return false;
    var cells = this.cells;
    if (!spec.edges) {
      return Object.keys(cells).filter(function (index) { return cells[index] === 'shade'; }).length === spec.shaded.length;
    }
    var keys = Object.keys(this.edges);
    if (keys.length < 4) return false;
    var degree = {};
    var links = {};
    keys.forEach(function (key) {
      var ends = key.split('-');
      ends.forEach(function (end, side) {
        degree[end] = (degree[end] || 0) + 1;
        (links[end] = links[end] || []).push(ends[1 - side]);
      });
    });
    var nodes = Object.keys(degree);
    if (!nodes.every(function (node) { return degree[node] === 2; })) return false;
    var seen = {};
    var stack = [nodes[0]];
    while (stack.length) {
      var node = stack.pop();
      if (seen[node]) continue;
      seen[node] = true;
      links[node].forEach(function (other) { if (!seen[other]) stack.push(other); });
    }
    return Object.keys(seen).length === nodes.length;
  };

  Board.prototype.onDown = function (event) {
    if (this.locked()) return;
    var index = this.cellAt(event);
    if (index < 0 || this.clues[index]) return;
    event.preventDefault();
    if (this.svg.setPointerCapture) this.svg.setPointerCapture(event.pointerId);
    if (this.kind === 'shade') {
      var current = this.cells[index] || '';
      var next;
      // Mouse as in penpa: left toggles shading, right toggles a dot.
      // Touch has no right button, so a tap cycles shaded → dot → empty.
      if (event.pointerType === 'mouse' && event.button === 2) next = current === 'dot' ? '' : 'dot';
      else if (event.pointerType === 'mouse') next = current === 'shade' ? '' : 'shade';
      else next = SHADE_CYCLE[(SHADE_CYCLE.indexOf(current) + 1) % SHADE_CYCLE.length];
      this.drag = { paint: next };
      this.paint(index, next);
    } else {
      this.drag = { last: index, erase: null };
    }
  };

  Board.prototype.onMove = function (event) {
    if (!this.drag || this.locked()) return;
    var index = this.cellAt(event);
    if (index < 0) return;
    if (this.kind === 'shade') {
      if (!this.clues[index]) this.paint(index, this.drag.paint);
      return;
    }
    // Walk cell by cell so a fast swipe does not skip segments.
    while (this.drag.last !== index) {
      var last = this.drag.last;
      var row = Math.floor(last / this.size);
      var col = last % this.size;
      var targetRow = Math.floor(index / this.size);
      var targetCol = index % this.size;
      if (col !== targetCol) col += targetCol > col ? 1 : -1;
      else row += targetRow > row ? 1 : -1;
      var next = row * this.size + col;
      if (this.clues[next]) { this.drag.last = next; continue; }
      if (!this.clues[last]) this.toggleEdge(last, next);
      if (!this.drag) return;
      this.drag.last = next;
    }
  };

  Board.prototype.paint = function (index, value) {
    if ((this.cells[index] || '') === value) return;
    if (value) this.cells[index] = value;
    else delete this.cells[index];
    this.changed();
  };

  Board.prototype.toggleEdge = function (a, b) {
    var key = edgeKey(a, b);
    if (this.drag.erase === null) this.drag.erase = !!this.edges[key];
    if (this.drag.erase) delete this.edges[key];
    else this.edges[key] = true;
    this.changed();
  };

  Board.prototype.solved = function () {
    var spec = LOGIC[this.step.getAttribute('data-vote-key')];
    if (!spec) return false;
    if (spec.edges) return sameSet(Object.keys(this.edges), spec.edges);
    var cells = this.cells;
    var shaded = Object.keys(cells).filter(function (index) { return cells[index] === 'shade'; });
    return sameSet(shaded, spec.shaded);
  };

  Board.prototype.center = function (index) {
    return [(index % this.size + 0.5) * CELL, (Math.floor(index / this.size) + 0.5) * CELL];
  };

  Board.prototype.render = function () {
    var self = this;
    [this.fillLayer, this.markLayer, this.lineLayer].forEach(function (layer) {
      while (layer.firstChild) layer.removeChild(layer.firstChild);
    });
    for (var index = 0; index < this.size * this.size; index += 1) {
      var x = (index % this.size) * CELL;
      var y = Math.floor(index / this.size) * CELL;
      var clue = this.clues[index];
      var state = this.cells[index] || '';
      var tone = clue ? clue.tone : (state === 'shade' ? 'shade' : 'empty');
      this.fillLayer.appendChild(svgEl('rect', { x: x, y: y, width: CELL, height: CELL, class: 'vote-board__cell vote-board__cell--' + tone }));
      if (clue && clue.tone === 'white') {
        this.markLayer.appendChild(svgEl('rect', { x: x + 3, y: y + 3, width: CELL - 6, height: CELL - 6, class: 'vote-board__white' }));
      }
      if (clue && clue.t) {
        var text = svgEl('text', { x: x + CELL / 2, y: y + CELL / 2, class: 'vote-board__clue vote-board__clue--' + clue.tone });
        text.textContent = clue.t;
        this.markLayer.appendChild(text);
      }
      if (!clue && state === 'dot') {
        var c = this.center(index);
        this.markLayer.appendChild(svgEl('circle', { cx: c[0], cy: c[1], r: 3.5, class: 'vote-board__dot' }));
      }
    }
    Object.keys(this.edges).forEach(function (key) {
      var ends = key.split('-').map(Number);
      var a = self.center(ends[0]);
      var b = self.center(ends[1]);
      self.lineLayer.appendChild(svgEl('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }));
    });
  };

  root.querySelectorAll('[data-vote-step][data-vote-logic]').forEach(function (step) {
    var host = step.querySelector('[data-vote-board]');
    if (host) new Board(step, host);
  });

  root.addEventListener('click', function (event) {
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

  // Text answers are checked as you type. A wrong answer is reported once the
  // word is full length (криптик) or after a pause in typing (порошок).
  function checkAnswer(input, immediate) {
    var step = input.closest('[data-vote-step]');
    if (!step || step.querySelector('[data-vote-next]:not([hidden])')) return;
    window.clearTimeout(input.voteWrongTimer);
    var value = normalizeWord(input.value);
    if (value && value === ANSWERS[step.getAttribute('data-vote-key')]) {
      lockSolved(step);
      return;
    }
    hideMsg(step);
    if (!value) return;
    var maxLength = parseInt(input.getAttribute('maxlength') || '0', 10);
    if (maxLength && value.length < maxLength && !immediate) return;
    if (immediate || maxLength) showMsg(step, 'Неверно', false);
    else input.voteWrongTimer = window.setTimeout(function () { showMsg(step, 'Неверно', false); }, WRONG_DELAY_MS);
  }

  root.addEventListener('input', function (event) {
    var input = event.target;
    if (input && input.matches && input.matches('[data-vote-form] input[name="answer"]')) checkAnswer(input, false);
  });

  root.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.hasAttribute || !form.hasAttribute('data-vote-form')) return;
    event.preventDefault();
    var input = form.querySelector('input[name="answer"]');
    if (input) checkAnswer(input, true);
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
