(function () {
  'use strict';

  var config = document.getElementById('censorly-support-config');
  if (!config) return;
  var schedule = JSON.parse(document.getElementById('censorly-schedule').textContent || '[]');
  var random = [];
  var randomLoaded = false;
  var list = document.getElementById('cz-list');
  var randomList = document.getElementById('cz-random-list');
  var randomPanel = document.getElementById('cz-random-panel');
  var busy = false;
  var currentTab = 'schedule';
  var scheduleUi = window.SupportScheduleTabs;
  var scheduleBusy = false;
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
  function get(url) {
    return fetch(url, {credentials: 'same-origin'}).then(function (response) {
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
    var visible = scheduleUi.rowsForTab(rows, currentTab);
    list.innerHTML = visible.length ? visible.map(function (row) {
      var move = row.is_deferred
        ? '<button class="new-btn new-btn--mini new-btn--ghost" data-defer-action="restore" data-link-id="' + row.link_id + '">Вернуть</button>'
        : '<button class="new-btn new-btn--mini new-btn--ghost" data-defer-action="defer" data-link-id="' + row.link_id + '">Отложить</button>';
      var status = row.is_deferred ? 'отложено' : row.is_published ? 'опубликован' : 'черновик';
      return '<div class="support-ladder-item support-schedule-item' + (row.is_published ? ' is-published' : ' is-future') + '" data-link-id="' + row.link_id + '" data-published="' + (row.is_published ? '1' : '0') + '" draggable="' + (!row.is_published && !row.is_deferred) + '">' +
        '<button type="button" class="support-ladder-item__handle support-schedule-handle" ' + (row.is_published || row.is_deferred ? 'disabled ' : '') + 'aria-label="Перетащить выпуск №' + row.number + '" title="Перетащить; стрелки вверх/вниз меняют порядок">⠿</button>' +
        '<div class="support-ladder-item__num">№' + row.number + '</div>' +
        '<div class="support-ladder-item__body"><div class="support-ladder-item__title">' + esc(row.wiki_title || row.name) + '</div>' +
        '<div class="support-ladder-item__meta">' + esc(row.publish_date || '—') + ' · ' + status + '</div></div>' +
        '<div class="support-ladder-item__actions"><a class="new-btn new-btn--mini" href="' + esc(row.play_url) + '" target="_blank" rel="noopener">сайт</a> ' +
        (row.is_published ? '' : '<button class="new-btn new-btn--mini new-btn--ghost support-schedule-jump" data-schedule-jump="top" title="Поставить на завтра" aria-label="Поставить на завтра">↑</button> <button class="new-btn new-btn--mini new-btn--ghost support-schedule-jump" data-schedule-jump="bottom" title="Поставить в конец расписания" aria-label="Поставить в конец расписания">↓</button> ') +
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
  document.querySelectorAll('#cz-tabs [data-tab]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      currentTab = tab.getAttribute('data-tab');
      document.querySelectorAll('#cz-tabs [data-tab]').forEach(function (item) { item.classList.toggle('is-active', item === tab); });
      var isRandom = currentTab === 'random';
      list.closest('.pal-card').hidden = isRandom;
      document.querySelector('.support-ladder-insert').hidden = isRandom;
      randomPanel.hidden = !isRandom;
      if (isRandom && !randomLoaded) run(function () {
        return get(endpoint('random-list')).then(function (data) {
          random = data.rows || [];
          randomLoaded = true;
          renderRandom(random);
        });
      });
      if (!isRandom) renderSchedule(schedule);
    });
  });
  document.getElementById('cz-save-start').onclick = function () { run(function () { return post(endpoint('publish'), {publish_start: document.getElementById('cz-publish-start').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-create').onclick = function () { run(function () { return post(endpoint('create'), {at_number: document.getElementById('cz-at-number').value, title: document.getElementById('cz-slot-title').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-gen-more').onclick = function () { run(function () { return post(endpoint('generate'), {n: document.getElementById('cz-gen-n').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-random').onclick = function () { run(function () { return post(endpoint('random')).then(function (data) { random.unshift(data.row); renderRandom(random); }); }); };
  document.getElementById('cz-title-form').onsubmit = function (event) { event.preventDefault(); run(function () { return post(endpoint('from-title'), {title: document.getElementById('cz-title').value}).then(function (data) { random.unshift(data.row); renderRandom(random); }); }); };
  document.addEventListener('click', function (event) {
    var insertEnd = event.target.closest('[data-insert-end]');
    if (insertEnd) {
      document.getElementById('cz-at-number').value = schedule.filter(function (row) { return !row.is_deferred; }).length + 1;
      document.getElementById('cz-create').click();
      return;
    }
    var button = event.target.closest('[data-action]');
    if (!button) return;
    var body = button.hasAttribute('data-number') ? {number: button.getAttribute('data-number')} : {share_hash: button.getAttribute('data-hash')};
    run(function () { return post(endpoint(button.dataset.action === 'reset' ? 'reset' : 'refetch'), body).then(function () { location.reload(); }); });
  });

  scheduleUi.mountSortable(list, {
    isBusy: function () { return scheduleBusy || currentTab === 'published' || currentTab === 'deferred'; },
    canDrag: function (item) { return item.dataset.published !== '1'; },
    minIndex: function () { return currentTab === 'future' ? 0 : scheduleUi.lastPublishedNumber(schedule); },
    onOrder: function (visibleOrder, meta) {
      var order = scheduleUi.fullOrder(schedule.filter(function (row) { return !row.is_deferred; }), visibleOrder, currentTab);
      scheduleBusy = true;
      scheduleUi.postJson(endpoint('reorder'), {order: order}).then(function (data) {
        schedule = data.rows || [];
        renderSchedule(schedule);
        if (meta.keyboard) {
          var moved = list.querySelector('[data-link-id="' + meta.movedId + '"] .support-schedule-handle');
          if (moved) moved.focus();
        }
      }).catch(function (error) {
        document.getElementById('cz-error').hidden = false;
        document.getElementById('cz-error').textContent = error.message;
      }).finally(function () { scheduleBusy = false; });
    }
  });
  scheduleUi.mountDeferredActions(list, function (rows) {
    schedule = rows;
    document.getElementById('cz-meta').textContent = scheduleUi.scheduleMeta(rows.filter(function (row) { return !row.is_deferred; }));
    renderSchedule(schedule);
  });
})();
