(function () {
  'use strict';

  var support = window.SupportScheduleTabs;
  var list = document.getElementById('word-salad-list');
  var bootstrap = document.getElementById('word-salad-bootstrap');
  var config = document.getElementById('word-salad-config');
  if (!support || !list || !bootstrap || !config) return;

  var Editor = window.WordSaladGridEditor;
  var rows = JSON.parse(bootstrap.textContent || '[]');
  var sentOffers = JSON.parse((document.getElementById('word-salad-offers-bootstrap') || {}).textContent || '[]');
  var offerByLink = JSON.parse((document.getElementById('word-salad-offer-by-link-bootstrap') || {}).textContent || '{}');
  var viewTab = 'schedule';
  var busy = false;
  var editLinkId = null;
  var editOfferId = null;
  var scheduleGrid = null;
  var offerGrid = null;
  var endpoints = {
    create: config.dataset.createUrl,
    reorder: config.dataset.reorderUrl,
    publishStart: config.dataset.publishStartUrl,
    detail: config.dataset.detailUrl,
    save: config.dataset.saveUrl,
    recheck: config.dataset.recheckUrl,
    remove: config.dataset.deleteUrl,
    offerDetail: config.dataset.offerDetailUrl,
    offerSave: config.dataset.offerSaveUrl,
    offerAccept: config.dataset.offerAcceptUrl,
    offerRevision: config.dataset.offerRevisionUrl,
    offerReset: config.dataset.offerResetUrl,
    linkRevision: config.dataset.linkRevisionUrl
  };
  var modalElement = document.getElementById('word-salad-edit-modal');
  var modal = support.mountModal(modalElement, {
    closeSelector: '[data-word-salad-close]',
    initialFocus: '#word-salad-edit-intro',
    onClose: function () { editLinkId = null; }
  });
  var offerModalElement = document.getElementById('word-salad-offer-modal');
  var offerModal = support.mountModal(offerModalElement, {
    closeSelector: '[data-word-salad-offer-close]',
    initialFocus: '#word-salad-offer-theme',
    onClose: function () { editOfferId = null; }
  });

  function endpoint(template, id) {
    return support.endpoint(template, id);
  }

  function isSentView() {
    return viewTab === 'sent';
  }

  function canReorder() {
    return !isSentView() && (viewTab === 'schedule' || viewTab === 'future');
  }

  function setBusy(value) {
    busy = !!value;
    list.classList.toggle('is-busy', busy);
    document.querySelectorAll('[data-insert-end], #word-salad-edit-save, #word-salad-edit-recheck, #word-salad-edit-delete, #word-salad-offer-save, #word-salad-offer-accept').forEach(function (button) {
      button.disabled = busy;
    });
  }

  function showError(message) {
    var error = document.getElementById('word-salad-edit-error');
    error.textContent = message || 'Не удалось сохранить';
    error.hidden = false;
  }

  function clearError() {
    var error = document.getElementById('word-salad-edit-error');
    error.textContent = '';
    error.hidden = true;
  }

  function showOfferError(message) {
    var error = document.getElementById('word-salad-offer-error');
    error.textContent = message || 'Не удалось сохранить';
    error.hidden = false;
  }

  function clearOfferError() {
    var error = document.getElementById('word-salad-offer-error');
    error.textContent = '';
    error.hidden = true;
  }

  function setRows(nextRows) {
    rows = (nextRows || []).slice().sort(function (a, b) { return a.number - b.number; });
    render();
  }

  function setOffers(nextOffers) {
    sentOffers = nextOffers || [];
    render();
  }

  function upsertOfferMap(offer) {
    if (!offer || !offer.accepted_link_id) return;
    offerByLink[String(offer.accepted_link_id)] = offer;
  }

  function updateValidation(statusId, gridApi, wordsId, rareId) {
    var status = document.getElementById(statusId);
    if (!Editor || !status) return;
    var rareField = rareId ? document.getElementById(rareId) : null;
    var result = Editor.validateLive(
      gridApi ? gridApi.getGrid() : [],
      document.getElementById(wordsId).value,
      rareField ? rareField.value : ''
    );
    if (gridApi) gridApi.highlightRemovable(result.removableCells);
    if (!result.errors.length) {
      status.hidden = false;
      status.className = 'ws-grid-editor__status is-ok';
      status.textContent = 'Сетка собирается.';
      return;
    }
    status.hidden = false;
    status.className = 'ws-grid-editor__status is-error';
    status.textContent = result.errors[0] + (result.missingWords.length ? ' Не находятся: ' + result.missingWords.join(', ') + '.' : '');
  }

  function ensureScheduleGrid(gridText) {
    var host = document.getElementById('word-salad-edit-grid');
    if (scheduleGrid) scheduleGrid.destroy();
    scheduleGrid = Editor.mount(host, {
      grid: gridText || '',
      onChange: function () {
        updateValidation('word-salad-edit-validation', scheduleGrid, 'word-salad-edit-words', 'word-salad-edit-rare-words');
      }
    });
  }

  function ensureOfferGrid(gridText) {
    var host = document.getElementById('word-salad-offer-grid');
    if (offerGrid) offerGrid.destroy();
    offerGrid = Editor.mount(host, {
      grid: gridText || '',
      onChange: function () {
        updateValidation('word-salad-offer-validation', offerGrid, 'word-salad-offer-words');
      }
    });
  }

  function insertButton(number) {
    var insert = document.createElement('div');
    insert.className = 'support-ladder-insert';
    insert.innerHTML = '<button type="button" class="support-ladder-insert__btn" data-insert-at="' +
      number + '" title="Вставить перед №' + number + '" aria-label="Вставить перед выпуском №' + number + '">+</button>';
    return insert;
  }

  function renderSent() {
    list.innerHTML = '';
    list.classList.add('support-schedule-readonly');
    document.getElementById('word-salad-meta').textContent = 'Отправленных салатиков: ' + sentOffers.length;
    document.querySelectorAll('[data-insert-end]').forEach(function (button) {
      button.closest('.support-ladder-insert').hidden = true;
    });
    if (!sentOffers.length) {
      list.innerHTML = '<p class="support-empty">Нет отправленных салатиков.</p>';
      return;
    }
    sentOffers.slice().sort(function (a, b) {
      return String(b.sent_at || '').localeCompare(String(a.sent_at || ''));
    }).forEach(function (offer) {
      var item = document.createElement('div');
      item.className = 'support-ladder-item support-schedule-item';
      var preview = offer.kind === 'idea'
        ? (offer.idea_text || offer.comment || '')
        : (offer.comment || offer.words_text || '');
      var play = offer.play_url
        ? '<a class="new-btn new-btn--mini new-btn--ghost" href="' + support.escapeHtml(offer.play_url) + '" target="_blank" rel="noopener">превью</a>'
        : '';
      item.innerHTML =
        '<div class="support-ladder-item__num">#' + offer.id + '</div>' +
        '<div class="support-ladder-item__body">' +
          '<div class="support-ladder-item__title">' + support.escapeHtml(offer.theme || 'Салатик') +
            ' <span class="support-flag">' + support.escapeHtml(offer.kind_label || '') + '</span>' +
            ' <span class="support-flag">' + support.escapeHtml(offer.status_label || '') + '</span></div>' +
          '<div class="support-ladder-item__meta">' +
            support.escapeHtml((offer.sent_at || offer.updated_at || '').slice(0, 10) || '—') +
            ' · ' + support.escapeHtml(offer.user_name || '') +
            (offer.telegram_handle ? ' · @' + support.escapeHtml(offer.telegram_handle) : '') +
          '</div>' +
          '<div class="support-ladder-item__preview">' + support.escapeHtml(preview) + '</div>' +
        '</div>' +
        '<div class="support-ladder-item__actions">' +
          '<button type="button" class="new-btn new-btn--mini" data-offer-edit="' + offer.id + '">править</button>' +
          play +
        '</div>';
      list.appendChild(item);
    });
  }

  function render() {
    if (isSentView()) {
      renderSent();
      return;
    }
    var visibleRows = support.rowsForTab(rows, viewTab);
    list.innerHTML = '';
    list.classList.toggle('support-schedule-readonly', !canReorder());
    document.getElementById('word-salad-meta').textContent = support.scheduleMeta(rows);
    document.querySelectorAll('[data-insert-end]').forEach(function (button) {
      button.closest('.support-ladder-insert').hidden = !canReorder();
    });
    if (!visibleRows.length) {
      list.innerHTML = '<p class="support-empty">В этой вкладке пока нет выпусков.</p>';
      return;
    }
    visibleRows.forEach(function (row) {
      if (!row.is_published && canReorder()) list.appendChild(insertButton(row.number));
      var linkedOffer = offerByLink[String(row.link_id)];
      var item = document.createElement('div');
      item.className = 'support-ladder-item support-schedule-item' + (row.is_published ? ' is-published' : ' is-future');
      item.draggable = !row.is_published && canReorder();
      item.dataset.linkId = String(row.link_id);
      item.dataset.published = row.is_published ? '1' : '0';
      var revisionBtn = (linkedOffer && linkedOffer.status !== 'draft')
        ? '<button type="button" class="new-btn new-btn--mini new-btn--ghost" data-revision-link="' + row.link_id + '">на доработку</button>'
        : '';
      item.innerHTML =
        '<button type="button" class="support-ladder-item__handle support-schedule-handle" ' +
          (row.is_published || !canReorder() ? 'disabled ' : '') +
          'aria-label="Перетащить выпуск №' + row.number + '" title="Перетащить; стрелки вверх/вниз меняют порядок">⠿</button>' +
        '<div class="support-ladder-item__num">№' + row.number + '</div>' +
        '<div class="support-ladder-item__body">' +
          '<div class="support-ladder-item__title">' + support.escapeHtml('Салатик #' + row.number) +
            ' <span class="support-flag ' + support.statusClass(row) + '">' + support.statusLabel(row) + '</span>' +
            (linkedOffer ? ' <span class="support-flag">автор</span>' : '') +
            '</div>' +
          '<div class="support-ladder-item__meta"><span class="support-cell-mono">' +
            support.escapeHtml(row.grid_preview || '—') + '</span> · id ' + row.link_id +
            ' · ' + row.words_count + ' сл.' +
            (row.publish_date ? ' · ' + support.escapeHtml(row.publish_date) : '') + '</div>' +
          '<div class="support-ladder-item__preview">' + support.escapeHtml(row.words_preview || '—') + '</div>' +
        '</div>' +
        '<div class="support-ladder-item__actions">' +
          '<button type="button" class="new-btn new-btn--mini" data-edit="' + row.link_id + '">править</button>' +
          '<a class="new-btn new-btn--mini new-btn--ghost" href="' + support.escapeHtml(row.preview_url) + '" target="_blank" rel="noopener">сайт</a>' +
          revisionBtn +
          (!row.is_published ? '<button type="button" class="new-btn new-btn--mini new-btn--ghost support-item-delete" data-delete="' + row.link_id + '">удалить</button>' : '') +
        '</div>';
      list.appendChild(item);
    });
  }

  function fillEditor(item) {
    editLinkId = item.link_id;
    document.getElementById('word-salad-edit-sub').textContent =
      '№' + item.number + ' · id ' + item.link_id + ' · ' + item.words_count + ' сл.';
    document.getElementById('word-salad-edit-intro').value = item.intro || '';
    document.getElementById('word-salad-edit-words').value = item.words_text || '';
    document.getElementById('word-salad-edit-rare-words').value = item.rare_words_text || '';
    document.getElementById('word-salad-edit-preview').href = item.preview_url || '#';
    var row = rows.find(function (candidate) { return candidate.link_id === item.link_id; });
    document.getElementById('word-salad-edit-delete').hidden = !!(row && row.is_published);
    ensureScheduleGrid(item.grid_text || '');
    updateValidation('word-salad-edit-validation', scheduleGrid, 'word-salad-edit-words', 'word-salad-edit-rare-words');
    clearError();
  }

  function fillOfferEditor(offer) {
    editOfferId = offer.id;
    document.getElementById('word-salad-offer-title').textContent =
      offer.kind === 'idea' ? 'Отправленная идея' : 'Отправленный салатик';
    document.getElementById('word-salad-offer-sub').textContent =
      (offer.user_name || '') + (offer.telegram_handle ? ' · @' + offer.telegram_handle : '');
    document.getElementById('word-salad-offer-theme').value = offer.theme || '';
    document.getElementById('word-salad-offer-idea').value = offer.idea_text || '';
    document.getElementById('word-salad-offer-suggested').value = offer.suggested_words || '';
    document.getElementById('word-salad-offer-words').value = offer.words_text || '';
    document.getElementById('word-salad-offer-comment').value = offer.comment || '';
    document.getElementById('word-salad-offer-admin-note').value = offer.admin_note || '';
    document.getElementById('word-salad-offer-idea-fields').hidden = offer.kind !== 'idea';
    document.getElementById('word-salad-offer-full-fields').hidden = offer.kind === 'idea';
    var preview = document.getElementById('word-salad-offer-preview');
    if (offer.play_url) {
      preview.hidden = false;
      preview.href = offer.play_url;
    } else {
      preview.hidden = true;
    }
    document.getElementById('word-salad-offer-reset-progress').hidden = offer.kind === 'idea';
    if (offer.kind === 'full') {
      ensureOfferGrid(offer.grid_text || '');
      updateValidation('word-salad-offer-validation', offerGrid, 'word-salad-offer-words');
    }
    clearOfferError();
  }

  function openEdit(linkId, trigger, suppliedItem) {
    if (busy && !suppliedItem) return;
    if (suppliedItem) {
      fillEditor(suppliedItem);
      modal.open(trigger);
      return;
    }
    setBusy(true);
    support.requestJson(endpoint(endpoints.detail, linkId))
      .then(function (data) {
        fillEditor(data.item);
        modal.open(trigger);
      })
      .catch(function (error) { alert(error.message || String(error)); })
      .finally(function () { setBusy(false); });
  }

  function openOffer(offerId, trigger, supplied) {
    if (busy && !supplied) return;
    if (supplied) {
      fillOfferEditor(supplied);
      offerModal.open(trigger);
      return;
    }
    setBusy(true);
    support.requestJson(endpoint(endpoints.offerDetail, offerId))
      .then(function (data) {
        fillOfferEditor(data.offer);
        offerModal.open(trigger);
      })
      .catch(function (error) { alert(error.message || String(error)); })
      .finally(function () { setBusy(false); });
  }

  function createAt(number, trigger) {
    if (busy) return;
    setBusy(true);
    support.postJson(endpoints.create, { at_number: number })
      .then(function (data) {
        setRows(data.rows);
        openEdit(data.item.link_id, trigger, data.item);
      })
      .catch(function (error) { alert(error.message || String(error)); })
      .finally(function () { setBusy(false); });
  }

  function removeItem(linkId) {
    if (busy || !window.confirm('Удалить этот выпуск салатика?')) return;
    setBusy(true);
    clearError();
    support.postJson(endpoint(endpoints.remove, linkId), {})
      .then(function (data) {
        setRows(data.rows);
        if (editLinkId === linkId) modal.close();
      })
      .catch(function (error) {
        if (modalElement.classList.contains('is-open')) showError(error.message || String(error));
        else alert(error.message || String(error));
      })
      .finally(function () { setBusy(false); });
  }

  list.addEventListener('click', function (event) {
    var insert = event.target.closest('[data-insert-at]');
    if (insert) {
      createAt(parseInt(insert.dataset.insertAt, 10), insert);
      return;
    }
    var edit = event.target.closest('[data-edit]');
    if (edit) {
      openEdit(parseInt(edit.dataset.edit, 10), edit);
      return;
    }
    var offerEdit = event.target.closest('[data-offer-edit]');
    if (offerEdit) {
      openOffer(parseInt(offerEdit.dataset.offerEdit, 10), offerEdit);
      return;
    }
    var revisionLink = event.target.closest('[data-revision-link]');
    if (revisionLink) {
      var note = window.prompt('Заметка автору (необязательно)', '') || '';
      setBusy(true);
      support.postJson(endpoint(endpoints.linkRevision, parseInt(revisionLink.dataset.revisionLink, 10)), { admin_note: note })
        .then(function (data) {
          setOffers(data.offers || sentOffers);
          if (data.offer) upsertOfferMap(data.offer);
        })
        .catch(function (error) { alert(error.message || String(error)); })
        .finally(function () { setBusy(false); });
      return;
    }
    var remove = event.target.closest('[data-delete]');
    if (remove) removeItem(parseInt(remove.dataset.delete, 10));
  });

  document.querySelectorAll('[data-insert-end]').forEach(function (button) {
    button.addEventListener('click', function () { createAt(rows.length + 1, button); });
  });

  support.mountTabs(document.getElementById('word-salad-tabs'), function (tab) {
    viewTab = tab;
    render();
  });

  document.getElementById('word-salad-save-start').addEventListener('click', function () {
    if (busy) return;
    var publishStart = document.getElementById('word-salad-publish-start').value;
    if (!publishStart) return;
    setBusy(true);
    support.postJson(endpoints.publishStart, { publish_start: publishStart })
      .then(function (data) {
        document.getElementById('word-salad-publish-start').value = data.publish_start;
        setRows(data.rows);
      })
      .catch(function (error) { alert(error.message || String(error)); })
      .finally(function () { setBusy(false); });
  });

  document.getElementById('word-salad-edit-words').addEventListener('input', function () {
    updateValidation('word-salad-edit-validation', scheduleGrid, 'word-salad-edit-words', 'word-salad-edit-rare-words');
  });
  document.getElementById('word-salad-edit-rare-words').addEventListener('input', function () {
    updateValidation('word-salad-edit-validation', scheduleGrid, 'word-salad-edit-words', 'word-salad-edit-rare-words');
  });
  document.getElementById('word-salad-offer-words').addEventListener('input', function () {
    updateValidation('word-salad-offer-validation', offerGrid, 'word-salad-offer-words');
  });

  document.getElementById('word-salad-edit-save').addEventListener('click', function () {
    if (busy || editLinkId == null) return;
    if (!scheduleGrid) {
      showError('Редактор сетки не загрузился. Обновите страницу.');
      return;
    }
    clearError();
    setBusy(true);
    support.postJson(endpoint(endpoints.save, editLinkId), {
      intro: document.getElementById('word-salad-edit-intro').value,
      grid_text: scheduleGrid ? scheduleGrid.getGridText() : '',
      words_text: document.getElementById('word-salad-edit-words').value,
      rare_words_text: document.getElementById('word-salad-edit-rare-words').value
    }).then(function (data) {
      var item = data.item;
      setRows(data.rows);
      fillEditor(item);
    }).catch(function (error) {
      showError(error.message || String(error));
    }).finally(function () { setBusy(false); });
  });

  document.getElementById('word-salad-edit-delete').addEventListener('click', function () {
    if (editLinkId != null) removeItem(editLinkId);
  });

  document.getElementById('word-salad-edit-recheck').addEventListener('click', function () {
    if (busy || editLinkId == null) return;
    if (!window.confirm('Перепроверить все посылки всех игроков? Цепочки будут пересобраны, уже собранным салатикам новые слова засчитаются в момент последней успешной посылки.')) return;
    clearError();
    setBusy(true);
    support.postJson(endpoint(endpoints.recheck, editLinkId), {})
      .then(function (data) {
        var stats = data.recheck || {};
        var actors = stats.actors || 0;
        var credited = stats.credited || 0;
        alert('Перепроверено игроков: ' + actors + (credited ? (', добавлено ответов: ' + credited) : ''));
      })
      .catch(function (error) {
        showError(error.message || String(error));
      })
      .finally(function () { setBusy(false); });
  });

  document.getElementById('word-salad-offer-save').addEventListener('click', function () {
    if (busy || editOfferId == null) return;
    var ideaOnly = document.getElementById('word-salad-offer-full-fields').hidden;
    if (!ideaOnly && !offerGrid) {
      showOfferError('Редактор сетки не загрузился. Обновите страницу.');
      return;
    }
    clearOfferError();
    setBusy(true);
    support.postJson(endpoint(endpoints.offerSave, editOfferId), {
      theme: document.getElementById('word-salad-offer-theme').value,
      idea_text: document.getElementById('word-salad-offer-idea').value,
      suggested_words: document.getElementById('word-salad-offer-suggested').value,
      grid_text: offerGrid ? offerGrid.getGridText() : '',
      words_text: document.getElementById('word-salad-offer-words').value,
      comment: document.getElementById('word-salad-offer-comment').value
    }).then(function (data) {
      setOffers(data.offers || sentOffers);
      fillOfferEditor(data.offer);
    }).catch(function (error) {
      showOfferError(error.message || String(error));
    }).finally(function () { setBusy(false); });
  });

  document.getElementById('word-salad-offer-accept').addEventListener('click', function () {
    if (busy || editOfferId == null) return;
    clearOfferError();
    setBusy(true);
    support.postJson(endpoint(endpoints.offerAccept, editOfferId), {})
      .then(function (data) {
        setOffers(data.offers || sentOffers);
        if (data.rows) setRows(data.rows);
        if (data.offer) upsertOfferMap(data.offer);
        offerModal.close();
      })
      .catch(function (error) {
        showOfferError(error.message || String(error));
      })
      .finally(function () { setBusy(false); });
  });

  document.getElementById('word-salad-offer-revision').addEventListener('click', function () {
    if (busy || editOfferId == null) return;
    clearOfferError();
    setBusy(true);
    support.postJson(endpoint(endpoints.offerRevision, editOfferId), {
      admin_note: document.getElementById('word-salad-offer-admin-note').value
    }).then(function (data) {
      setOffers(data.offers || sentOffers);
      offerModal.close();
    }).catch(function (error) {
      showOfferError(error.message || String(error));
    }).finally(function () { setBusy(false); });
  });

  document.getElementById('word-salad-offer-reset-progress').addEventListener('click', function () {
    if (busy || editOfferId == null) return;
    if (!window.confirm('Сбросить прогресс всех игроков по этому салатику?')) return;
    setBusy(true);
    support.postJson(endpoint(endpoints.offerReset, editOfferId), {})
      .then(function () { alert('Прогресс сброшен'); })
      .catch(function (error) { showOfferError(error.message || String(error)); })
      .finally(function () { setBusy(false); });
  });

  support.mountSortable(list, {
    isBusy: function () { return busy || !canReorder(); },
    canDrag: function (item) { return item.dataset.published !== '1'; },
    minIndex: function () {
      return viewTab === 'future' ? 0 : support.lastPublishedNumber(rows);
    },
    onOrder: function (visibleOrder, meta) {
      if (busy) return;
      var order = support.fullOrder(rows, visibleOrder, viewTab);
      setBusy(true);
      support.postJson(endpoints.reorder, { order: order })
        .then(function (data) {
          setRows(data.rows);
          if (meta.keyboard) {
            var moved = list.querySelector('[data-link-id="' + meta.movedId + '"] .support-schedule-handle');
            if (moved) moved.focus();
          }
        })
        .catch(function (error) { alert(error.message || String(error)); })
        .finally(function () { setBusy(false); });
    }
  });

  render();
  if (config.dataset.initialEdit) openEdit(parseInt(config.dataset.initialEdit, 10));
}());
