(function () {
  'use strict';

  document.querySelectorAll('[data-results-progressive]').forEach(function (root) {
    var tbody = document.querySelector(root.getAttribute('data-results-tbody'));
    var button = root.querySelector('[data-results-load-more-button]');
    var status = root.querySelector('[data-results-load-more-status]');
    if (!tbody || !button) return;

    var nextPage = parseInt(root.getAttribute('data-next-page') || '0', 10) || 0;
    var total = parseInt(root.getAttribute('data-total') || '0', 10) || 0;
    var restoreTarget = 0;
    try {
      restoreTarget = parseInt(new URL(window.location.href).searchParams.get('loaded') || '0', 10) || 0;
    } catch (e) {}
    var loading = false;
    var loadError = false;

    function countRows() {
      return tbody.querySelectorAll('tr[data-result-row]:not(.is-hidden)').length;
    }

    function updateUrl(count) {
      try {
        var url = new URL(window.location.href);
        url.searchParams.set('loaded', String(count));
        url.searchParams.delete('page');
        url.searchParams.delete('partial');
        window.history.replaceState(window.history.state, '', url.toString());
      } catch (e) {}
    }

    function updateState() {
      var count = countRows();
      button.hidden = !nextPage && !loadError;
      if (!status) return;
      status.textContent = loadError
        ? 'Не удалось загрузить следующую порцию.'
        : nextPage
          ? 'Загружено ' + count + ' из ' + total
          : 'Загружены все ' + count + (root.getAttribute('data-subject') || ' результатов');
    }

    async function loadNext() {
      if (loading || !nextPage) return;
      loading = true;
      loadError = false;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      root.setAttribute('aria-busy', 'true');
      button.textContent = 'Загрузка…';
      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timeoutId = controller ? window.setTimeout(function () { controller.abort(); }, 15000) : null;
      try {
        var url = new URL(window.location.href);
        url.searchParams.set('page', String(nextPage));
        url.searchParams.set('partial', '1');
        var fetchOptions = { credentials: 'same-origin' };
        if (controller) fetchOptions.signal = controller.signal;
        var response = await fetch(url.toString(), fetchOptions);
        if (!response.ok) throw new Error('HTTP ' + response.status);
        var tmp = document.createElement('tbody');
        tmp.innerHTML = await response.text();
        var meta = tmp.querySelector('template[data-results-meta="1"]');
        if (!meta) throw new Error('Missing results metadata');
        nextPage = meta.getAttribute('data-has-next') === '1'
          ? (parseInt(meta.getAttribute('data-next-page') || '', 10) || 0) : 0;
        total = parseInt(meta.getAttribute('data-total') || total, 10) || total;
        Array.prototype.slice.call(tmp.children).forEach(function (node) {
          if (!node.tagName || node.tagName.toLowerCase() === 'template') return;
          tbody.appendChild(node);
        });
        if (typeof window.interovesApplyHideAnon === 'function') window.interovesApplyHideAnon();
        updateUrl(countRows());
      } catch (error) {
        loadError = true;
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
        loading = false;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        root.removeAttribute('aria-busy');
        button.textContent = loadError ? 'Повторить' : 'Загрузить ещё';
        updateState();
      }
    }

    button.addEventListener('click', loadNext);
    document.addEventListener('results:visibility-changed', updateState);
    updateState();

    // Rebuild a previously loaded state after a refresh or a shared link.
    // This is opt-in via ?loaded=... and does not affect ordinary visits.
    if (restoreTarget > countRows()) {
      (async function () {
        while (nextPage && countRows() < restoreTarget) await loadNext();
      })();
    }
  });
})();
