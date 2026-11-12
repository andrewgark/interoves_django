/* Per-game tabs and the month chart on player and team pages.
 *
 * The page renders one panel per game; this script switches the tabs and
 * fills each panel's month card from /u|t/<id>/games/<game>/<YYYY-MM>/.
 * The chart is plain SVG: one column per release of the month, the public
 * median as a dashed line, the player's result as a lollipop from the median
 * (blue = better than the median, orange = worse) and the viewer as a ring.
 */
(function (root) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var CHART_HEIGHT = 230;
  var PAD = { top: 16, right: 10, bottom: 26, left: 44 };
  // Time above the cap is drawn at the top edge with an arrow: one night-long
  // solve must not flatten the rest of the month into the baseline.
  var TIME_CAP_MIN = 10 * 60;
  var TIME_CAP_MAX = 2 * 60 * 60;
  var CAP_MEDIAN_FACTOR = 3;
  // After this long a loading month explains why: medians of a month nobody
  // opened yet are computed on the first request.
  var SLOW_HINT_MS = 1800;

  var METRICS = {
    time: { label: 'Время', lowerIsBetter: true },
    att: { label: 'Попытки', lowerIsBetter: true },
    hints: { label: 'Подсказки', lowerIsBetter: true },
    score: { label: 'Баллы', lowerIsBetter: false },
  };

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    var total = Math.round(seconds);
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var secs = total % 60;
    var mm = hours ? String(minutes).padStart(2, '0') : String(minutes);
    var out = mm + ':' + String(secs).padStart(2, '0');
    return hours ? hours + ':' + out : out;
  }

  function formatValue(metric, value) {
    if (value === null || value === undefined) return '—';
    if (metric === 'time') return formatDuration(value);
    if (metric === 'score') return Math.round(value) + '%';
    return String(Math.round(value * 10) / 10).replace('.', ',');
  }

  function plural(n, forms) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return forms[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
    return forms[2];
  }

  /* Upper end of a "nice" axis: 1, 2, 2.5, 5 × 10^k (time: minute steps). */
  function niceStep(span, metric, ticks) {
    var raw = span / (ticks || 4);
    if (metric === 'time') {
      var steps = [15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600];
      for (var i = 0; i < steps.length; i++) if (steps[i] >= raw) return steps[i];
      return 3600 * Math.ceil(raw / 3600);
    }
    if (raw <= 1) return 1;
    var power = Math.pow(10, Math.floor(Math.log10(raw)));
    var bases = [1, 2, 2.5, 5, 10];
    for (var j = 0; j < bases.length; j++) {
      if (bases[j] * power >= raw) return bases[j] * power;
    }
    return 10 * power;
  }

  /* y-domain for a metric: [0, top] plus the cap above which values clip. */
  function yDomain(releases, metric, visible) {
    if (metric === 'score') return { top: 100, step: 25, cap: null };
    var medians = [];
    var values = [];
    releases.forEach(function (rel) {
      if (rel.m && rel.m[metric] !== null && rel.m[metric] !== undefined) medians.push(rel.m[metric]);
      ['p', 'v'].forEach(function (series) {
        var point = rel[series];
        if (visible[series] && point && point[metric] !== null && point[metric] !== undefined) {
          values.push(point[metric]);
        }
      });
    });
    var maxMedian = medians.length ? Math.max.apply(null, medians) : 0;
    var cap = null;
    if (metric === 'time') {
      cap = Math.min(TIME_CAP_MAX, Math.max(TIME_CAP_MIN, maxMedian * CAP_MEDIAN_FACTOR));
    } else {
      cap = Math.max(metric === 'hints' ? 4 : 10, maxMedian * CAP_MEDIAN_FACTOR);
    }
    var maxValue = Math.max.apply(null, [maxMedian].concat(values).concat([metric === 'time' ? 60 : 1]));
    var top = Math.min(maxValue, cap);
    var step = niceStep(top, metric, 4);
    top = Math.ceil(top / step) * step;
    return { top: top, step: step, cap: maxValue > top ? top : null };
  }

  function el(name, attrs, parent) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  function html(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function verdict(metric, player, median) {
    if (player === null || player === undefined || median === null || median === undefined) return '';
    if (player === median) return 'even';
    var better = METRICS[metric].lowerIsBetter ? player < median : player > median;
    return better ? 'better' : 'worse';
  }

  function dateLabel(iso) {
    if (!iso) return '';
    var parts = iso.split('-');
    return Number(parts[2]) + '.' + parts[1];
  }

  /* ---------------------------------------------------------------- chart */

  function drawChart(card, state) {
    var data = state.data;
    var metric = state.metric;
    var host = card.querySelector('[data-pgs-chart]');
    var tip = card.querySelector('[data-pgs-tip]');
    host.textContent = '';
    tip.hidden = true;
    var releases = data.releases;
    var width = Math.max(260, host.clientWidth || 600);
    var innerW = width - PAD.left - PAD.right;
    var innerH = CHART_HEIGHT - PAD.top - PAD.bottom;
    var domain = yDomain(releases, metric, state.visible);
    var band = innerW / Math.max(1, releases.length);
    var x = function (i) { return PAD.left + band * (i + 0.5); };
    var y = function (value) {
      var clipped = Math.min(value, domain.top);
      return PAD.top + innerH - (clipped / domain.top) * innerH;
    };

    var svg = el('svg', {
      class: 'pgs-chart__svg', width: width, height: CHART_HEIGHT,
      viewBox: '0 0 ' + width + ' ' + CHART_HEIGHT, role: 'img',
      'aria-label': METRICS[metric].label + ' по выпускам: ' + data.title + ', ' + data.month_label,
    }, host);

    // Grid and y-axis labels.
    for (var tick = 0; tick <= domain.top + 1e-9; tick += domain.step) {
      var ty = y(tick);
      el('line', { class: 'pgs-chart__grid', x1: PAD.left, x2: width - PAD.right, y1: ty, y2: ty }, svg);
      var label = el('text', { class: 'pgs-chart__tick', x: PAD.left - 6, y: ty + 3.5, 'text-anchor': 'end' }, svg);
      label.textContent = formatValue(metric, tick) + (domain.cap !== null && tick + domain.step > domain.top + 1e-9 ? '+' : '');
    }

    // x labels: thin out so they never collide.
    var every = Math.max(1, Math.ceil(28 / band));
    releases.forEach(function (rel, i) {
      // Counted from the end so the latest release always has a label.
      if ((releases.length - 1 - i) % every !== 0) return;
      var text = el('text', { class: 'pgs-chart__tick', x: x(i), y: CHART_HEIGHT - 8, 'text-anchor': 'middle' }, svg);
      text.textContent = '№' + rel.n;
    });

    var hover = el('rect', { class: 'pgs-chart__hover', y: PAD.top, height: innerH, width: band, x: -999 }, svg);

    // Median: dashed line, broken where a release has no median.
    if (state.visible.m) {
      var path = '';
      var pen = false;
      releases.forEach(function (rel, i) {
        var value = rel.m ? rel.m[metric] : null;
        if (value === null || value === undefined) { pen = false; return; }
        path += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(value).toFixed(1);
        pen = true;
      });
      if (path) el('path', { class: 'pgs-chart__median', d: path }, svg);
      releases.forEach(function (rel, i) {
        var value = rel.m ? rel.m[metric] : null;
        if (value === null || value === undefined) return;
        el('circle', { class: 'pgs-chart__median-dot', cx: x(i), cy: y(value), r: 2.5 }, svg);
      });
    }

    var dotR = Math.max(3, Math.min(5, band / 4));
    releases.forEach(function (rel, i) {
      var cx = x(i);
      if (rel.author || rel.pre) {
        var mark = el('text', { class: 'pgs-chart__mark', x: cx, y: PAD.top + innerH - 4, 'text-anchor': 'middle' }, svg);
        mark.textContent = rel.author ? '✎' : 'т';
      }
      var player = rel.p;
      if (state.visible.p && player) {
        var value = player[metric];
        if (metric === 'time' && !player.solved) {
          var miss = el('text', { class: 'pgs-chart__miss', x: cx, y: PAD.top + 9, 'text-anchor': 'middle' }, svg);
          miss.textContent = '×';
        } else if (value !== null && value !== undefined) {
          var median = rel.m ? rel.m[metric] : null;
          var kind = verdict(metric, value, median) || 'even';
          if (state.visible.m && median !== null && median !== undefined) {
            el('line', { class: 'pgs-chart__stem pgs-chart__stem--' + kind, x1: cx, x2: cx, y1: y(median), y2: y(value) }, svg);
          }
          el('circle', { class: 'pgs-chart__dot pgs-chart__dot--' + kind, cx: cx, cy: y(value), r: dotR }, svg);
          if (domain.cap !== null && value > domain.top) {
            el('path', { class: 'pgs-chart__clip pgs-chart__dot--' + kind, d: 'M' + (cx - 3.5) + ' ' + (PAD.top - 3) + 'l3.5 -5l3.5 5z' }, svg);
          }
        }
      }
      var mine = rel.v;
      if (state.visible.v && mine) {
        var own = mine[metric];
        if (metric === 'time' && !mine.solved) return;
        if (own !== null && own !== undefined) {
          el('circle', { class: 'pgs-chart__viewer', cx: cx, cy: y(own), r: dotR + 2.5 }, svg);
        }
      }
    });

    // One hit column per release, the full chart height: the tooltip shows
    // on hover and on tap.
    releases.forEach(function (rel, i) {
      var hit = el('rect', {
        class: 'pgs-chart__hit', x: PAD.left + band * i, y: PAD.top, width: band, height: innerH,
      }, svg);
      var show = function () {
        hover.setAttribute('x', PAD.left + band * i);
        showTip(card, tip, state, rel, x(i), width);
      };
      hit.addEventListener('mouseenter', show);
      hit.addEventListener('click', show);
    });
    svg.addEventListener('mouseleave', function () {
      hover.setAttribute('x', -999);
      if (!tip.matches(':hover')) tip.hidden = true;
    });

    var note = card.querySelector('[data-pgs-note]');
    var parts = [];
    if (domain.cap !== null) parts.push('выше ' + formatValue(metric, domain.top) + ' — обрезано, стрелка вверх');
    if (metric === 'time' && releases.some(function (r) { return r.p && !r.p.solved; })) parts.push('× — не решено');
    if (releases.some(function (r) { return r.author; })) parts.push('✎ — автор выпуска');
    if (releases.some(function (r) { return r.pre; })) parts.push('т — играл до публикации');
    note.textContent = parts.join(' · ');
    note.hidden = !parts.length;
  }

  function showTip(card, tip, state, rel, cx, width) {
    var metric = state.metric;
    tip.textContent = '';
    var head = html('div', 'pgs-tip__head');
    var link = html('a', 'pgs-tip__title', '№' + rel.n);
    link.href = rel.url;
    head.appendChild(link);
    if (rel.date) head.appendChild(html('span', 'pgs-tip__date', dateLabel(rel.date)));
    tip.appendChild(head);
    var median = rel.m ? rel.m[metric] : null;
    var rows = [];
    if (rel.author) rows.push(['', 'автор выпуска', '']);
    else if (rel.pre) rows.push(['', 'играл до публикации', '']);
    var who = state.ownerLabel;
    if (rel.p) {
      var value = rel.p[metric];
      var text = metric === 'time' && !rel.p.solved ? 'не решено' : formatValue(metric, value);
      rows.push(['p ' + (verdict(metric, value, median) || 'even'), who, text]);
    } else if (!rel.author) {
      rows.push(['p', who, 'не играл']);
    }
    rows.push(['m', 'Медиана' + (rel.m && rel.m.n ? ' (' + rel.m.n + ')' : ''), formatValue(metric, median)]);
    if (state.hasViewer) {
      var mine = rel.v;
      rows.push(['v', 'Вы', !mine ? 'не играли' : (metric === 'time' && !mine.solved ? 'не решено' : formatValue(metric, mine[metric]))]);
    }
    rows.forEach(function (row) {
      var line = html('div', 'pgs-tip__row');
      var key = html('span', 'pgs-tip__key');
      if (row[0]) key.appendChild(html('i', 'pgs-swatch pgs-swatch--' + row[0].split(' ').join(' pgs-swatch--')));
      key.appendChild(document.createTextNode(row[1]));
      line.appendChild(key);
      line.appendChild(html('strong', '', row[2]));
      tip.appendChild(line);
    });
    tip.hidden = false;
    // Beside the column, never over it: the hovered marks stay visible.
    var tipWidth = tip.offsetWidth || 180;
    var gap = 14;
    var left = cx > width / 2 ? cx - gap - tipWidth : cx + gap;
    if (left < 0 || left + tipWidth > width) left = Math.max(0, Math.min(width - tipWidth, cx - tipWidth / 2));
    tip.style.left = left + 'px';
  }

  /* ----------------------------------------------------------------- card */

  function renderKpis(card, data) {
    var box = card.querySelector('[data-pgs-kpis]');
    box.textContent = '';
    var kpi = data.kpi;
    function tile(value, label, hint) {
      var node = html('div', 'pgs-kpi');
      node.appendChild(html('strong', '', value));
      node.appendChild(html('span', '', label));
      if (hint) node.setAttribute('data-tooltip', hint);
      box.appendChild(node);
      return node;
    }
    if (kpi.places) {
      tile('#' + kpi.places.place, 'место из ' + kpi.places.total, 'Место в таблице месяца по среднему баллу за выпуск');
      tile('#' + kpi.places.place_tie, kpi.places.tie_label, 'Место в таблице месяца, если при равном среднем балле учитывать ' + (kpi.places.tie_label === 'с учётом попыток' ? 'попытки' : 'время'));
    }
    if (kpi.score_pct !== null) {
      tile(kpi.score_pct + '%', 'баллов', 'Набранные баллы от максимума по всем выпускам месяца, кроме авторских');
    }
    tile(kpi.played + ' из ' + kpi.eligible, plural(kpi.eligible, ['выпуска', 'выпусков', 'выпусков']) + ' сыграно');
    if (kpi.authored) tile(String(kpi.authored), plural(kpi.authored, ['выпуск', 'выпуска', 'выпусков']) + ' как автор');
    if (kpi.duel && kpi.duel.common) {
      var duel = kpi.duel;
      var node = html('div', 'pgs-kpi pgs-kpi--duel');
      var bar = html('div', 'pgs-duel');
      [['win', duel.wins_pct, 'вы впереди'], ['tie', duel.ties_pct, 'поровну'], ['loss', duel.losses_pct, 'впереди ' + card.dataset.owner]].forEach(function (part) {
        var item = html('span', 'pgs-duel__item pgs-duel__item--' + part[0]);
        item.appendChild(html('b', '', part[1] + '%'));
        item.appendChild(html('small', '', part[2]));
        bar.appendChild(item);
      });
      node.appendChild(bar);
      node.appendChild(html('span', '', duel.common + ' ' + plural(duel.common, ['общий выпуск', 'общих выпуска', 'общих выпусков']) + ' в этом месяце'));
      box.appendChild(node);
    }
  }

  function renderControls(card, state) {
    var data = state.data;
    var metrics = card.querySelector('[data-pgs-metrics]');
    metrics.textContent = '';
    if (data.metrics.indexOf(state.metric) === -1) state.metric = 'time';
    data.metrics.forEach(function (metric) {
      var button = html('button', 'new-segmented__btn' + (metric === state.metric ? ' is-active' : ''), METRICS[metric].label);
      button.type = 'button';
      button.setAttribute('aria-pressed', metric === state.metric ? 'true' : 'false');
      button.addEventListener('click', function () {
        state.metric = metric;
        root.PlayerGameStats.preferredMetric = metric;
        renderControls(card, state);
        drawChart(card, state);
      });
      metrics.appendChild(button);
    });

    var legend = card.querySelector('[data-pgs-legend]');
    legend.textContent = '';
    var series = [['p', state.ownerLabel], ['m', 'Медиана']];
    if (state.hasViewer) series.push(['v', 'Вы']);
    series.forEach(function (item) {
      var button = html('button', 'pgs-legend__btn');
      button.type = 'button';
      button.setAttribute('aria-pressed', state.visible[item[0]] ? 'true' : 'false');
      button.setAttribute('data-tooltip', state.visible[item[0]] ? 'Скрыть' : 'Показать');
      button.appendChild(html('i', 'pgs-swatch pgs-swatch--' + item[0] + (item[0] === 'p' ? ' pgs-swatch--better' : '')));
      button.appendChild(document.createTextNode(item[1]));
      button.addEventListener('click', function () {
        state.visible[item[0]] = !state.visible[item[0]];
        renderControls(card, state);
        drawChart(card, state);
      });
      legend.appendChild(button);
    });
  }

  function renderMonthNav(card, state) {
    var data = state.data;
    var select = card.querySelector('[data-pgs-month]');
    select.textContent = '';
    data.months.forEach(function (month) {
      var option = html('option', '', month.label + (month.played ? ' · ' + month.played : ''));
      option.value = month.key;
      option.selected = month.key === data.month;
      select.appendChild(option);
    });
    var prev = card.querySelector('[data-pgs-prev]');
    var next = card.querySelector('[data-pgs-next]');
    prev.disabled = !data.prev;
    next.disabled = !data.next;
    prev.onclick = function () { if (data.prev) load(card, state, data.prev); };
    next.onclick = function () { if (data.next) load(card, state, data.next); };
    select.onchange = function () { load(card, state, select.value); };
  }

  function render(card, state) {
    clearTimeout(state.slowTimer);
    card.classList.remove('is-loading', 'is-slow');
    card.querySelector('[data-pgs-skeleton]').hidden = true;
    card.querySelector('[data-pgs-body]').hidden = false;
    card.querySelector('[data-pgs-error]').hidden = true;
    renderMonthNav(card, state);
    renderKpis(card, state.data);
    renderControls(card, state);
    drawChart(card, state);
  }

  function load(card, state, month) {
    var url = card.dataset.url.replace('GAME', encodeURIComponent(card.dataset.game)).replace('MONTH', encodeURIComponent(month));
    if (state.cache[month]) {
      state.data = state.cache[month];
      render(card, state);
      return;
    }
    card.classList.add('is-loading');
    card.classList.remove('is-slow');
    var token = {};
    state.request = token;
    clearTimeout(state.slowTimer);
    state.slowTimer = setTimeout(function () {
      if (state.request === token) card.classList.add('is-slow');
    }, SLOW_HINT_MS);
    fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      })
      .then(function (data) {
        state.cache[data.month] = data;
        state.cache[month] = data;
        if (state.request !== token) return;
        state.data = data;
        state.hasViewer = data.releases.some(function (rel) { return rel.v !== null; });
        render(card, state);
      })
      .catch(function () {
        if (state.request !== token) return;
        clearTimeout(state.slowTimer);
        card.classList.remove('is-loading', 'is-slow');
        card.querySelector('[data-pgs-skeleton]').hidden = true;
        card.querySelector('[data-pgs-error]').hidden = false;
      });
  }

  function initCard(card) {
    if (card.pgsState) return;
    var state = {
      data: null, cache: {}, request: null, hasViewer: false,
      metric: root.PlayerGameStats.preferredMetric || 'time',
      visible: { p: true, m: true, v: true },
      ownerLabel: card.dataset.ownerShort || 'Игрок',
    };
    card.pgsState = state;
    card.querySelector('[data-pgs-retry]').addEventListener('click', function () {
      load(card, state, (state.data && state.data.month) || 'latest');
    });
    load(card, state, 'latest');
    if (typeof ResizeObserver !== 'undefined') {
      var lastWidth = 0;
      new ResizeObserver(function () {
        var width = card.querySelector('[data-pgs-chart]').clientWidth;
        if (state.data && Math.abs(width - lastWidth) > 4) {
          lastWidth = width;
          drawChart(card, state);
        }
      }).observe(card.querySelector('[data-pgs-chart]'));
    }
  }

  function selectTab(rootNode, gameId) {
    rootNode.querySelectorAll('[data-pgs-tab]').forEach(function (tab) {
      var active = tab.dataset.pgsTab === gameId;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    });
    rootNode.querySelectorAll('[data-pgs-panel]').forEach(function (panel) {
      var active = panel.dataset.pgsPanel === gameId;
      panel.hidden = !active;
      if (active) {
        var card = panel.querySelector('[data-pgs-card]');
        if (card) initCard(card);
      }
    });
  }

  function bind(doc) {
    doc.querySelectorAll('[data-pgs]').forEach(function (rootNode) {
      var tabs = Array.prototype.slice.call(rootNode.querySelectorAll('[data-pgs-tab]'));
      tabs.forEach(function (tab, index) {
        tab.addEventListener('click', function () { selectTab(rootNode, tab.dataset.pgsTab); });
        tab.addEventListener('keydown', function (event) {
          var step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
          if (!step) return;
          var next = tabs[(index + step + tabs.length) % tabs.length];
          selectTab(rootNode, next.dataset.pgsTab);
          next.focus();
        });
      });
      if (tabs.length) selectTab(rootNode, tabs[0].dataset.pgsTab);
      doc.addEventListener('click', function (event) {
        if (!event.target.closest('.pgs-chart')) {
          rootNode.querySelectorAll('[data-pgs-tip]').forEach(function (tip) { tip.hidden = true; });
        }
      });
    });
  }

  var api = {
    formatDuration: formatDuration,
    formatValue: formatValue,
    yDomain: yDomain,
    verdict: verdict,
    plural: plural,
    preferredMetric: null,
  };
  root.PlayerGameStats = api;
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { bind(document); });
    } else {
      bind(document);
    }
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
