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
  var offersPanel = document.getElementById('cz-offers-panel');
  var offersList = document.getElementById('cz-offers-list');
  var offersLoaded = false;
  var busy = false;
  var currentTab = 'schedule';
  var scheduleUi = window.SupportScheduleTabs;
  var scheduleBusy = false;
  var endpoint = function (name) { return config.getAttribute('data-' + name + '-url'); };
  var createModal = document.getElementById('cz-create-modal');
  var createTitle = document.getElementById('cz-end-title');
  var createError = document.getElementById('cz-end-error');

  function closeCreateModal() {
    createModal.classList.remove('is-open');
    createModal.hidden = true;
    document.body.classList.remove('new-modal-open');
  }
  function openCreateModal() {
    createError.hidden = true;
    createError.textContent = '';
    createTitle.value = '';
    createModal.hidden = false;
    createModal.classList.add('is-open');
    document.body.classList.add('new-modal-open');
    createTitle.focus();
  }

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
  function renderOffers(rows) {
    offersList.innerHTML = rows.length ? rows.map(function (row) {
      var actionsHtml = row.status === 'sent'
        ? '<button class="new-btn new-btn--mini" data-offer-accept="' + row.id + '">В расписание</button> <button class="new-btn new-btn--mini new-btn--ghost" data-offer-reject="' + row.id + '">Не брать</button>'
        : '';
      return '<div class="support-ladder-item"><div class="support-ladder-item__num">' + esc(row.accepted_number ? '№' + row.accepted_number : '#' + row.id) + '</div>' +
        '<div class="support-ladder-item__body"><div class="support-ladder-item__title">' + esc(row.wiki_title) + ' <span class="support-flag">' + esc(row.status_label) + '</span></div>' +
        '<div class="support-ladder-item__meta">' + esc(row.user_name) + (row.telegram_handle ? ' · @' + esc(row.telegram_handle) : '') + ' · ' + esc(row.created_at) + '</div>' +
        (row.comment ? '<div class="support-ladder-item__preview">' + esc(row.comment) + '</div>' : '') + '</div>' +
        '<div class="support-ladder-item__actions"><a class="new-btn new-btn--mini new-btn--ghost" href="' + esc(row.play_url) + '" target="_blank" rel="noopener">превью</a> ' + actionsHtml + '</div></div>';
    }).join('') : '<p class="support-empty">Пока нет предложений.</p>';
  }
  function run(work) {
    if (busy) return;
    busy = true;
    work().catch(function (error) {
      if (!createModal.hidden) {
        createError.hidden = false;
        createError.textContent = error.message;
        return;
      }
      var node = currentTab === 'offers'
        ? document.getElementById('cz-offers-error')
        : document.getElementById('cz-error');
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
      var isOffers = currentTab === 'offers';
      var isAux = isRandom || isOffers;
      list.closest('.pal-card').hidden = isAux;
      document.querySelector('.support-ladder-insert').hidden = isAux;
      randomPanel.hidden = !isRandom;
      offersPanel.hidden = !isOffers;
      if (isRandom && !randomLoaded) run(function () {
        return get(endpoint('random-list')).then(function (data) {
          random = data.rows || [];
          randomLoaded = true;
          renderRandom(random);
        });
      });
      if (isOffers && !offersLoaded) run(function () {
        return get(endpoint('offers')).then(function (data) {
          offersLoaded = true;
          renderOffers(data.rows || []);
        }).catch(function (error) {
          var node = document.getElementById('cz-offers-error');
          node.hidden = false;
          node.textContent = error.message;
          throw error;
        });
      });
      if (!isAux) renderSchedule(schedule);
    });
  });
  document.getElementById('cz-save-start').onclick = function () { run(function () { return post(endpoint('publish'), {publish_start: document.getElementById('cz-publish-start').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-create').onclick = function () { run(function () { return post(endpoint('create'), {at_number: document.getElementById('cz-at-number').value, title: document.getElementById('cz-slot-title').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-end-create').onclick = function () {
    var title = createTitle.value.trim();
    if (!title) {
      createError.textContent = 'Укажите название статьи или ссылку на Википедию.';
      createError.hidden = false;
      createTitle.focus();
      return;
    }
    run(function () {
      return post(endpoint('create'), {
        at_number: schedule.filter(function (row) { return !row.is_deferred; }).length + 1,
        title: title,
      }).then(function () { location.reload(); });
    });
  };
  document.getElementById('cz-gen-more').onclick = function () { run(function () { return post(endpoint('generate'), {n: document.getElementById('cz-gen-n').value}).then(function () { location.reload(); }); }); };
  document.getElementById('cz-random').onclick = function () { run(function () { return post(endpoint('random')).then(function (data) { random.unshift(data.row); renderRandom(random); }); }); };
  document.getElementById('cz-title-form').onsubmit = function (event) { event.preventDefault(); run(function () { return post(endpoint('from-title'), {title: document.getElementById('cz-title').value}).then(function (data) { random.unshift(data.row); renderRandom(random); }); }); };
  document.addEventListener('click', function (event) {
    var insertEnd = event.target.closest('[data-insert-end]');
    if (insertEnd) {
      openCreateModal();
      return;
    }
    if (event.target.closest('[data-cz-create-close]')) closeCreateModal();
    var button = event.target.closest('[data-action]');
    if (button) {
      var body = button.hasAttribute('data-number') ? {number: button.getAttribute('data-number')} : {share_hash: button.getAttribute('data-hash')};
      run(function () { return post(endpoint(button.dataset.action === 'reset' ? 'reset' : 'refetch'), body).then(function () { location.reload(); }); });
      return;
    }
    var accept = event.target.closest('[data-offer-accept]');
    var reject = event.target.closest('[data-offer-reject]');
    var offerButton = accept || reject;
    if (!offerButton) return;
    var offerId = offerButton.getAttribute(accept ? 'data-offer-accept' : 'data-offer-reject');
    var urlTemplate = endpoint(accept ? 'offer-accept' : 'offer-reject');
    var url = urlTemplate.replace('/0/', '/' + encodeURIComponent(offerId) + '/');
    run(function () { return post(url, {}).then(function () { location.reload(); }); });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !createModal.hidden) closeCreateModal();
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
