(function () {
  'use strict';
  var root = document.getElementById('cz-pool'); if (!root) return;
  var taxonomy = JSON.parse(document.getElementById('censorly-pool-taxonomy').textContent || '{}');
  var topicPicker = document.getElementById('cz-pool-topics'), typePicker = document.getElementById('cz-pool-types');
  var topicSearch = document.querySelector('[data-pool-topic-search]');
  var typeSearch = document.querySelector('[data-pool-type-search]');
  var taxonomyIcons = window.CensorlyTaxonomyIcons || {};
  var topicIcons = taxonomyIcons.categories || {};
  var typeIcons = taxonomyIcons.types || {};
  var category = document.getElementById('cz-pool-category'), sub = document.getElementById('cz-pool-subcategory');
  var groups = document.getElementById('cz-pool-groups'), modal = document.getElementById('cz-pool-modal'), current = '';
  var more = document.getElementById('cz-pool-more'), prompt = document.getElementById('cz-pool-prompt'), nextOffset = null, loadedCount = 0, busy = false, requestId = 0, controller = null, refreshState = null;
  var status = document.getElementById('cz-pool-status');
  var saveButton = document.getElementById('cz-pool-save'), saveBusy = false;
  var modalOpener = null, taxonomyOpener = null;
  function csrf() { var el = document.querySelector('[name=csrfmiddlewaretoken]'); return el ? el.value : ''; }
  function buildTagPickers() {
    topicPicker.innerHTML = ''; typePicker.innerHTML = '';
    var nodes = (taxonomy.tree || []).slice();
    if (!nodes.length) {
      var paths = new Set();
      Object.keys(taxonomy.categories || {}).forEach(function (parent) {
        paths.add(parent);
        (taxonomy.categories[parent] || []).forEach(function (relative) {
          var path = parent;
          relative.split('/').forEach(function (part) { path += '/' + part; paths.add(path); });
        });
      });
      nodes = Array.from(paths).map(function (path) {
        var parts = path.split('/');
        return {id:path, name:parts[parts.length - 1], parent_id:parts.length > 1 ? parts.slice(0, -1).join('/') : null, path:path, sort_order:0};
      });
    }
    var children = {};
    nodes.forEach(function (node) {
      var parentKey = node.parent_id == null ? 'root' : String(node.parent_id);
      (children[parentKey] || (children[parentKey] = [])).push(node);
    });
    function renderCategory(node, depth, container) {
      var item = document.createElement('div'); item.className = 'censorly-pool-category-node'; item.dataset.poolCategoryNode = node.path;
      var line = document.createElement('div'); line.className = 'censorly-pool-category-line';
      var label = document.createElement('label'); label.className = 'censorly-pool-tag-option';
      var input = document.createElement('input'); input.type = 'checkbox'; input.value = node.path; input.dataset.poolTag = 'topic';
      var icon = document.createElement('i'); icon.className = 'ph ' + (depth === 0 ? (topicIcons[node.name] || 'ph-folder-simple') : 'ph-folder'); icon.setAttribute('aria-hidden', 'true');
      var name = document.createElement('span'); name.textContent = node.name;
      label.append(input, icon, name); line.append(label);
      var childNodes = children[String(node.id)] || [];
      if (childNodes.length) {
        var expand = document.createElement('button'); expand.type = 'button'; expand.className = 'censorly-pool-category-expand';
        expand.setAttribute('aria-label', 'Раскрыть ' + node.name); expand.setAttribute('aria-expanded', 'false');
        expand.innerHTML = '<i class="ph ph-caret-down" aria-hidden="true"></i>'; line.append(expand);
      }
      item.append(line);
      if (childNodes.length) {
        var nested = document.createElement('div'); nested.className = 'censorly-pool-category-children'; nested.hidden = true;
        childNodes.forEach(function (child) { renderCategory(child, depth + 1, nested); }); item.append(nested);
      }
      container.append(item);
    }
    (children.root || []).forEach(function (node) { renderCategory(node, 0, topicPicker); });
    (taxonomy.types || []).forEach(function (type) {
      var label = document.createElement('label'); label.className = 'censorly-pool-type-chip';
      var input = document.createElement('input'); input.type = 'checkbox'; input.value = type; input.dataset.poolTag = 'type';
      var icon = document.createElement('i'); icon.className = 'ph ' + (typeIcons[type] || 'ph-tag'); icon.setAttribute('aria-hidden', 'true');
      var name = document.createElement('span'); name.textContent = type;
      label.append(input, icon, name); typePicker.append(label);
    });
    updateTagCounts();
  }
  buildTagPickers();
  function setChecks(container, values) {
    var selected = new Set(values || []);
    container.querySelectorAll('input[type="checkbox"]').forEach(function (input) { input.checked = selected.has(input.value); });
  }
  function getChecks(container) {
    return Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(function (input) { return input.value; });
  }
  function updateTagCounts() {
    var topicCount = topicPicker.querySelectorAll('[data-pool-tag="topic"]:checked').length;
    var typeCount = typePicker.querySelectorAll('[data-pool-tag="type"]:checked').length;
    document.querySelector('[data-pool-topic-count]').textContent = topicCount ? 'Выбрано: ' + topicCount : 'Категории не выбраны';
    document.querySelector('[data-pool-type-count]').textContent = typeCount ? 'Выбрано: ' + typeCount : 'Типы не выбраны';
    var error = document.getElementById('cz-pool-error');
    if (!error.hidden && ((error.textContent.indexOf('категорию') !== -1 && topicCount) || (error.textContent.indexOf('тип объекта') !== -1 && typeCount))) error.hidden = true;
  }
  topicPicker.addEventListener('change', updateTagCounts);
  typePicker.addEventListener('change', updateTagCounts);
  typeSearch.addEventListener('input', function (event) {
    var query = event.target.value.trim().toLocaleLowerCase('ru');
    typePicker.querySelectorAll('.censorly-pool-type-chip').forEach(function (chip) {
      chip.hidden = Boolean(query) && chip.textContent.toLocaleLowerCase('ru').indexOf(query) === -1;
    });
  });
  topicPicker.addEventListener('click', function (event) {
    var expand = event.target.closest('.censorly-pool-category-expand'); if (!expand) return;
    var item = expand.closest('[data-pool-category-node]');
    var nested = item.querySelector(':scope > .censorly-pool-category-children');
    var opening = nested.hidden; nested.hidden = !opening;
    expand.setAttribute('aria-expanded', String(opening));
    expand.setAttribute('aria-label', (opening ? 'Свернуть ' : 'Раскрыть ') + item.querySelector(':scope > .censorly-pool-category-line span').textContent);
    expand.querySelector('i').className = 'ph ' + (opening ? 'ph-caret-up' : 'ph-caret-down');
  });
  topicSearch.addEventListener('input', function (event) {
    var query = event.target.value.trim().toLocaleLowerCase('ru');
    var items = Array.from(topicPicker.querySelectorAll('[data-pool-category-node]'));
    var visiblePaths = new Set();
    if (query) items.forEach(function (item) {
      var path = item.dataset.poolCategoryNode;
      var name = item.querySelector(':scope > .censorly-pool-category-line span').textContent.toLocaleLowerCase('ru');
      if (name.indexOf(query) === -1) return;
      var parts = path.split('/');
      for (var length = 1; length <= parts.length; length += 1) visiblePaths.add(parts.slice(0, length).join('/'));
    });
    items.forEach(function (item) {
      var path = item.dataset.poolCategoryNode;
      item.hidden = Boolean(query) && !visiblePaths.has(path);
      var nested = item.querySelector(':scope > .censorly-pool-category-children');
      var expand = item.querySelector(':scope > .censorly-pool-category-line .censorly-pool-category-expand');
      if (nested) {
        nested.hidden = !query;
        if (expand) { expand.setAttribute('aria-expanded', String(Boolean(query))); expand.querySelector('i').className = 'ph ' + (query ? 'ph-caret-up' : 'ph-caret-down'); }
      }
    });
  });
  document.querySelector('[data-pool-topic-clear]').addEventListener('click', function () {
    topicPicker.querySelectorAll('[data-pool-tag="topic"]').forEach(function (input) { input.checked = false; }); updateTagCounts();
  });
  document.querySelector('[data-pool-type-clear]').addEventListener('click', function () {
    typePicker.querySelectorAll('[data-pool-tag="type"]').forEach(function (input) { input.checked = false; }); updateTagCounts();
  });
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
      more.hidden = true;
    }
    if (busy) return;
    busy = true; more.disabled = true;
    var currentRequest = ++requestId;
    var pageLoaded = false;
    controller = new AbortController();
    var url = new URL(root.dataset.listUrl, location.href); url.searchParams.set('category', category.value); if (sub.value) url.searchParams.set('subcategory', sub.value); url.searchParams.set('offset', nextOffset || 0);
    fetch(url, {credentials:'same-origin', signal:controller.signal}).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok || data.ok === false || !data.groups) throw new Error(data.error || 'Не удалось загрузить статьи');
        return data;
      });
    }).then(function (data) {
      if (currentRequest !== requestId) return;
      pageLoaded = true;
      status.textContent = '';
      prompt.hidden = true;
      Object.keys(data.groups).forEach(function (level) {
        var section = Array.from(groups.querySelectorAll('[data-pool-level]')).find(function (node) { return node.dataset.poolLevel === level; });
        if (!section) { section = document.createElement('section'); section.className = 'pal-card support-card'; section.dataset.poolLevel = level; section.innerHTML = '<h2 class="pal-title">' + esc(level) + '</h2><div class="censorly-pool-level-rows"></div>'; groups.append(section); }
        section.querySelector('.censorly-pool-level-rows').insertAdjacentHTML('beforeend', data.groups[level].map(articleHtml).join(''));
        loadedCount += data.groups[level].length;
      });
      nextOffset = data.next_offset; more.hidden = nextOffset === null; more.textContent = 'Показать ещё · осталось ' + Math.max(0, data.total - (nextOffset || data.total));
      if (!data.total) { groups.innerHTML = '<p class="support-empty">В этой категории статей нет.</p>'; more.hidden = true; }
    }).catch(function (error) { if (error.name !== 'AbortError' && currentRequest === requestId) status.textContent = error.message; }).finally(function () {
      if (currentRequest !== requestId) return;
      busy = false; controller = null; more.disabled = false;
      if (pageLoaded && refreshState && loadedCount < refreshState.loaded && nextOffset !== null) { load(false); return; }
      if (refreshState) { var y = refreshState.scrollY; refreshState = null; requestAnimationFrame(function () { window.scrollTo(0, y); }); }
    });
  }
  category.addEventListener('change', function () {
    sub.innerHTML = '<option value="">Вся категория</option>' + (taxonomy.categories[category.value] || []).map(function (v) { return '<option>' + esc(v) + '</option>'; }).join('');
    sub.value = '';
    if (!category.value) { requestId += 1; if (controller) controller.abort(); controller = null; busy = false; groups.innerHTML = ''; prompt.hidden = false; more.hidden = true; nextOffset = null; status.textContent = ''; return; }
    load(true);
  });
  sub.addEventListener('change', function () { load(true); });
  more.addEventListener('click', function () { load(false); });
  function open(row) {
    if (saveBusy) return;
    modalOpener = document.activeElement;
    document.getElementById('cz-pool-error').hidden = true;
    current = row.title || ''; document.getElementById('cz-pool-title').value = current;
    setChecks(topicPicker, row.topics); setChecks(typePicker, row.types);
    topicSearch.value = ''; topicSearch.dispatchEvent(new Event('input'));
    typeSearch.value = ''; typeSearch.dispatchEvent(new Event('input')); updateTagCounts();
    document.getElementById('cz-pool-difficulty').value = row.difficulty || 'средне'; document.getElementById('cz-pool-enabled').checked = row.enabled !== false;
    document.getElementById('cz-pool-modal-title').textContent = current ? 'Изменить статью и теги' : 'Добавить статью';
    modal.hidden = false; modal.classList.add('is-open'); document.body.classList.add('new-modal-open');
    document.getElementById('cz-pool-title').focus();
  }
  function close(force, restoreFocus) {
    if (saveBusy && !force) return;
    modal.hidden = true; modal.classList.remove('is-open'); document.body.classList.remove('new-modal-open');
    if (restoreFocus !== false && modalOpener && modalOpener.isConnected) modalOpener.focus();
    modalOpener = null;
  }
  function save(body) {
    if (saveBusy) return;
    var modalSaving = !modal.hidden;
    var modalControls = modalSaving ? Array.from(modal.querySelectorAll('input, select, button')).map(function (control) {
      var wasDisabled = control.disabled; control.disabled = true; return {control:control, disabled:wasDisabled};
    }) : [];
    saveBusy = true; saveButton.disabled = true;
    if (modalSaving) { modal.setAttribute('aria-busy', 'true'); saveButton.textContent = 'Сохраняем…'; }
    status.textContent = '';
    fetch(root.dataset.saveUrl, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(body)})
      .then(function (r) { return r.json().then(function (data) { if (!r.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить статью'); return data; }); })
      .then(function () {
        close(true, false);
        (category.value ? category : document.getElementById('cz-pool-add')).focus({preventScroll:true});
        if (category.value) { refreshState = {loaded: loadedCount, scrollY: window.scrollY}; load(true); }
      })
      .catch(function (err) {
        if (modal.hidden) status.textContent = err.message;
        else { var box = document.getElementById('cz-pool-error'); box.textContent = err.message; box.hidden = false; }
      })
      .finally(function () {
        modalControls.forEach(function (item) { item.control.disabled = item.disabled; });
        modal.removeAttribute('aria-busy'); saveButton.textContent = 'Сохранить';
        saveBusy = false; saveButton.disabled = false;
      });
  }
  document.getElementById('cz-pool-add').addEventListener('click', function () {
    var initialTopic = category.value ? category.value + (sub.value ? '/' + sub.value : '') : '';
    open({topics: initialTopic ? [initialTopic] : []});
  });
  groups.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-edit], [data-delta]'); if (!btn) return;
    var row = JSON.parse(decodeURIComponent(btn.dataset.edit || btn.dataset.row));
    if (btn.hasAttribute('data-edit')) return open(row);
    var levels = ['легко','средне','сложно','очень сложно'], next = Math.max(0, Math.min(3, levels.indexOf(row.difficulty) + Number(btn.dataset.delta)));
    save({title:row.title, replacement_title:row.title, topics:row.topics, types:row.types, difficulty:levels[next], enabled:row.enabled});
  });
  modal.querySelectorAll('[data-pool-close]').forEach(function (el) { el.addEventListener('click', function () { close(); }); });
  saveButton.addEventListener('click', function () {
    var topics = getChecks(topicPicker), types = getChecks(typePicker), enabled = document.getElementById('cz-pool-enabled').checked;
    var error = document.getElementById('cz-pool-error');
    var titleInput = document.getElementById('cz-pool-title');
    if (!titleInput.value.trim()) { error.textContent = 'Укажите название статьи'; error.hidden = false; titleInput.focus(); return; }
    if (enabled && !topics.length) { error.textContent = 'Выберите хотя бы одну категорию'; error.hidden = false; return; }
    if (enabled && !types.length) { error.textContent = 'Выберите хотя бы один тип объекта'; error.hidden = false; return; }
    save({title:current, replacement_title:document.getElementById('cz-pool-title').value, topics:topics, types:types, difficulty:document.getElementById('cz-pool-difficulty').value, enabled:enabled});
  });
  var taxonomyModal = document.getElementById('cz-pool-taxonomy-modal');
  var taxonomyKind = document.getElementById('cz-taxonomy-kind');
  var taxonomyItem = document.getElementById('cz-taxonomy-item');
  var taxonomyName = document.getElementById('cz-taxonomy-name');
  var taxonomyParent = document.getElementById('cz-taxonomy-parent');
  var taxonomyOrder = document.getElementById('cz-taxonomy-order');
  var taxonomyError = document.getElementById('cz-taxonomy-error');
  var taxonomyCurrent = null, taxonomyBusy = false;
  function categoryNodes() { return taxonomy.all_tree || taxonomy.tree || []; }
  function taxonomyLabel(item) { return '　'.repeat((item.path.match(/\//g) || []).length) + item.name; }
  function populateTaxonomyEditor() {
    var kind = taxonomyKind.value, selectedId = taxonomyCurrent && String(taxonomyCurrent.id);
    var entries = kind === 'category' ? categoryNodes() : (taxonomy.all_type_nodes || taxonomy.type_nodes || (taxonomy.types || []).map(function (name, index) { return {id:name, name:name, path:name, sort_order:index, is_active:true}; }));
    taxonomyItem.innerHTML = '<option value="">Новая запись</option>' + entries.map(function (item) {
      var label = kind === 'category' ? taxonomyLabel(item) : item.name;
      return '<option value="' + esc(item.id) + '">' + esc(label + (item.is_active === false ? ' (выключена)' : '')) + '</option>';
    }).join('');
    taxonomyItem.value = entries.some(function (item) { return String(item.id) === selectedId; }) ? selectedId : '';
    document.getElementById('cz-taxonomy-parent-wrap').hidden = kind !== 'category';
    taxonomyParent.innerHTML = '<option value="">Верхний уровень</option>' + categoryNodes().filter(function (item) {
      var isCurrentParent = taxonomyCurrent && String(item.id) === String(taxonomyCurrent.parent_id);
      var outsideCurrentBranch = !taxonomyCurrent || (String(item.id) !== String(taxonomyCurrent.id) && !item.path.startsWith(taxonomyCurrent.path + '/'));
      return outsideCurrentBranch && (item.is_active !== false || isCurrentParent);
    }).map(function (item) {
      return '<option value="' + esc(item.id) + '">' + esc(item.path) + (item.is_active === false ? ' (выключена)' : '') + '</option>';
    }).join('');
    var selected = entries.find(function (item) { return String(item.id) === taxonomyItem.value; });
    taxonomyCurrent = selected || null;
    taxonomyName.value = selected ? selected.name : '';
    taxonomyOrder.value = selected && selected.sort_order !== undefined ? selected.sort_order : '0';
    document.getElementById('cz-taxonomy-active').checked = selected ? selected.is_active !== false : true;
    taxonomyParent.value = kind === 'category' && selected && selected.parent_id ? String(selected.parent_id) : '';
    document.getElementById('cz-taxonomy-delete').disabled = !selected;
  }
  function openTaxonomyEditor() {
    taxonomyOpener = document.activeElement;
    taxonomyCurrent = null; taxonomyKind.value = 'category'; populateTaxonomyEditor();
    taxonomyError.hidden = true; taxonomyModal.hidden = false; taxonomyModal.classList.add('is-open'); document.body.classList.add('new-modal-open');
    taxonomyKind.focus();
  }
  function closeTaxonomyEditor() {
    if (taxonomyBusy) return;
    taxonomyModal.hidden = true; taxonomyModal.classList.remove('is-open'); document.body.classList.remove('new-modal-open');
    if (taxonomyOpener && taxonomyOpener.isConnected) taxonomyOpener.focus();
    taxonomyOpener = null;
  }
  function saveTaxonomy(action) {
    if (taxonomyBusy) return;
    taxonomyBusy = true;
    var body = {kind:taxonomyKind.value, action:action, name:taxonomyName.value.trim(), sort_order:Number(taxonomyOrder.value || 0), is_active:document.getElementById('cz-taxonomy-active').checked};
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
    var entries = taxonomyKind.value === 'category' ? categoryNodes() : (taxonomy.all_type_nodes || taxonomy.type_nodes || []);
    taxonomyCurrent = entries.find(function (item) { return String(item.id) === taxonomyItem.value; }) || null;
    populateTaxonomyEditor();
  });
  document.getElementById('cz-taxonomy-new').addEventListener('click', function () { taxonomyCurrent = null; taxonomyItem.value = ''; populateTaxonomyEditor(); taxonomyName.focus(); });
  document.getElementById('cz-taxonomy-save').addEventListener('click', function () { saveTaxonomy(taxonomyCurrent ? 'save' : 'create'); });
  document.getElementById('cz-taxonomy-delete').addEventListener('click', function () { if (taxonomyCurrent) saveTaxonomy('delete'); });
  document.addEventListener('keydown', function (event) {
    var activeModal = !taxonomyModal.hidden ? taxonomyModal : (!modal.hidden ? modal : null);
    if (!activeModal) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      if (activeModal === taxonomyModal) closeTaxonomyEditor(); else close();
      return;
    }
    if (event.key !== 'Tab') return;
    var focusable = Array.from(activeModal.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'))
      .filter(function (element) { return !element.hidden && element.getClientRects().length; });
    if (!focusable.length) { event.preventDefault(); activeModal.querySelector('.new-rules-modal__box').focus(); return; }
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !activeModal.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !activeModal.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  });
})();
