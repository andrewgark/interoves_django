(function () {
  'use strict';

  var support = window.SupportScheduleTabs;
  var list = document.getElementById('word-salad-list');
  var bootstrap = document.getElementById('word-salad-bootstrap');
  var config = document.getElementById('word-salad-config');
  if (!support || !list || !bootstrap || !config) return;

  var rows = JSON.parse(bootstrap.textContent || '[]');
  var viewTab = 'schedule';
  var busy = false;
  var editLinkId = null;
  var endpoints = {
    create: config.dataset.createUrl,
    reorder: config.dataset.reorderUrl,
    publishStart: config.dataset.publishStartUrl,
    detail: config.dataset.detailUrl,
    save: config.dataset.saveUrl,
    remove: config.dataset.deleteUrl
  };
  var modalElement = document.getElementById('word-salad-edit-modal');
  var modal = support.mountModal(modalElement, {
    closeSelector: '[data-word-salad-close]',
    initialFocus: '#word-salad-edit-intro',
    onClose: function () { editLinkId = null; }
  });

  function endpoint(template, id) {
    return support.endpoint(template, id);
  }

  function canReorder() {
    return viewTab === 'schedule' || viewTab === 'future';
  }

  function setBusy(value) {
    busy = !!value;
    list.classList.toggle('is-busy', busy);
    document.querySelectorAll('[data-insert-end], #word-salad-edit-save, #word-salad-edit-delete').forEach(function (button) {
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

  function setRows(nextRows) {
    rows = (nextRows || []).slice().sort(function (a, b) { return a.number - b.number; });
    render();
  }

  function insertButton(number) {
    var insert = document.createElement('div');
    insert.className = 'support-ladder-insert';
    insert.innerHTML = '<button type="button" class="support-ladder-insert__btn" data-insert-at="' +
      number + '" title="Вставить перед №' + number + '" aria-label="Вставить перед выпуском №' + number + '">+</button>';
    return insert;
  }

  function render() {
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
      var item = document.createElement('div');
      item.className = 'support-ladder-item support-schedule-item' + (row.is_published ? ' is-published' : ' is-future');
      item.draggable = !row.is_published && canReorder();
      item.dataset.linkId = String(row.link_id);
      item.dataset.published = row.is_published ? '1' : '0';
      item.innerHTML =
        '<button type="button" class="support-ladder-item__handle support-schedule-handle" ' +
          (row.is_published || !canReorder() ? 'disabled ' : '') +
          'aria-label="Перетащить выпуск №' + row.number + '" title="Перетащить; стрелки вверх/вниз меняют порядок">⠿</button>' +
        '<div class="support-ladder-item__num">№' + row.number + '</div>' +
        '<div class="support-ladder-item__body">' +
          '<div class="support-ladder-item__title">' + support.escapeHtml('Салатик #' + row.number) +
            ' <span class="support-flag ' + support.statusClass(row) + '">' + support.statusLabel(row) + '</span></div>' +
          '<div class="support-ladder-item__meta"><span class="support-cell-mono">' +
            support.escapeHtml(row.grid_preview || '—') + '</span> · id ' + row.link_id +
            ' · ' + row.words_count + ' сл.' +
            (row.publish_date ? ' · ' + support.escapeHtml(row.publish_date) : '') + '</div>' +
          '<div class="support-ladder-item__preview">' + support.escapeHtml(row.words_preview || '—') + '</div>' +
        '</div>' +
        '<div class="support-ladder-item__actions">' +
          '<button type="button" class="new-btn new-btn--mini" data-edit="' + row.link_id + '">править</button>' +
          '<a class="new-btn new-btn--mini new-btn--ghost" href="' + support.escapeHtml(row.preview_url) + '" target="_blank" rel="noopener">сайт</a>' +
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
    document.getElementById('word-salad-edit-grid').value = item.grid_text || '';
    document.getElementById('word-salad-edit-words').value = item.words_text || '';
    document.getElementById('word-salad-edit-preview').href = item.preview_url || '#';
    var row = rows.find(function (candidate) { return candidate.link_id === item.link_id; });
    document.getElementById('word-salad-edit-delete').hidden = !!(row && row.is_published);
    clearError();
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

  document.getElementById('word-salad-edit-save').addEventListener('click', function () {
    if (busy || editLinkId == null) return;
    clearError();
    setBusy(true);
    support.postJson(endpoint(endpoints.save, editLinkId), {
      intro: document.getElementById('word-salad-edit-intro').value,
      grid_text: document.getElementById('word-salad-edit-grid').value,
      words_text: document.getElementById('word-salad-edit-words').value
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
            var moved = list.querySelector('[data-link-id="' + meta.movedId + '"] .support-ladder-item__handle');
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
