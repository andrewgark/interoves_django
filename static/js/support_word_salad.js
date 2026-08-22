(function () {
  'use strict';

  var support = window.SupportScheduleTabs;
  var list = document.getElementById('word-salad-list');
  var bootstrap = document.getElementById('word-salad-bootstrap');
  if (!support || !list || !bootstrap) return;

  var rows = JSON.parse(bootstrap.textContent || '[]');
  var busy = false;
  var editLinkId = null;
  var endpoints = {
    create: list.dataset.createUrl,
    reorder: list.dataset.reorderUrl,
    detail: list.dataset.detailUrl,
    save: list.dataset.saveUrl,
    remove: list.dataset.deleteUrl
  };
  var modalElement = document.getElementById('word-salad-edit-modal');
  var modal = support.mountModal(modalElement, {
    closeSelector: '[data-word-salad-close]',
    initialFocus: '#word-salad-edit-intro',
    onClose: function () { editLinkId = null; }
  });

  function endpoint(template, id) {
    return template.replace('/0/', '/' + encodeURIComponent(String(id)) + '/');
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
    list.innerHTML = '';
    document.getElementById('word-salad-meta').textContent = 'Всего ' + rows.length;
    if (!rows.length) {
      list.innerHTML = '<p class="support-empty">Пока нет выпусков. Нажмите «Создать салат».</p>';
      return;
    }
    rows.forEach(function (row) {
      list.appendChild(insertButton(row.number));
      var item = document.createElement('div');
      item.className = 'support-ladder-item support-schedule-item';
      item.draggable = true;
      item.dataset.linkId = String(row.link_id);
      item.innerHTML =
        '<button type="button" class="support-ladder-item__handle support-schedule-handle" aria-label="Перетащить выпуск №' + row.number + '" title="Перетащить; стрелки вверх/вниз меняют порядок">⠿</button>' +
        '<div class="support-ladder-item__num">№' + row.number + '</div>' +
        '<div class="support-ladder-item__body">' +
          '<div class="support-ladder-item__title">' + support.escapeHtml('Салат #' + row.number) + '</div>' +
          '<div class="support-ladder-item__meta"><span class="support-cell-mono">' +
            support.escapeHtml(row.grid_preview || '—') + '</span> · id ' + row.link_id +
            ' · ' + row.words_count + ' сл.</div>' +
          '<div class="support-ladder-item__preview">' + support.escapeHtml(row.words_preview || '—') + '</div>' +
        '</div>' +
        '<div class="support-ladder-item__actions">' +
          '<button type="button" class="new-btn new-btn--mini" data-edit="' + row.link_id + '">править</button>' +
          '<a class="new-btn new-btn--mini new-btn--ghost" href="' + support.escapeHtml(row.preview_url) + '" target="_blank" rel="noopener">сайт</a>' +
          '<button type="button" class="new-btn new-btn--mini new-btn--ghost support-item-delete" data-delete="' + row.link_id + '">удалить</button>' +
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
    if (busy || !window.confirm('Удалить этот выпуск салата?')) return;
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
    isBusy: function () { return busy; },
    canDrag: function () { return true; },
    onOrder: function (order, meta) {
      if (busy) return;
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
  if (list.dataset.initialEdit) openEdit(parseInt(list.dataset.initialEdit, 10));
}());
