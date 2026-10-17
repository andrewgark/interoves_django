(function () {
  'use strict';
  var form = document.querySelector('[data-random-censorly-form]');
  if (!form) return;
  var button = form.querySelector('[data-random-censorly-open]');
  var launcher = form.querySelector('[data-random-censorly-launcher]');
  var settings = launcher.querySelector('[data-random-censorly-settings]');
  var settingsToggle = launcher.querySelector('[data-random-censorly-settings-toggle]');
  var playButton = launcher.querySelector('[data-random-censorly-play]');
  var filtersInput = form.querySelector('[data-random-censorly-filters]');
  var taxonomyNode = document.getElementById('censorly-random-taxonomy');
  var taxonomy = JSON.parse(taxonomyNode ? taxonomyNode.textContent : '{}') || {};
  var categoryRoot = settings.querySelector('[data-filter-categories]');
  var typeRoot = settings.querySelector('[data-filter-types]');
  var validation = settings.querySelector('[data-random-censorly-validation]');
  var taxonomyIcons = window.CensorlyTaxonomyIcons || {};
  var categoryIcons = taxonomyIcons.categories || {};
  var typeIcons = taxonomyIcons.types || {};
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char];
    });
  }
  function ruSectionWord(count) {
    var remainder100 = count % 100;
    if (remainder100 >= 11 && remainder100 <= 14) return 'разделов';
    var remainder10 = count % 10;
    if (remainder10 === 1) return 'раздел';
    if (remainder10 >= 2 && remainder10 <= 4) return 'раздела';
    return 'разделов';
  }
  function readSaved() {
    try {
      var value = JSON.parse(localStorage.getItem('censorly-random-filters') || 'null');
      return value && typeof value === 'object' ? value : {};
    } catch (_) { return {}; }
  }
  var saved = readSaved();
  function pathsFromTaxonomy() {
    var entries = taxonomy.tree || [];
    var byParent = {};
    entries.forEach(function (node) {
      var parentKey = node.parent_id == null ? 'root' : String(node.parent_id);
      (byParent[parentKey] || (byParent[parentKey] = [])).push(node);
    });
    function sortNodes(nodes) {
      return (nodes || []).slice().sort(function (a, b) {
        return Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.name.localeCompare(b.name, 'ru');
      });
    }
    var ordered = [];
    function visit(nodes) {
      sortNodes(nodes).forEach(function (node) {
        if (node.path) ordered.push(node.path);
        visit(byParent[String(node.id)]);
      });
    }
    if (entries.length) visit(byParent.root);
    if (!ordered.length) {
      Object.keys(taxonomy.categories || {}).forEach(function (root) {
        ordered.push(root);
        (taxonomy.categories[root] || []).forEach(function (child) {
          var path = root;
          child.split('/').forEach(function (part) { path += '/' + part; ordered.push(path); });
        });
      });
    }
    return Array.from(new Set(ordered));
  }
  var categoryPaths = pathsFromTaxonomy();
  var categoryIdToPath = {};
  var categoryPathToId = {};
  (taxonomy.tree || []).forEach(function (node) {
    if (node.id != null && node.path) {
      categoryIdToPath[String(node.id)] = node.path;
      categoryPathToId[node.path] = String(node.id);
    }
  });
  var hasStableCategoryIds = Object.keys(categoryIdToPath).length > 0;
  var categoryNodes = {};
  categoryPaths.forEach(function (path) {
    var parts = path.split('/');
    var current = '';
    parts.forEach(function (part, index) {
      current = current ? current + '/' + part : part;
      if (!categoryNodes[current]) categoryNodes[current] = {path:current, name:part, children:[]};
      if (index > 0) {
        var parentPath = parts.slice(0, index).join('/');
        var parent = categoryNodes[parentPath];
        if (parent && parent.children.indexOf(current) === -1) parent.children.push(current);
      }
    });
  });
  // Keep the order configured in the taxonomy editor; alphabetical ordering
  // makes the filter disagree with the pool management screen.
  var roots = Object.keys(categoryNodes).filter(function (path) { return path.indexOf('/') === -1; });
  var types = (taxonomy.types || []).slice();
  var difficulties = ['легко', 'средне', 'сложно', 'очень сложно'];
  var defaultDifficulties = ['легко', 'средне'];
  var selectedCategories;
  if (saved.version === 3 && Array.isArray(saved.category_ids)) {
    selectedCategories = new Set(saved.category_ids.map(function (id) {
      var path = categoryIdToPath[String(id)];
      if (!path && !hasStableCategoryIds && categoryNodes[String(id)]) path = String(id);
      return path;
    }).filter(function (path) { return path && categoryNodes[path]; }));
  } else if (Array.isArray(saved.categories)) {
    // Upgrade the previous path-based preference without changing selection.
    selectedCategories = new Set(saved.categories.filter(function (path) { return categoryNodes[path]; }));
  } else {
    selectedCategories = new Set(categoryPaths);
  }
  var selectedTypes = Array.isArray(saved.types)
    ? new Set(saved.types.filter(function (type) { return types.indexOf(type) !== -1; }))
    : new Set(types);
  var selectedDifficulties = Array.isArray(saved.difficulties)
    ? new Set(saved.difficulties.filter(function (value) { return difficulties.indexOf(value) !== -1; }))
    : new Set(defaultDifficulties);
  Array.from(selectedCategories).forEach(function (path) {
    categoryPaths.forEach(function (candidate) {
      if (candidate.indexOf(path + '/') === 0) selectedCategories.add(candidate);
    });
  });

  function appendCategory(path, depth, container) {
    var node = categoryNodes[path];
    if (!node) return;
    var row = document.createElement('div');
    row.className = 'censorly-category-tree__node';
    row.dataset.categoryNode = path;
    var line = document.createElement('div');
    line.className = 'censorly-category-tree__line';
    var checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.value = path; checkbox.dataset.filterCategory = '1';
    checkbox.checked = selectedCategories.has(path); checkbox.setAttribute('aria-label', 'Выбрать категорию ' + node.name);
    var icon = document.createElement('i');
    icon.className = 'ph ' + (depth === 0 ? (categoryIcons[node.name] || 'ph-folder-simple') : 'ph-folder');
    icon.setAttribute('aria-hidden', 'true');
    var label = document.createElement('label');
    label.className = 'censorly-category-tree__label';
    var name = document.createElement('span'); name.textContent = node.name;
    var amount = document.createElement('small');
    if (node.children.length) amount.textContent = node.children.length + ' ' + ruSectionWord(node.children.length);
    label.append(checkbox, icon, name);
    if (node.children.length) label.append(amount);
    line.append(label);
    if (node.children.length) {
      var expand = document.createElement('button');
      expand.type = 'button'; expand.className = 'censorly-category-tree__expand';
      expand.setAttribute('aria-label', 'Раскрыть ' + node.name); expand.setAttribute('aria-expanded', 'false');
      expand.innerHTML = '<i class="ph ph-caret-down" aria-hidden="true"></i>';
      line.append(expand);
    }
    row.append(line);
    if (node.children.length) {
      var children = document.createElement('div');
      children.className = 'censorly-category-tree__children'; hidden(children, true);
      node.children.forEach(function (child) { appendCategory(child, depth + 1, children); });
      row.append(children);
    }
    container.append(row);
  }
  function hidden(element, value) {
    element.hidden = value;
    element.classList.toggle('is-hidden', value);
  }
  roots.forEach(function (path) { appendCategory(path, 0, categoryRoot); });

  types.forEach(function (type) {
    var label = document.createElement('label');
    label.className = 'censorly-type-chip';
    var input = document.createElement('input'); input.type = 'checkbox'; input.value = type;
    input.dataset.filterType = '1'; input.checked = selectedTypes.has(type);
    var icon = document.createElement('i'); icon.className = 'ph ' + (typeIcons[type] || 'ph-tag'); icon.setAttribute('aria-hidden', 'true');
    var name = document.createElement('span'); name.textContent = type;
    label.append(input, icon, name); typeRoot.append(label);
  });
  settings.querySelectorAll('[data-filter-difficulty]').forEach(function (input) {
    input.checked = selectedDifficulties.has(input.value);
  });

  function descendants(path) {
    return Array.from(categoryRoot.querySelectorAll('[data-filter-category]')).filter(function (input) {
      return input.value === path || input.value.indexOf(path + '/') === 0;
    });
  }
  function updateCategoryStates() {
    var all = Array.from(categoryRoot.querySelectorAll('[data-filter-category]'));
    all.forEach(function (input) { input.checked = selectedCategories.has(input.value); input.indeterminate = false; });
    Object.keys(categoryNodes).sort(function (a, b) { return b.split('/').length - a.split('/').length; }).forEach(function (path) {
      var input = categoryRoot.querySelector('[data-filter-category][value="' + CSS.escape(path) + '"]');
      if (!input || !categoryNodes[path].children.length) return;
      var childInputs = descendants(path).filter(function (item) { return item !== input; });
      var checked = childInputs.filter(function (item) { return item.checked; }).length;
      if (childInputs.length && checked === childInputs.length) input.checked = true;
      else if (checked > 0 || input.checked) { input.checked = false; input.indeterminate = true; }
    });
    updateCounts();
  }
  function compressedCategories() {
    var output = [];
    function visit(path, ancestorSelected) {
      var input = categoryRoot.querySelector('[data-filter-category][value="' + CSS.escape(path) + '"]');
      if (!input) return;
      if (input.checked && !ancestorSelected) { output.push(path); return; }
      categoryNodes[path].children.forEach(function (child) { visit(child, ancestorSelected || input.checked); });
      if (!categoryNodes[path].children.length && input.checked && !ancestorSelected) output.push(path);
    }
    roots.forEach(function (root) { visit(root, false); });
    return output;
  }
  function categoryIds(paths) {
    return paths.map(function (path) { return categoryPathToId[path] || path; });
  }
  function persistSelection() {
    var categories = compressedCategories();
    var state = {
      version:3,
      categories:categories,
      category_ids:categoryIds(categories),
      types:Array.from(selectedTypes),
      difficulties:Array.from(selectedDifficulties)
    };
    try { localStorage.setItem('censorly-random-filters', JSON.stringify(state)); } catch (_) {}
  }
  function updateCounts() {
    var selectedPaths = compressedCategories();
    var categoryCount = settings.querySelector('[data-category-count]');
    var allRootsSelected = roots.length > 0 && roots.every(function (root) { return selectedPaths.indexOf(root) !== -1; });
    categoryCount.textContent = allRootsSelected ? 'Все категории' : selectedPaths.length ? 'Выбрано веток: ' + selectedPaths.length : 'Ничего не выбрано';
    var typeSelected = typeRoot.querySelectorAll('[data-filter-type]:checked').length;
    settings.querySelector('[data-type-count]').textContent = typeSelected + ' из ' + types.length + ' выбрано';
  }
  settings.querySelector('[data-category-search]').addEventListener('input', function (event) {
    var query = event.target.value.trim().toLocaleLowerCase('ru');
    function hasMatch(path) {
      var node = categoryNodes[path];
      return node.name.toLocaleLowerCase('ru').indexOf(query) !== -1 || node.children.some(hasMatch);
    }
    function visibleByQuery(path) {
      var parts = path.split('/');
      for (var length = 1; length <= parts.length; length += 1) {
        if (parts.slice(0, length).join('/').split('/').pop().toLocaleLowerCase('ru').indexOf(query) !== -1) return true;
      }
      return hasMatch(path);
    }
    Object.keys(categoryNodes).sort(function (a, b) { return a.split('/').length - b.split('/').length; }).forEach(function (path) {
      var row = categoryRoot.querySelector('[data-category-node="' + CSS.escape(path) + '"]');
      var show = !query || visibleByQuery(path);
      hidden(row, !show);
      var children = row.querySelector(':scope > .censorly-category-tree__children');
      var expand = row.querySelector(':scope > .censorly-category-tree__line .censorly-category-tree__expand');
      if (children && query) {
        hidden(children, false);
        if (expand) { expand.setAttribute('aria-expanded', 'true'); expand.querySelector('i').className = 'ph ph-caret-up'; }
      } else if (children && !query) {
        hidden(children, true);
        if (expand) { expand.setAttribute('aria-expanded', 'false'); expand.querySelector('i').className = 'ph ph-caret-down'; }
      }
    });
  });
  function selectAllCategories(checked) {
    categoryRoot.querySelectorAll('[data-filter-category]').forEach(function (input) {
      if (checked) selectedCategories.add(input.value); else selectedCategories.delete(input.value);
    });
    updateCategoryStates();
    persistSelection();
  }
  categoryRoot.addEventListener('change', function (event) {
    var input = event.target.closest('[data-filter-category]');
    if (!input) return;
    descendants(input.value).forEach(function (item) {
      if (input.checked) selectedCategories.add(item.value); else selectedCategories.delete(item.value);
    });
    if (!input.checked) {
      var parts = input.value.split('/');
      while (parts.length > 1) {
        parts.pop();
        selectedCategories.delete(parts.join('/'));
      }
    }
    updateCategoryStates();
    validation.hidden = true;
    persistSelection();
  });
  categoryRoot.addEventListener('click', function (event) {
    var expand = event.target.closest('.censorly-category-tree__expand');
    if (!expand) return;
    var row = expand.closest('[data-category-node]');
    var children = row.querySelector(':scope > .censorly-category-tree__children');
    var opening = children.hidden;
    hidden(children, !opening); expand.setAttribute('aria-expanded', String(opening));
    expand.setAttribute('aria-label', (opening ? 'Свернуть ' : 'Раскрыть ') + categoryNodes[row.dataset.categoryNode].name);
    expand.querySelector('i').className = 'ph ' + (opening ? 'ph-caret-up' : 'ph-caret-down');
  });
  typeRoot.addEventListener('change', function (event) {
    var input = event.target.closest('[data-filter-type]'); if (!input) return;
    if (input.checked) selectedTypes.add(input.value); else selectedTypes.delete(input.value);
    updateCounts(); validation.hidden = true;
    persistSelection();
  });
  settings.querySelectorAll('[data-filter-difficulty]').forEach(function (input) {
    input.addEventListener('change', function () {
      if (input.checked) selectedDifficulties.add(input.value); else selectedDifficulties.delete(input.value);
      validation.hidden = true;
      persistSelection();
    });
  });
  settings.querySelector('[data-category-select-all]').addEventListener('click', function () { selectAllCategories(true); validation.hidden = true; });
  settings.querySelector('[data-category-clear]').addEventListener('click', function () { selectAllCategories(false); validation.hidden = true; });
  settings.querySelector('[data-type-select-all]').addEventListener('click', function () {
    typeRoot.querySelectorAll('[data-filter-type]').forEach(function (input) { input.checked = true; selectedTypes.add(input.value); });
    updateCounts(); validation.hidden = true;
    persistSelection();
  });
  settings.querySelector('[data-type-clear]').addEventListener('click', function () {
    typeRoot.querySelectorAll('[data-filter-type]').forEach(function (input) { input.checked = false; selectedTypes.delete(input.value); });
    updateCounts(); validation.hidden = true;
    persistSelection();
  });
  settings.querySelector('[data-difficulty-reset]').addEventListener('click', function () {
    selectedDifficulties = new Set();
    settings.querySelectorAll('[data-filter-difficulty]').forEach(function (input) { input.checked = false; });
    validation.hidden = true;
    persistSelection();
  });
  function setSettingsOpen(open) {
    settings.hidden = !open;
    settingsToggle.setAttribute('aria-expanded', String(open));
    settingsToggle.querySelector('span').textContent = open ? 'Скрыть настройки' : 'Настройки случайной игры';
    settingsToggle.querySelector('.censorly-random-settings-toggle__caret').className = 'ph ' + (open ? 'ph-caret-up' : 'ph-caret-down') + ' censorly-random-settings-toggle__caret';
  }
  button.addEventListener('click', function () {
    var open = launcher.hidden;
    launcher.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    if (open) playButton.focus();
  });
  settingsToggle.addEventListener('click', function () {
    var open = settings.hidden;
    setSettingsOpen(open);
    if (open) settings.querySelector('[data-category-search]').focus();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !launcher.hidden) {
      if (!settings.hidden) { setSettingsOpen(false); settingsToggle.focus(); }
      else { launcher.hidden = true; button.setAttribute('aria-expanded', 'false'); button.focus(); }
    }
  });
  playButton.addEventListener('click', function () {
    var chosenDifficulties = Array.from(selectedDifficulties);
    var chosenTypes = Array.from(selectedTypes);
    var chosenCategories = compressedCategories();
    if (!chosenCategories.length || !chosenDifficulties.length || !chosenTypes.length) {
      setSettingsOpen(true);
      validation.hidden = false;
      settingsToggle.focus();
      return;
    }
    var state = {
      version:3,
      categories:chosenCategories,
      category_ids:categoryIds(chosenCategories),
      types:chosenTypes,
      difficulties:chosenDifficulties
    };
    try { localStorage.setItem('censorly-random-filters', JSON.stringify(state)); } catch (_) {}
    filtersInput.value = JSON.stringify({
      categories:state.categories, types:state.types, difficulties:state.difficulties,
      filter_difficulty:true, filter_types:true, filter_categories:true
    });
    launcher.hidden = true; button.setAttribute('aria-expanded', 'false');
    button.disabled = true; button.classList.add('is-loading');
    button.setAttribute('aria-busy', 'true'); form.submit();
  });
  updateCategoryStates();
})();
