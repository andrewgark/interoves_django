(function () {
  'use strict';
  var root = document.getElementById('cz-pool'); if (!root) return;
  var taxonomy = JSON.parse(document.getElementById('censorly-pool-taxonomy').textContent || '{}');
  var topicPicker = document.getElementById('cz-pool-topics'), typePicker = document.getElementById('cz-pool-types');
  var category = document.getElementById('cz-pool-category'), sub = document.getElementById('cz-pool-subcategory');
  var groups = document.getElementById('cz-pool-groups'), modal = document.getElementById('cz-pool-modal'), current = '';
  var more = document.getElementById('cz-pool-more'), prompt = document.getElementById('cz-pool-prompt'), nextOffset = null, busy = false, requestId = 0, controller = null;
  var saveButton = document.getElementById('cz-pool-save'), saveBusy = false;
  function csrf() { var el = document.querySelector('[name=csrfmiddlewaretoken]'); return el ? el.value : ''; }
  function addCheck(container, label, value, group) {
    var item = document.createElement('label'); item.className = 'censorly-pool-tag-option';
    var input = document.createElement('input'); input.type = 'checkbox'; input.value = value; input.dataset.poolTag = group;
    item.append(input, document.createTextNode(label)); container.append(item);
  }
  Object.keys(taxonomy.categories || {}).forEach(function (parent) {
    var section = document.createElement('section'); section.className = 'censorly-pool-tag-category';
    var title = document.createElement('strong'); title.textContent = parent; section.append(title);
    addCheck(section, 'Вся категория', parent, 'topic');
    (taxonomy.categories[parent] || []).forEach(function (child) { addCheck(section, child, parent + '/' + child, 'topic'); });
    topicPicker.append(section);
  });
  (taxonomy.types || []).forEach(function (type) { addCheck(typePicker, type, type, 'type'); });
  function setChecks(container, values) {
    var selected = new Set(values || []);
    container.querySelectorAll('input[type="checkbox"]').forEach(function (input) { input.checked = selected.has(input.value); });
  }
  function getChecks(container) {
    return Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(function (input) { return input.value; });
  }
  function esc(v) { return String(v || '').replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function articleHtml(row) {
          var encoded = esc(encodeURIComponent(JSON.stringify(row)));
          return '<article class="censorly-pool-row' + (row.enabled ? '' : ' is-disabled') + '"><div><strong>' + esc(row.title) + '</strong><small>' + esc(row.topics.join(' · ')) + (row.types.length ? ' · ' + esc(row.types.join(', ')) : '') + '</small></div><button type="button" class="new-btn new-btn--mini new-btn--ghost" data-delta="1" data-row="' + encoded + '" title="Усложнить">↑</button><button type="button" class="new-btn new-btn--mini new-btn--ghost" data-delta="-1" data-row="' + encoded + '" title="Упростить">↓</button><button type="button" class="new-btn new-btn--mini new-btn--ghost" data-edit="' + encoded + '">Теги</button></article>';
  }
  function load(reset) {
    if (!category.value) return;
    if (reset) {
      requestId += 1; if (controller) controller.abort(); controller = null; busy = false;
      nextOffset = 0; groups.innerHTML = '';
    }
    if (busy) return;
    busy = true; more.disabled = true;
    var currentRequest = ++requestId;
    controller = new AbortController();
    var url = new URL(root.dataset.listUrl, location.href); url.searchParams.set('category', category.value); if (sub.value) url.searchParams.set('subcategory', sub.value); url.searchParams.set('offset', nextOffset || 0);
    fetch(url, {credentials:'same-origin', signal:controller.signal}).then(function (r) { return r.json(); }).then(function (data) {
      if (currentRequest !== requestId) return;
      prompt.hidden = true;
      Object.keys(data.groups).forEach(function (level) {
        var section = Array.from(groups.querySelectorAll('[data-pool-level]')).find(function (node) { return node.dataset.poolLevel === level; });
        if (!section) { section = document.createElement('section'); section.className = 'pal-card support-card'; section.dataset.poolLevel = level; section.innerHTML = '<h2 class="pal-title">' + esc(level) + '</h2><div class="censorly-pool-level-rows"></div>'; groups.append(section); }
        section.querySelector('.censorly-pool-level-rows').insertAdjacentHTML('beforeend', data.groups[level].map(articleHtml).join(''));
      });
      nextOffset = data.next_offset; more.hidden = nextOffset === null; more.textContent = 'Показать ещё · осталось ' + Math.max(0, data.total - (nextOffset || data.total));
      if (!data.total) { groups.innerHTML = '<p class="support-empty">В этой категории статей нет.</p>'; more.hidden = true; }
    }).catch(function (error) { if (error.name !== 'AbortError' && currentRequest === requestId) document.getElementById('cz-pool-status').textContent = error.message; }).finally(function () { if (currentRequest === requestId) { busy = false; controller = null; more.disabled = false; } });
  }
  category.addEventListener('change', function () {
    sub.innerHTML = '<option value="">Вся категория</option>' + (taxonomy.categories[category.value] || []).map(function (v) { return '<option>' + esc(v) + '</option>'; }).join('');
    if (!category.value) { requestId += 1; if (controller) controller.abort(); controller = null; busy = false; groups.innerHTML = ''; prompt.hidden = false; more.hidden = true; nextOffset = null; return; }
    load(true);
  });
  sub.addEventListener('change', function () { load(true); });
  more.addEventListener('click', function () { load(false); });
  function open(row) {
    if (saveBusy) return;
    document.getElementById('cz-pool-error').hidden = true;
    current = row.title || ''; document.getElementById('cz-pool-title').value = current;
    setChecks(topicPicker, row.topics); setChecks(typePicker, row.types);
    document.getElementById('cz-pool-difficulty').value = row.difficulty || 'средне'; document.getElementById('cz-pool-enabled').checked = row.enabled !== false;
    document.getElementById('cz-pool-modal-title').textContent = current ? 'Изменить статью и теги' : 'Добавить статью';
    modal.hidden = false; modal.classList.add('is-open'); document.body.classList.add('new-modal-open');
  }
  function close(force) {
    if (saveBusy && !force) return;
    modal.hidden = true; modal.classList.remove('is-open'); document.body.classList.remove('new-modal-open');
  }
  function save(body) {
    if (saveBusy) return;
    saveBusy = true; saveButton.disabled = true;
    fetch(root.dataset.saveUrl, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (data) { if (!r.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить статью'); return data; }); })
      .then(function () { close(true); if (category.value) load(true); })
      .catch(function (err) { var box = document.getElementById('cz-pool-error'); box.textContent = err.message; box.hidden = false; })
      .finally(function () { saveBusy = false; saveButton.disabled = false; });
  }
  document.getElementById('cz-pool-add').addEventListener('click', function () { open({}); });
  groups.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-edit], [data-delta]'); if (!btn) return;
    var row = JSON.parse(decodeURIComponent(btn.dataset.edit || btn.dataset.row));
    if (btn.hasAttribute('data-edit')) return open(row);
    var levels = ['легко','средне','сложно'], next = Math.max(0, Math.min(2, levels.indexOf(row.difficulty) + Number(btn.dataset.delta)));
    save({title:row.title, replacement_title:row.title, topics:row.topics, types:row.types, difficulty:levels[next], enabled:row.enabled});
  });
  modal.querySelectorAll('[data-pool-close]').forEach(function (el) { el.addEventListener('click', function () { close(); }); });
  saveButton.addEventListener('click', function () {
    save({title:current, replacement_title:document.getElementById('cz-pool-title').value, topics:getChecks(topicPicker), types:getChecks(typePicker), difficulty:document.getElementById('cz-pool-difficulty').value, enabled:document.getElementById('cz-pool-enabled').checked});
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.hidden) close();
  });
})();
