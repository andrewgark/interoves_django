(function () {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function seconds(value) {
    if (value == null) return '—';
    var n = Math.round(Number(value));
    return Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0');
  }
  function metric(label, value) {
    return '<div class="new-daily-statistics__metric"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></div>';
  }
  function rows(items, type) {
    return (items || []).map(function (item) {
      var value = type === 'salad'
        ? (item.median_order == null ? '—' : String(item.median_order).replace('.', ',') + '-е') + ' · ' + seconds(item.median_time_seconds) + (item.hint_percent > 0 ? ' · подсказка ' + String(item.hint_percent).replace('.', ',') + '%' : '')
        : (item.median_time_seconds == null ? '—' : seconds(item.median_time_seconds));
      return '<li><span>' + esc(item.word) + '</span><strong>' + esc(value) + '</strong></li>';
    }).join('');
  }
  function section(title, content) {
    return content ? '<section class="new-daily-statistics__section"><h3>' + esc(title) + '</h3>' + content + '</section>' : '';
  }
  function percent(value) {
    return String(value == null ? 0 : value).replace('.', ',') + '%';
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
      return '<li class="new-daily-statistics__guess"><span>' + esc(x.word) + '</span><strong>' + esc(x.players) + '</strong></li>';
    }).join('');
  }
  function histogram(items) {
    var rows = items || [];
    if (!rows.length) return '';
    var html = '<div class="new-daily-statistics__histogram" role="list" aria-label="Распределение попыток" style="--histogram-columns:' + Math.max(1, rows.length) + '">';
    rows.forEach(function (x, index) {
      var count = Number(x.count) || 0;
      var label = String(x.label == null ? x.attempts : x.label);
      var attempts = label + ' ' + (x.to == null ? 'попыток' : attemptWord(x.from));
      var tooltip = attempts + '\n' + count + ' ' + playerWord(count) + '\n' + percent(x.percent);
      var showAxis = index === 0 || x.to == null || (Number(x.from) % 5 === 0);
      html += '<div class="new-daily-statistics__histogram-item" role="listitem">' +
        '<button type="button" class="new-daily-statistics__bar" data-histogram-bar aria-label="' + esc(tooltip.replace(/\n/g, ', ')) + '" style="--bar-height:' + Math.max(0, Math.min(100, Number(x.bar_percent) || 0)) + '%"' + (count ? ' data-nonzero="true"' : '') + '>' +
          '<span class="new-daily-statistics__bar-fill"></span><span class="new-daily-statistics__tooltip" role="tooltip">' + esc(tooltip).replace(/\n/g, '<br>') + '</span>' +
        '</button><span class="new-daily-statistics__axis-label' + (showAxis ? '' : ' is-hidden') + '">' + esc(label) + '</span></div>';
    });
    return html + '</div>';
  }
  function render(root, data) {
    var summary = data.summary || {};
    var html = '<h2 class="new-daily-statistics__title">Статистика</h2><div class="new-daily-statistics__summary">' +
      metric('Решили', summary.solved || data.solved || 0);
    if (data.kind === 'alphabet') html += metric('Медиана попыток', summary.median_attempts == null ? '—' : String(summary.median_attempts).replace('.', ','));
    else html += metric('Медиана времени', seconds(summary.median_time_seconds)) + metric('Без подсказок', (summary.without_hints_percent || 0) + '%');
    html += '</div>';
    if (data.kind === 'salad') {
      html += section('Слова', '<ul class="new-daily-statistics__list">' + rows(data.words, 'salad') + '</ul>');
      if ((data.rare || []).length) html += section('Редкие находки', '<ul class="new-daily-statistics__list">' + popularity(data.rare) + '</ul>');
      if ((data.off_topic || []).length) html += section('Не по теме', '<ul class="new-daily-statistics__list">' + popularity(data.off_topic) + '</ul>');
    } else if (data.kind === 'ladder') {
      html += section('Статистика слов', '<ul class="new-daily-statistics__list new-daily-statistics__list--ladder">' + (data.words || []).map(function (x) { return '<li class="' + (x.given ? 'is-given' : '') + '"><span>' + esc(x.word) + '</span><strong>' + (x.given ? 'дано' : esc(seconds(x.median_time_seconds))) + '</strong></li>'; }).join('') + '</ul>');
    } else if (data.kind === 'alphabet') {
      html += '<div class="new-daily-statistics__body">' +
        section('Распределение попыток', histogram(data.distribution)) +
        ((data.guesses || []).length ? section('Популярные догадки', '<ul class="new-daily-statistics__list new-daily-statistics__list--guesses">' + popularity(data.guesses) + '</ul>') : '') +
        '</div>';
    }
    root.innerHTML = html;
    root.hidden = false;
  }
  document.addEventListener('click', function (event) {
    var bar = event.target.closest && event.target.closest('[data-histogram-bar]');
    document.querySelectorAll('[data-histogram-bar].is-tooltip-open').forEach(function (item) {
      if (item !== bar) item.classList.remove('is-tooltip-open');
    });
    if (bar) bar.classList.toggle('is-tooltip-open');
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('[data-histogram-bar].is-tooltip-open').forEach(function (item) {
      item.classList.remove('is-tooltip-open');
    });
  });
  function boot(root) {
    var requested = false;
    function tryLoad() {
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
}());
