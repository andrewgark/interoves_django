(function () {
  'use strict';
  var root = document.getElementById('cz-pool'); if (!root) return;
  var taxonomy = JSON.parse(document.getElementById('censorly-pool-taxonomy').textContent || '{}');
  var topicPicker = document.getElementById('cz-pool-topics'), typePicker = document.getElementById('cz-pool-types');
  var category = document.getElementById('cz-pool-category'), sub = document.getElementById('cz-pool-subcategory');
  var groups = document.getElementById('cz-pool-groups'), modal = document.getElementById('cz-pool-modal'), current = '';
  var more = document.getElementById('cz-pool-more'), prompt = document.getElementById('cz-pool-prompt'), nextOffset = null, loadedCount = 0, busy = false, requestId = 0, controller = null, refreshState = null;
  var saveButton = document.getElementById('cz-pool-save'), saveBusy = false;
  function csrf() { var el = document.querySelector('[name=csrfmiddlewaretoken]'); return el ? el.value : ''; }
  function addCheck(container, label, value, group) {
    var item = document.createElement('label'); item.className = 'censorly-pool-tag-option';
    var input = document.createElement('input'); input.type = 'checkbox'; input.value = value; input.dataset.poolTag = group;
    item.append(input, document.createTextNode(label)); container.append(item);
  }
  function buildTagPickers() {
    topicPicker.innerHTML = ''; typePicker.innerHTML = '';
    Object.keys(taxonomy.categories || {}).forEach(function (parent) {
      var section = document.createElement('section'); section.className = 'censorly-pool-tag-category';
      var title = document.createElement('strong'); title.textContent = parent; section.append(title);
      addCheck(section, 'Вся категория', parent, 'topic');
      (taxonomy.categories[parent] || []).forEach(function (child) {
        var path = parent + '/' + child;
        addCheck(section, child.split('/').pop() + (child.indexOf('/') !== -1 ? ' · ' + child : ''), path, 'topic');
      });
      topicPicker.append(section);
    });
    (taxonomy.types || []).forEach(function (type) { addCheck(typePicker, type, type, 'type'); });
  }
  buildTagPickers();
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
          var tags = (row.topics || []).concat(row.types || []).join(' · ');
          return '<article class="censorly-pool-row' + (row.enabled ? '' : ' is-disabled') + '"><div class="censorly-pool-row__main"><strong title="' + esc(row.title) + '">' + esc(row.title) + '</strong><small title="' + esc(tags) + '">' + esc(tags) + '</small></div><div class="censorly-pool-actions"><button type="button" class="censorly-pool-action" data-delta="1" data-row="' + encoded + '" title="Усложнить" aria-label="Усложнить">↑</button><button type="button" class="censorly-pool-action" data-delta="-1" data-row="' + encoded + '" title="Упростить" aria-label="Упростить">↓</button><button type="button" class="censorly-pool-action" data-edit="' + encoded + '" title="Изменить статью и теги" aria-label="Изменить статью и теги">✎</button></div></article>';
  }
  function load(reset) {
    if (!category.value) return;
    if (reset) {
      requestId += 1; if (controller) controller.abort(); controller = null; busy = false;
      nextOffset = 0; loadedCount = 0; groups.innerHTML = '';
    }
    if (busy) return;
    busy = true; more.disabled = true;
    var currentRequest = ++requestId;
    var pageLoaded = false;
    controller = new AbortController();
    var url = new URL(root.dataset.listUrl, location.href); url.searchParams.set('category', category.value); if (sub.value) url.searchParams.set('subcategory', sub.value); url.searchParams.set('offset', nextOffset || 0);
    fetch(url, {credentials:'same-origin', signal:controller.signal}).then(function (r) { return r.json(); }).then(function (data) {
      if (currentRequest !== requestId) return;
      pageLoaded = true;
      prompt.hidden = true;
      Object.keys(data.groups).forEach(function (level) {
        var section = Array.from(groups.querySelectorAll('[data-pool-level]')).find(function (node) { return node.dataset.poolLevel === level; });
        if (!section) { section = document.createElement('section'); section.className = 'pal-card support-card'; section.dataset.poolLevel = level; section.innerHTML = '<h2 class="pal-title">' + esc(level) + '</h2><div class="censorly-pool-level-rows"></div>'; groups.append(section); }
        section.querySelector('.censorly-pool-level-rows').insertAdjacentHTML('beforeend', data.groups[level].map(articleHtml).join(''));
        loadedCount += data.groups[level].length;
      });
      nextOffset = data.next_offset; more.hidden = nextOffset === null; more.textContent = 'Показать ещё · осталось ' + Math.max(0, data.total - (nextOffset || data.total));
      if (!data.total) { groups.innerHTML = '<p class="support-empty">В этой категории статей нет.</p>'; more.hidden = true; }
    }).catch(function (error) { if (error.name !== 'AbortError' && currentRequest === requestId) document.getElementById('cz-pool-status').textContent = error.message; }).finally(function () {
      if (currentRequest !== requestId) return;
      busy = false; controller = null; more.disabled = false;
      if (pageLoaded && refreshState && loadedCount < refreshState.loaded && nextOffset !== null) { load(false); return; }
      if (refreshState) { var y = refreshState.scrollY; refreshState = null; requestAnimationFrame(function () { window.scrollTo(0, y); }); }
    });
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
      .then(function () {
        close(true);
        if (category.value) { refreshState = {loaded: loadedCount, scrollY: window.scrollY}; load(true); }
      })
      .catch(function (err) { var box = document.getElementById('cz-pool-error'); box.textContent = err.message; box.hidden = false; })
      .finally(function () { saveBusy = false; saveButton.disabled = false; });
  }
  document.getElementById('cz-pool-add').addEventListener('click', function () { open({}); });
  groups.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-edit], [data-delta]'); if (!btn) return;
    var row = JSON.parse(decodeURIComponent(btn.dataset.edit || btn.dataset.row));
    if (btn.hasAttribute('data-edit')) return open(row);
    var levels = ['легко','средне','сложно','очень сложно'], next = Math.max(0, Math.min(3, levels.indexOf(row.difficulty) + Number(btn.dataset.delta)));
    save({title:row.title, replacement_title:row.title, topics:row.topics, types:row.types, difficulty:levels[next], enabled:row.enabled});
  });
  modal.querySelectorAll('[data-pool-close]').forEach(function (el) { el.addEventListener('click', function () { close(); }); });
  saveButton.addEventListener('click', function () {
    save({title:current, replacement_title:document.getElementById('cz-pool-title').value, topics:getChecks(topicPicker), types:getChecks(typePicker), difficulty:document.getElementById('cz-pool-difficulty').value, enabled:document.getElementById('cz-pool-enabled').checked});
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.hidden) close();
  });

  var taxonomyModal = document.getElementById('cz-pool-taxonomy-modal');
  var taxonomyKind = document.getElementById('cz-taxonomy-kind');
  var taxonomyItem = document.getElementById('cz-taxonomy-item');
  var taxonomyName = document.getElementById('cz-taxonomy-name');
  var taxonomyParent = document.getElementById('cz-taxonomy-parent');
  var taxonomyOrder = document.getElementById('cz-taxonomy-order');
  var taxonomyError = document.getElementById('cz-taxonomy-error');
  var taxonomyCurrent = null, taxonomyBusy = false;
  function categoryNodes() { return taxonomy.tree || []; }
  function taxonomyLabel(item) { return '　'.repeat((item.path.match(/\//g) || []).length) + item.name; }
  function populateTaxonomyEditor() {
    var kind = taxonomyKind.value, selectedId = taxonomyCurrent && String(taxonomyCurrent.id);
    var entries = kind === 'category' ? categoryNodes() : (taxonomy.type_nodes || (taxonomy.types || []).map(function (name, index) { return {id:name, name:name, path:name, sort_order:index}; }));
    taxonomyItem.innerHTML = '<option value="">Новая запись</option>' + entries.map(function (item) {
      return '<option value="' + esc(item.id) + '">' + esc(kind === 'category' ? taxonomyLabel(item) : item.name) + '</option>';
    }).join('');
    taxonomyItem.value = entries.some(function (item) { return String(item.id) === selectedId; }) ? selectedId : '';
    document.getElementById('cz-taxonomy-parent-wrap').hidden = kind !== 'category';
    taxonomyParent.innerHTML = '<option value="">Верхний уровень</option>' + categoryNodes().filter(function (item) {
      return !taxonomyCurrent || (String(item.id) !== String(taxonomyCurrent.id) && !item.path.startsWith(taxonomyCurrent.path + '/'));
    }).map(function (item) { return '<option value="' + esc(item.id) + '">' + esc(item.path) + '</option>'; }).join('');
    var selected = entries.find(function (item) { return String(item.id) === taxonomyItem.value; });
    taxonomyCurrent = selected || null;
    taxonomyName.value = selected ? selected.name : '';
    taxonomyOrder.value = selected && selected.sort_order !== undefined ? selected.sort_order : '0';
    taxonomyParent.value = kind === 'category' && selected && selected.parent_id ? String(selected.parent_id) : '';
    document.getElementById('cz-taxonomy-delete').disabled = !selected;
  }
  function openTaxonomyEditor() {
    taxonomyCurrent = null; taxonomyKind.value = 'category'; populateTaxonomyEditor();
    taxonomyError.hidden = true; taxonomyModal.hidden = false; taxonomyModal.classList.add('is-open'); document.body.classList.add('new-modal-open');
  }
  function closeTaxonomyEditor() {
    if (taxonomyBusy) return;
    taxonomyModal.hidden = true; taxonomyModal.classList.remove('is-open'); document.body.classList.remove('new-modal-open');
  }
  function saveTaxonomy(action) {
    if (taxonomyBusy) return;
    taxonomyBusy = true;
    var body = {kind:taxonomyKind.value, action:action, name:taxonomyName.value.trim(), sort_order:Number(taxonomyOrder.value || 0)};
    if (action !== 'create' && taxonomyCurrent) body.id = taxonomyCurrent.id;
    if (taxonomyKind.value === 'category') body.parent_id = taxonomyParent.value || null;
    fetch(root.dataset.taxonomySaveUrl, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (data) { if (!r.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить'); return data; }); })
      .then(function (data) {
        var oldCategory = category.value, oldSub = sub.value, y = window.scrollY;
        taxonomy = data.taxonomy; buildTagPickers();
        category.innerHTML = '<option value="">Все категории</option>' + Object.keys(taxonomy.categories || {}).map(function (name) { return '<option value="' + esc(name) + '">' + esc(name) + '</option>'; }).join('');
        category.value = oldCategory;
        if (category.value !== oldCategory) { groups.innerHTML = ''; prompt.hidden = false; more.hidden = true; loadedCount = 0; nextOffset = null; }
        sub.innerHTML = '<option value="">Вся категория</option>' + (taxonomy.categories[oldCategory] || []).map(function (v) { return '<option value="' + esc(v) + '">' + esc(v) + '</option>'; }).join('');
        sub.value = oldSub;
        taxonomyCurrent = null; populateTaxonomyEditor(); taxonomyError.hidden = true;
        if (category.value) { refreshState = {loaded:loadedCount, scrollY:y}; load(true); }
      })
      .catch(function (err) { taxonomyError.textContent = err.message; taxonomyError.hidden = false; })
      .finally(function () { taxonomyBusy = false; });
  }
  document.getElementById('cz-pool-taxonomy-open').addEventListener('click', openTaxonomyEditor);
  taxonomyModal.querySelectorAll('[data-taxonomy-close]').forEach(function (el) { el.addEventListener('click', closeTaxonomyEditor); });
  taxonomyKind.addEventListener('change', function () { taxonomyCurrent = null; populateTaxonomyEditor(); });
  taxonomyItem.addEventListener('change', function () {
    var entries = taxonomyKind.value === 'category' ? categoryNodes() : (taxonomy.type_nodes || []);
    taxonomyCurrent = entries.find(function (item) { return String(item.id) === taxonomyItem.value; }) || null;
    populateTaxonomyEditor();
  });
  document.getElementById('cz-taxonomy-new').addEventListener('click', function () { taxonomyCurrent = null; taxonomyItem.value = ''; populateTaxonomyEditor(); taxonomyName.focus(); });
  document.getElementById('cz-taxonomy-save').addEventListener('click', function () { saveTaxonomy(taxonomyCurrent ? 'save' : 'create'); });
  document.getElementById('cz-taxonomy-delete').addEventListener('click', function () { if (taxonomyCurrent) saveTaxonomy('delete'); });
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && !taxonomyModal.hidden) closeTaxonomyEditor(); });
})();
