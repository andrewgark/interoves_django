(function () {
  'use strict';

  var config = document.getElementById('censorly-support-config');
  if (!config) return;
  var schedule = JSON.parse(document.getElementById('censorly-schedule').textContent || '[]');
  var random = JSON.parse(document.getElementById('censorly-random').textContent || '[]');
  var list = document.getElementById('cz-list');
  var randomList = document.getElementById('cz-random-list');
  var busy = false;
  var currentTab = 'schedule';
  var endpoint = function (name) { return config.getAttribute('data-' + name + '-url'); };

  function csrf() {
    var input = document.querySelector('input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      body: JSON.stringify(body || {}),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || data.ok === false) throw new Error(data.error || 'Ошибка');
        return data;
      });
    });
  }
  function esc(value) {
    return String(value || '').replace(/[&<>"']/g, function (char) {
      return {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char];
    });
  }
  function actions(kind, value) {
    var attr = kind === 'number'
      ? 'data-number="' + esc(value) + '"'
      : 'data-hash="' + esc(value) + '"';
    return '<button class="new-btn new-btn--mini new-btn--ghost" ' + attr + ' data-action="refetch">Скачать текст заново</button>' +
      ' <button class="new-btn new-btn--mini new-btn--ghost" ' + attr + ' data-action="reset">Сбросить мой прогресс</button>';
  }
  function renderSchedule(rows) {
    var visible = rows.filter(function (row) {
      if (currentTab === 'published') return row.is_published;
      if (currentTab === 'future') return !row.is_published && !row.is_deferred;
      if (currentTab === 'deferred') return row.is_deferred;
      return true;
    });
    list.innerHTML = visible.length ? visible.map(function (row) {
      var move = row.is_deferred
        ? '<button class="new-btn new-btn--mini new-btn--ghost" data-defer-action="restore" data-link-id="' + row.link_id + '">Вернуть</button>'
        : '<button class="new-btn new-btn--mini new-btn--ghost" data-defer-action="defer" data-link-id="' + row.link_id + '">Отложить</button>';
      var status = row.is_deferred ? 'отложено' : row.is_published ? 'опубликован' : 'черновик';
      return '<div class="support-ladder-item"><div class="support-ladder-item__num">№' + row.number + '</div>' +
        '<div class="support-ladder-item__body"><div class="support-ladder-item__title">' + esc(row.wiki_title || row.name) + '</div>' +
        '<div class="support-ladder-item__meta">' + esc(row.publish_date || '—') + ' · ' + status + ' · ' + row.token_count + ' токенов</div></div>' +
        '<div class="support-ladder-item__actions"><a class="new-btn new-btn--mini" href="' + esc(row.play_url) + '" target="_blank" rel="noopener">сайт</a> ' +
        move + ' ' + actions('number', row.number) + '</div></div>';
    }).join('') : '<p class="support-empty">Расписание пусто.</p>';
  }
  function renderRandom(rows) {
    randomList.innerHTML = rows.length ? rows.map(function (row) {
      return '<div class="support-ladder-item"><div class="support-ladder-item__body"><div class="support-ladder-item__title">' + esc(row.wiki_title) + '</div>' +
        '<div class="support-ladder-item__meta"><code>' + esc(row.share_hash) + '</code> · ' + row.token_count + ' токенов · ' + esc(row.created_at) + '</div></div>' +
        '<div class="support-ladder-item__actions"><a class="new-btn new-btn--mini" href="' + esc(row.play_url) + '" target="_blank" rel="noopener">сайт</a> ' +
        actions('hash', row.share_hash) + '</div></div>';
    }).join('') : '<p class="support-empty">Пока пусто.</p>';
  }
  function run(work) {
    if (busy) return;
    busy = true;
    work().catch(function (error) {
      var node = document.getElementById('cz-error');
      node.hidden = false;
      node.textContent = error.message;
    }).finally(function () { busy = false; });
  }

  renderSchedule(schedule);
  renderRandom(random);
  document.querySelectorAll('#cz-tabs [data-tab]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      currentTab = tab.getAttribute('data-tab');
      document.querySelectorAll('#cz-tabs [data-tab]').forEach(function (item) { item.classList.toggle('is-active', item === tab); });
      renderSchedule(schedule);
    });
  });
  document.getElementById('cz-save-start').onclick = function () { run(function () { return post(endpoint('publish'), {publish_start: document.getElementById('cz-publish-start').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-create').onclick = function () { run(function () { return post(endpoint('create'), {at_number: document.getElementById('cz-at-number').value, title: document.getElementById('cz-slot-title').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-gen-more').onclick = function () { run(function () { return post(endpoint('generate'), {n: document.getElementById('cz-gen-n').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-random').onclick = function () { run(function () { return post(endpoint('random')).then(function (data) { random.unshift(data.row); renderRandom(random); }); }); };
  document.getElementById('cz-title-form').onsubmit = function (event) { event.preventDefault(); run(function () { return post(endpoint('from-title'), {title: document.getElementById('cz-title').value}).then(function (data) { random.unshift(data.row); renderRandom(random); }); }); };
  document.addEventListener('click', function (event) {
    var move = event.target.closest('[data-defer-action]');
    if (move) {
      var moveUrl = list.getAttribute(move.dataset.deferAction === 'restore' ? 'data-restore-url' : 'data-defer-url');
      run(function () { return post(moveUrl.replace('/0/', '/' + encodeURIComponent(move.dataset.linkId) + '/')).then(function () { location.reload(); }); });
      return;
    }
    var button = event.target.closest('[data-action]');
    if (!button) return;
    var body = button.hasAttribute('data-number') ? {number: button.getAttribute('data-number')} : {share_hash: button.getAttribute('data-hash')};
    run(function () { return post(endpoint(button.dataset.action === 'reset' ? 'reset' : 'refetch'), body).then(function () { location.reload(); }); });
  });
})();
