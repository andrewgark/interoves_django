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
        return (a.publish_date || '').localeCompare(b.publish_date || '');
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
  };
})(window);
