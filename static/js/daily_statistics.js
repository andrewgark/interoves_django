(function () {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function seconds(value) {
    if (value == null || value === '' || !Number.isFinite(Number(value))) return '—';
    var n = Math.max(0, Math.round(Number(value)));
    return Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0');
  }
  function metric(label, value) {
    return '<div class="new-daily-statistics__metric"><strong>' + esc(value) + '</strong><span>' + esc(label) + '</span></div>';
  }
  function section(title, content) {
    return content ? '<section class="new-daily-statistics__section"><h3>' + esc(title) + '</h3>' + content + '</section>' : '';
  }
  function percent(value) {
    return decimal(value == null ? 0 : value) + '%';
  }
  function decimal(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return '0';
    return n.toFixed(1).replace(/\.0$/, '').replace('.', ',');
  }
  function playerWord(value) {
    var n = Math.abs(Number(value)) % 100;
    var last = n % 10;
    if (n >= 11 && n <= 19) return 'игроков';
    if (last === 1) return 'игрок';
    if (last >= 2 && last <= 4) return 'игрока';
    return 'игроков';
  }
  function attemptWord(value) {
    var n = Math.abs(Number(value)) % 100;
    var last = n % 10;
    if (n >= 11 && n <= 19) return 'попыток';
    if (last === 1) return 'попытка';
    if (last >= 2 && last <= 4) return 'попытки';
    return 'попыток';
  }
  function popularity(items) {
    return (items || []).map(function (x) {
      var tooltip = x.rare ? ' data-tooltip="Редкая находка" aria-label="Редкая находка" tabindex="0" title="Редкая находка"' : '';
      return '<li class="new-daily-statistics__guess' + (x.rare ? ' is-rare' : '') + '"><span class="new-daily-statistics__finding-pill' + (x.rare ? ' is-rare' : '') + '"' + tooltip + '><span>' + esc(x.word) + '</span><small>(' + esc(x.players) + ')</small></span></li>';
    }).join('');
  }
  function saladWords(items) {
    var words = items || [];
    var maxOrder = words.reduce(function (max, item) {
      return Math.max(max, Number(item.average_order) || 0);
    }, 0);
    return words.map(function (item) {
      var order = Number(item.average_order);
      var hasOrder = item.average_order != null && item.average_order !== '' && Number.isFinite(order);
      var width = hasOrder && maxOrder > 0 ? Math.max(4, Math.round(order / maxOrder * 100)) : 0;
      var tone = !hasOrder ? ' is-missing' : (Number(item.hint_percent) > 0 ? ' is-hinted' : '');
      var complexityLabel = hasOrder ? decimal(order) : '—';
      var complexityTooltip = hasOrder
        ? 'Средний порядковый номер: ' + complexityLabel + '. На каком месте игроки в среднем угадывали слово без подсказки.'
        : 'Нет данных: слово не было угадано самостоятельно или нет записи активного времени.';
      return '<li class="new-daily-statistics__salad-word' + (hasOrder ? '' : ' is-missing') + '">' +
        '<div class="new-daily-statistics__salad-line"><span class="new-daily-statistics__word-pill' + tone + '">' + esc(item.word) + '</span><span class="new-daily-statistics__hint-rate"><i class="ph ph-lightbulb" aria-hidden="true"></i> ' + esc(percent(item.hint_percent)) + '</span></div>' +
        '<div class="new-daily-statistics__complexity"><div class="new-daily-statistics__salad-bar new-daily-statistics__hover-value" data-tooltip="' + esc(complexityTooltip) + '" aria-label="' + esc(complexityTooltip) + '" tabindex="0"><i style="width:' + width + '%"></i></div></div>' +
        '</li>';
    }).join('');
  }
  function ladderWords(items) {
    var words = (items || []).filter(function (item) { return !item.given; });
    var maxSeconds = words.reduce(function (max, item) {
      return Math.max(max, Number(item.median_time_seconds) || 0);
    }, 0);
    return words.map(function (item) {
      var value = Number(item.median_time_seconds);
      var hasTime = item.median_time_seconds != null && item.median_time_seconds !== '' && Number.isFinite(value) && value >= 0;
      var width = hasTime && maxSeconds > 0 ? Math.max(4, Math.round(value / maxSeconds * 100)) : 0;
      var label = hasTime ? seconds(value) : '—';
      var timeTooltip = hasTime
        ? 'Медианное активное время: ' + label + '. От предыдущего успешно разгаданного слова до этого, без пауз.'
        : 'Нет данных: все наблюдения были с подсказкой или без записи активного времени.';
      return '<li class="new-daily-statistics__ladder-word' + (hasTime ? '' : ' is-missing') + '">' +
        '<div class="new-daily-statistics__ladder-line"><span class="new-daily-statistics__word-pill' + (hasTime ? '' : ' is-missing') + '">' + esc(item.word) + '</span><strong>' + esc(label) + '</strong></div>' +
        '<div class="new-daily-statistics__ladder-bar new-daily-statistics__hover-value" data-tooltip="' + esc(timeTooltip) + '" aria-label="' + esc(timeTooltip) + '" tabindex="0"><i style="width:' + width + '%"></i></div>' +
        '</li>';
    }).join('');
  }
  function histogram(items) {
    var rows = items || [];
    if (!rows.length) return '';
    var accessibleRows = rows.map(function (x) {
      var label = String(x.label == null ? x.attempts : x.label);
      var count = Number(x.count) || 0;
      return '<li><span>' + esc(label) + '</span><span>' + esc(count + ' ' + playerWord(count) + ' · ' + percent(x.percent)) + '</span></li>';
    }).join('');
    return '<div class="new-daily-statistics__histogram"><canvas data-attempts-chart role="img" aria-label="Распределение попыток"></canvas><div data-attempts-chart-fallback hidden></div><ul class="new-visually-hidden" data-attempts-chart-data>' + accessibleRows + '</ul></div>';
  }
  function renderHistogramFallback(root, rows) {
    var canvas = root.querySelector('[data-attempts-chart]');
    var fallback = root.querySelector('[data-attempts-chart-fallback]');
    if (!canvas || !fallback) return;
    var max = rows.reduce(function (value, item) { return Math.max(value, Number(item.count) || 0); }, 0);
    var chartWidth = 900;
    var chartHeight = 220;
    var left = 8;
    var bottom = 190;
    var plotHeight = 166;
    var slot = (chartWidth - left * 2) / rows.length;
    var accent = window.getComputedStyle(root).getPropertyValue('--accent').trim() || '#7c3aed';
    var muted = window.getComputedStyle(root).getPropertyValue('--muted').trim() || '#697386';
    var bars = rows.map(function (item, index) {
      var count = Number(item.count) || 0;
      var label = String(item.label == null ? item.attempts : item.label);
      var height = max ? count / max * plotHeight : 0;
      var x = left + index * slot + slot * .2;
      var width = Math.max(2, slot * .6);
      var y = bottom - height;
      var showLabel = index === 0 || index === rows.length - 1 || (Number(item.from) % 5 === 0);
      return '<rect x="' + x.toFixed(2) + '" y="' + y.toFixed(2) + '" width="' + width.toFixed(2) + '" height="' + height.toFixed(2) + '" rx="3" fill="' + esc(accent) + '"><title>' + esc(label + ': ' + count + ' ' + playerWord(count) + ' · ' + percent(item.percent)) + '</title></rect>' +
        (showLabel ? '<text x="' + (x + width / 2).toFixed(2) + '" y="210" text-anchor="middle" fill="' + esc(muted) + '">' + esc(label) + '</text>' : '');
    }).join('');
    fallback.innerHTML = '<svg viewBox="0 0 ' + chartWidth + ' ' + chartHeight + '" preserveAspectRatio="none" role="img" aria-label="Распределение попыток">' +
      '<line x1="' + left + '" y1="' + bottom + '" x2="' + (chartWidth - left) + '" y2="' + bottom + '" stroke="' + esc(muted) + '" stroke-opacity=".35"></line>' + bars + '</svg>';
    canvas.hidden = true;
    fallback.hidden = false;
  }
  function renderHistogram(root, items, attempt) {
    var canvas = root.querySelector('[data-attempts-chart]');
    if (!canvas) return;
    var rows = items || [];
    if (!window.Chart) {
      if ((attempt || 0) < 20) {
        window.setTimeout(function () { renderHistogram(root, rows, (attempt || 0) + 1); }, 250);
      } else {
        renderHistogramFallback(root, rows);
        console.warn('Chart.js unavailable; using the histogram fallback.');
      }
      return;
    }
    if (root.__dailyHistogram) root.__dailyHistogram.destroy();
    var styles = window.getComputedStyle(root);
    var accent = styles.getPropertyValue('--accent').trim() || '#7c3aed';
    var border = styles.getPropertyValue('--border').trim() || '#d8dbe2';
    var muted = styles.getPropertyValue('--muted').trim() || '#697386';
    try {
      root.__dailyHistogram = new window.Chart(canvas, {
      type: 'bar',
      data: {
        labels: rows.map(function (x) { return String(x.label == null ? x.attempts : x.label); }),
        datasets: [{
          data: rows.map(function (x) { return Number(x.count) || 0; }),
          backgroundColor: accent,
          borderRadius: 3,
          borderSkipped: false,
          barPercentage: .62,
          categoryPercentage: .92,
          maxBarThickness: 12
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        layout: { padding: { top: 6, right: 2, bottom: 0, left: 2 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            callbacks: {
              title: function (contexts) {
                var item = rows[contexts[0].dataIndex];
                return String(item.label == null ? item.attempts : item.label);
              },
              label: function (context) {
                var item = rows[context.dataIndex];
                var count = Number(item.count) || 0;
                return count + ' ' + playerWord(count) + ' · ' + percent(item.percent);
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: border },
            ticks: {
              color: muted,
              font: { size: 11 },
              maxRotation: 0,
              autoSkip: false,
              callback: function (value, index) {
                var item = rows[index];
                return index === 0 || index === rows.length - 1 || (item && Number(item.from) % 5 === 0) ? this.getLabelForValue(value) : '';
              }
            }
          },
          y: { beginAtZero: true, display: false, grid: { color: border } }
        }
      }
      });
    } catch (error) {
      renderHistogramFallback(root, rows);
      console.error('Unable to render the attempts histogram.', error);
    }
  }
  function render(root, data) {
    var preservedResult = data.kind === 'salad' ? document.querySelector('[data-raddle-result]') : null;
    var summary = data.summary || {};
    var html = '<h2 class="new-daily-statistics__title">Статистика</h2><div class="new-daily-statistics__summary">' +
      metric('Решили', summary.solved || data.solved || 0);
    if (data.kind === 'alphabet') html += metric('Медиана попыток', summary.median_attempts == null ? '—' : String(summary.median_attempts).replace('.', ',')) + metric('Без подсказок', percent(summary.without_hints_percent));
    else html += metric('Медиана времени', seconds(summary.median_time_seconds)) + metric('Без подсказок', percent(summary.without_hints_percent));
    html += '</div>';
    if (data.kind === 'salad') {
      html += section('Слова', '<ul class="new-daily-statistics__list new-daily-statistics__list--salad">' + saladWords(data.words) + '</ul>');
      if ((data.popular_findings || []).length) html += '<div class="new-daily-statistics__salad-findings">' + section('Популярные находки', '<ul class="new-daily-statistics__list new-daily-statistics__list--guesses">' + popularity(data.popular_findings) + '</ul>') + '</div>';
    } else if (data.kind === 'ladder') {
      if (data.word_stats_available !== false) {
        html += section('Статистика слов', '<p class="new-daily-statistics__hint">Медианное активное время от предыдущего успешно разгаданного слова игрока до этого слова, без пауз. Первое промежуточное слово считается от начала игры.</p><ul class="new-daily-statistics__list new-daily-statistics__list--ladder">' + ladderWords(data.words) + '</ul>');
      }
    } else if (data.kind === 'alphabet') {
      html += '<div class="new-daily-statistics__body">' +
        section('Распределение попыток', histogram(data.distribution)) +
        ((data.guesses || []).length ? section('Популярные догадки', '<ul class="new-daily-statistics__list new-daily-statistics__list--guesses">' + popularity(data.guesses) + '</ul>') : '') +
        '</div>';
    }
    root.innerHTML = html;
    if (data.kind === 'salad') {
      var findings = root.querySelector('.new-daily-statistics__salad-findings');
      if (preservedResult && findings) {
        findings.appendChild(preservedResult);
        preservedResult.classList.add('new-raddle-result--in-statistics');
      } else if (preservedResult && !preservedResult.isConnected) {
        root.appendChild(preservedResult);
      }
    }
    root.hidden = false;
    if (data.kind === 'alphabet') {
      window.requestAnimationFrame(function () { renderHistogram(root, data.distribution); });
    }
  }
  function boot(root) {
    if (root.__dailyStatisticsBooted) return;
    root.__dailyStatisticsBooted = true;
    var requested = false;
    function tryLoad() {
      if (!root.isConnected) return;
      var result = document.querySelector('[data-raddle-result]:not([hidden])');
      if (!result || requested) return;
      requested = true;
      fetch(root.getAttribute('data-statistics-url'), {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
        .then(function (response) { if (!response.ok) throw new Error('statistics unavailable'); return response.json(); })
        .then(function (data) { render(root, data); })
        .catch(function () { requested = false; });
    }
    tryLoad();
    var timer = window.setInterval(tryLoad, 300);
    window.setTimeout(function () { window.clearInterval(timer); }, 30000);
  }
  document.querySelectorAll('[data-daily-statistics]').forEach(boot);
  if (window.MutationObserver) {
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        Array.prototype.forEach.call(mutation.addedNodes, function (node) {
          if (node.nodeType !== 1) return;
          if (node.matches && node.matches('[data-daily-statistics]')) boot(node);
          if (node.querySelectorAll) node.querySelectorAll('[data-daily-statistics]').forEach(boot);
        });
      });
    }).observe(document.body, {childList: true, subtree: true});
  }
}());
