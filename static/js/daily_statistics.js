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
  function popularity(items) {
    return (items || []).map(function (x) {
      return '<li><span>' + esc(x.word) + '</span><strong>' + esc(x.players) + ' игроков</strong></li>';
    }).join('');
  }
  function render(root, data) {
    var summary = data.summary || {};
    var html = '<h2>Статистика</h2><div class="new-daily-statistics__summary">' +
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
      html += section('Распределение попыток', '<ul class="new-daily-statistics__histogram">' + (data.distribution || []).map(function (x) { return '<li><span>' + esc(x.attempts) + '</span><i style="--bar:' + Math.max(0, Math.min(100, x.percent || 0)) + '%"></i><strong>' + esc(String(x.percent).replace('.', ',') + '%') + '</strong></li>'; }).join('') + '</ul>');
      if ((data.guesses || []).length) html += section('Популярные догадки', '<ul class="new-daily-statistics__list">' + popularity(data.guesses) + '</ul>');
    }
    root.innerHTML = html;
    root.hidden = false;
  }
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
