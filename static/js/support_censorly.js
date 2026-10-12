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
  var suggestUrl = endpoint('suggest');
  var createModal = document.getElementById('cz-create-modal');
  var createTitle = document.getElementById('cz-end-title');
  var createError = document.getElementById('cz-end-error');

  function attachTitleSuggestions(input, listbox) {
    var timer = null;
    var controller = null;
    var activeIndex = -1;
    var requestId = 0;
    function close() {
      requestId += 1;
      clearTimeout(timer);
      if (controller) controller.abort();
      controller = null;
      listbox.hidden = true;
      listbox.innerHTML = '';
      listbox.removeAttribute('aria-busy');
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      activeIndex = -1;
    }
    function loading() {
      activeIndex = -1;
      input.removeAttribute('aria-activedescendant');
      listbox.innerHTML = '<div class="support-censorly-suggestions__loading"><span class="support-censorly-suggestions__spinner" aria-hidden="true"></span><span>Загружается…</span></div>';
      listbox.hidden = false;
      listbox.setAttribute('aria-busy', 'true');
      input.setAttribute('aria-expanded', 'true');
    }
    function select(title) {
      input.value = title;
      close();
      input.focus();
    }
    function show(titles) {
      if (!titles.length) { close(); return; }
      activeIndex = -1;
      listbox.innerHTML = titles.map(function (title, index) {
        return '<button type="button" id="' + listbox.id + '-option-' + index + '" role="option" tabindex="-1" aria-selected="false" class="support-autocomplete-option" data-title-index="' + index + '">' + esc(title) + '</button>';
      }).join('');
      listbox.hidden = false;
      listbox.removeAttribute('aria-busy');
      input.setAttribute('aria-expanded', 'true');
    }
    function activate(index) {
      var options = listbox.querySelectorAll('[role="option"]');
      if (!options.length) return;
      activeIndex = (index + options.length) % options.length;
      options.forEach(function (option, i) { option.setAttribute('aria-selected', i === activeIndex ? 'true' : 'false'); });
      input.setAttribute('aria-activedescendant', options[activeIndex].id);
      options[activeIndex].scrollIntoView({block: 'nearest'});
    }
    input.addEventListener('input', function () {
      var query = input.value.trim();
      clearTimeout(timer);
      if (controller) controller.abort();
      controller = null;
      requestId += 1;
      if (query.length < 2) { close(); return; }
      var currentRequest = requestId;
      loading();
      timer = setTimeout(function () {
        controller = new AbortController();
        var url = new URL(suggestUrl, window.location.href);
        url.searchParams.set('q', query);
        get(url.toString(), {signal: controller.signal}).then(function (data) {
          if (requestId !== currentRequest || input.value.trim() !== query) return;
          show(data.titles || []);
        }).catch(function (error) {
          if (requestId !== currentRequest || error.name === 'AbortError' || input.value.trim() !== query) return;
          activeIndex = -1;
          listbox.innerHTML = '<div class="support-censorly-suggestions__error">Не удалось загрузить подсказки</div>';
          listbox.removeAttribute('aria-busy');
        });
      }, 250);
    });
    input.addEventListener('keydown', function (event) {
      var count = listbox.querySelectorAll('[role="option"]').length;
      if (event.key === 'ArrowDown' && !listbox.hidden) { event.preventDefault(); activate(activeIndex + 1); }
      else if (event.key === 'ArrowUp' && !listbox.hidden) { event.preventDefault(); activate(activeIndex <= 0 ? count - 1 : activeIndex - 1); }
      else if (event.key === 'Enter' && activeIndex >= 0 && !listbox.hidden) {
        event.preventDefault();
        var option = listbox.querySelectorAll('[role="option"]')[activeIndex];
        if (option) select(option.textContent);
      } else if (event.key === 'Escape' && !listbox.hidden) { event.stopPropagation(); close(); }
    });
    listbox.addEventListener('click', function (event) {
      var option = event.target.closest('[role="option"]');
      if (option) select(option.textContent);
    });
    document.addEventListener('click', function (event) {
      if (event.target !== input && !listbox.contains(event.target)) close();
    });
  }

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
  function get(url, options) {
    options = options || {};
    options.credentials = 'same-origin';
    return fetch(url, options).then(function (response) {
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
      var generateButton = document.getElementById('cz-gen-more');
      if (generateButton.disabled) {
        generateButton.disabled = false;
        generateButton.textContent = 'Нагенерить';
        var generateStatus = document.getElementById('cz-gen-status');
        generateStatus.textContent = error.message;
        return;
      }
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
  attachTitleSuggestions(document.getElementById('cz-slot-title'), document.getElementById('cz-slot-suggestions'));
  attachTitleSuggestions(createTitle, document.getElementById('cz-end-suggestions'));
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
  document.getElementById('cz-create').onclick = function () {
    var title = document.getElementById('cz-slot-title').value.trim();
    var error = document.getElementById('cz-create-error');
    error.hidden = Boolean(title);
    error.textContent = title ? '' : 'Выберите или укажите статью. Для случайных статей используйте «Нагенерить». ';
    if (!title) return;
    run(function () { return post(endpoint('create'), {at_number: document.getElementById('cz-at-number').value, title: title}).then(function () { location.reload(); }); });
  };
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
  document.getElementById('cz-gen-more').onclick = function () {
    var button = document.getElementById('cz-gen-more');
    var status = document.getElementById('cz-gen-status');
    run(function () {
      status.textContent = '';
      button.disabled = true;
      button.textContent = 'Генерируем…';
      status.innerHTML = '<span class="support-censorly-suggestions__spinner" aria-hidden="true"></span> Загружаем статьи из Википедии';
      return post(endpoint('generate'), {n: document.getElementById('cz-gen-n').value}).then(function () { location.reload(); });
    });
  };
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
