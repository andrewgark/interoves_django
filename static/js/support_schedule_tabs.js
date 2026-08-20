(function (global) {
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function rowsForTab(allRows, tab) {
    if (tab === 'published') {
      return allRows.filter(function (r) { return r.is_published; }).sort(function (a, b) {
        return (b.publish_date || '').localeCompare(a.publish_date || '');
      });
    }
    if (tab === 'future') {
      return allRows.filter(function (r) { return !r.is_published; }).sort(function (a, b) {
        return a.number - b.number;
      });
    }
    return allRows;
  }

  function mountTabs(container, onChange) {
    container.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-tab]');
      if (!btn) return;
      container.querySelectorAll('.support-tabs__btn').forEach(function (el) {
        el.classList.toggle('is-active', el === btn);
      });
      onChange(btn.getAttribute('data-tab'));
    });
  }

  function csrfToken() {
    var input = document.querySelector('input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function requestJson(url, options) {
    options = options || {};
    var fetchOptions = {
      method: options.method || 'GET',
      credentials: 'same-origin',
      headers: Object.assign({ 'X-CSRFToken': csrfToken() }, options.headers || {})
    };
    if (Object.prototype.hasOwnProperty.call(options, 'body')) {
      fetchOptions.headers['Content-Type'] = 'application/json';
      fetchOptions.body = JSON.stringify(options.body || {});
    }
    return fetch(url, fetchOptions).then(function (response) {
      return response.text().then(function (text) {
        var data;
        try {
          data = text ? JSON.parse(text) : {};
        } catch (err) {
          data = null;
        }
        if (!response.ok || !data || data.ok === false) {
          var clean = (text || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
          throw new Error((data && data.error) || clean.slice(0, 240) || ('HTTP ' + response.status));
        }
        return data;
      });
    });
  }

  function postJson(url, body) {
    return requestJson(url, { method: 'POST', body: body || {} });
  }

  function mountModal(modal, options) {
    options = options || {};
    var returnFocus = null;
    function open(trigger) {
      returnFocus = trigger || document.activeElement;
      modal.hidden = false;
      modal.classList.add('is-open');
      document.body.style.overflow = 'hidden';
      var focusTarget = options.initialFocus && modal.querySelector(options.initialFocus);
      if (focusTarget) focusTarget.focus();
    }
    function close() {
      if (!modal.classList.contains('is-open')) return;
      modal.classList.remove('is-open');
      modal.hidden = true;
      document.body.style.overflow = '';
      if (options.onClose) options.onClose();
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
      returnFocus = null;
    }
    modal.querySelectorAll(options.closeSelector || '[data-modal-close]').forEach(function (el) {
      el.addEventListener('click', close);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && modal.classList.contains('is-open')) close();
    });
    return { open: open, close: close };
  }

  function mountSortable(list, options) {
    options = options || {};
    var itemSelector = options.itemSelector || '.support-schedule-item';
    var handleSelector = options.handleSelector || '.support-schedule-handle';
    var dragId = null;
    var armedId = null;
    var dropIndex = null;

    function idFor(item) {
      return parseInt(item.dataset.linkId, 10);
    }
    function items() {
      return Array.prototype.slice.call(list.querySelectorAll(itemSelector));
    }
    function order() {
      return items().map(idFor);
    }
    function minIndex() {
      return Math.max(0, Number(options.minIndex ? options.minIndex() : 0) || 0);
    }
    function canDrag(item) {
      return !options.canDrag || options.canDrag(item);
    }
    function isBusy() {
      return !!(options.isBusy && options.isBusy());
    }
    function clearMarkers() {
      items().forEach(function (item) {
        item.classList.remove('is-dragging', 'is-drop-before', 'is-drop-after');
      });
      dragId = null;
      armedId = null;
      dropIndex = null;
    }
    function submit(nextOrder, meta) {
      if (options.onOrder) options.onOrder(nextOrder, meta || {});
    }

    list.addEventListener('pointerdown', function (event) {
      var handle = event.target.closest(handleSelector);
      var item = handle && handle.closest(itemSelector);
      armedId = item && canDrag(item) && !isBusy() ? String(idFor(item)) : null;
    });
    list.addEventListener('mousedown', function (event) {
      var handle = event.target.closest(handleSelector);
      var item = handle && handle.closest(itemSelector);
      armedId = item && canDrag(item) && !isBusy() ? String(idFor(item)) : null;
    });
    list.addEventListener('dragstart', function (event) {
      var item = event.target.closest(itemSelector);
      if (!item || !canDrag(item) || isBusy() || armedId !== String(idFor(item))) {
        event.preventDefault();
        return;
      }
      dragId = String(idFor(item));
      dropIndex = null;
      item.classList.add('is-dragging');
      event.dataTransfer.effectAllowed = 'move';
      try { event.dataTransfer.setData('text/plain', dragId); } catch (err) {}
    });
    list.addEventListener('dragover', function (event) {
      if (!dragId || isBusy()) return;
      var item = event.target.closest(itemSelector);
      if (!item) return;
      event.preventDefault();
      var allItems = items();
      var targetIndex = allItems.indexOf(item);
      if (targetIndex < 0) return;
      var rect = item.getBoundingClientRect();
      var before = (event.clientY - rect.top) < rect.height / 2;
      dropIndex = Math.max(minIndex(), before ? targetIndex : targetIndex + 1);
      allItems.forEach(function (row) {
        row.classList.remove('is-drop-before', 'is-drop-after');
      });
      item.classList.add(dropIndex <= targetIndex ? 'is-drop-before' : 'is-drop-after');
    });
    list.addEventListener('drop', function (event) {
      if (!dragId || dropIndex == null || isBusy()) return;
      event.preventDefault();
      var nextOrder = order();
      var from = nextOrder.indexOf(parseInt(dragId, 10));
      var to = dropIndex;
      if (from < 0 || from < minIndex()) {
        clearMarkers();
        return;
      }
      if (from < to) to -= 1;
      to = Math.max(minIndex(), to);
      if (from !== to) {
        var moved = nextOrder.splice(from, 1)[0];
        nextOrder.splice(to, 0, moved);
        submit(nextOrder, { movedId: moved, from: from, to: to });
      }
      clearMarkers();
    });
    list.addEventListener('dragend', clearMarkers);
    list.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      var handle = event.target.closest(handleSelector);
      var item = handle && handle.closest(itemSelector);
      if (!item || !canDrag(item) || isBusy()) return;
      var nextOrder = order();
      var from = nextOrder.indexOf(idFor(item));
      var to = from + (event.key === 'ArrowUp' ? -1 : 1);
      if (to < minIndex() || to < 0 || to >= nextOrder.length) return;
      event.preventDefault();
      var moved = nextOrder.splice(from, 1)[0];
      nextOrder.splice(to, 0, moved);
      submit(nextOrder, { movedId: moved, from: from, to: to, keyboard: true });
    });
    return { clear: clearMarkers, order: order };
  }

  function renderBannedList(host, banned, opts) {
    opts = opts || {};
    host.innerHTML = '';
    if (!banned.length) {
      host.innerHTML = '<p class="support-empty">Пока пусто.</p>';
      return;
    }
    var list = document.createElement('div');
    list.className = 'support-banned-list';
    banned.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'support-banned-item';
      var main = opts.renderMain ? opts.renderMain(item) : (
        '<div class="support-banned-item__main">' +
          '<div class="support-banned-item__word">' + escapeHtml(item.word || item.label || '—') + '</div>' +
          (item.banned_at ? '<div class="support-banned-item__meta">' + escapeHtml(item.banned_at) + '</div>' : '') +
        '</div>'
      );
      var unbanBtn = '';
      if (opts.unbanAttr) {
        unbanBtn = '<button type="button" class="new-btn new-btn--mini new-btn--ghost" ' +
          opts.unbanAttr(item) + '>снять запрет</button>';
      }
      row.innerHTML = main + unbanBtn;
      list.appendChild(row);
    });
    host.appendChild(list);
  }

  global.SupportScheduleTabs = {
    escapeHtml: escapeHtml,
    rowsForTab: rowsForTab,
    mountTabs: mountTabs,
    renderBannedList: renderBannedList,
    requestJson: requestJson,
    postJson: postJson,
    mountModal: mountModal,
    mountSortable: mountSortable,
  };
})(window);
