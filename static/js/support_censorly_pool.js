(function () {
  'use strict';
  var root = document.getElementById('cz-pool'); if (!root) return;
  var taxonomy = JSON.parse(document.getElementById('censorly-pool-taxonomy').textContent || '{}');
  var category = document.getElementById('cz-pool-category'), sub = document.getElementById('cz-pool-subcategory');
  var groups = document.getElementById('cz-pool-groups'), modal = document.getElementById('cz-pool-modal'), current = '';
  function csrf() { var el = document.querySelector('[name=csrfmiddlewaretoken]'); return el ? el.value : ''; }
  function esc(v) { return String(v || '').replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function load() {
    var url = new URL(root.dataset.listUrl, location.href); if (category.value) url.searchParams.set('category', category.value); if (sub.value) url.searchParams.set('subcategory', sub.value);
    fetch(url, {credentials:'same-origin'}).then(function (r) { return r.json(); }).then(function (data) {
      groups.innerHTML = Object.keys(data.groups).map(function (level) {
        var rows = data.groups[level]; return '<section class="pal-card support-card"><h2 class="pal-title">' + esc(level) + ' <small>(' + rows.length + ')</small></h2>' + (rows.length ? rows.map(function (row) {
          var encoded = esc(encodeURIComponent(JSON.stringify(row)));
          return '<article class="censorly-pool-row' + (row.enabled ? '' : ' is-disabled') + '"><div><strong>' + esc(row.title) + '</strong><small>' + esc(row.topics.join(' · ')) + (row.types.length ? ' · ' + esc(row.types.join(', ')) : '') + '</small></div><button type="button" class="new-btn new-btn--mini new-btn--ghost" data-delta="-1" data-row="' + encoded + '" title="Упростить">↑</button><button type="button" class="new-btn new-btn--mini new-btn--ghost" data-delta="1" data-row="' + encoded + '" title="Усложнить">↓</button><button type="button" class="new-btn new-btn--mini new-btn--ghost" data-edit="' + encoded + '">Теги</button></article>';
        }).join('') : '<p class="support-empty">Нет статей</p>') + '</section>';
      }).join('');
    });
  }
  category.addEventListener('change', function () { sub.innerHTML = '<option value="">Вся категория</option>' + (taxonomy.categories[category.value] || []).map(function (v) { return '<option>' + esc(v) + '</option>'; }).join(''); load(); });
  sub.addEventListener('change', load);
  function open(row) {
    current = row.title || ''; document.getElementById('cz-pool-title').value = current;
    document.getElementById('cz-pool-topics').value = (row.topics || []).join('; '); document.getElementById('cz-pool-types').value = (row.types || []).join('; ');
    document.getElementById('cz-pool-difficulty').value = row.difficulty || 'средне'; document.getElementById('cz-pool-enabled').checked = row.enabled !== false;
    document.getElementById('cz-pool-modal-title').textContent = current ? 'Изменить статью и теги' : 'Добавить статью';
    modal.hidden = false; modal.classList.add('is-open'); document.body.classList.add('new-modal-open');
  }
  function close() { modal.hidden = true; modal.classList.remove('is-open'); document.body.classList.remove('new-modal-open'); }
  function save(body) {
    fetch(root.dataset.saveUrl, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(body)})
      .then(function (r) { return r.json(); }).then(function (d) { if (!d.ok) throw new Error(d.error); close(); load(); })
      .catch(function (err) { var box = document.getElementById('cz-pool-error'); box.textContent = err.message; box.hidden = false; });
  }
  document.getElementById('cz-pool-add').addEventListener('click', function () { open({}); });
  groups.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-edit], [data-delta]'); if (!btn) return;
    var row = JSON.parse(decodeURIComponent(btn.dataset.edit || btn.dataset.row));
    if (btn.hasAttribute('data-edit')) return open(row);
    var levels = ['легко','средне','сложно'], next = Math.max(0, Math.min(2, levels.indexOf(row.difficulty) + Number(btn.dataset.delta)));
    save({title:row.title, replacement_title:row.title, topics:row.topics, types:row.types, difficulty:levels[next], enabled:row.enabled});
  });
  modal.querySelectorAll('[data-pool-close]').forEach(function (el) { el.addEventListener('click', close); });
  document.getElementById('cz-pool-save').addEventListener('click', function () {
    var split = function (v) { return v.split(';').map(function (x) { return x.trim(); }).filter(Boolean); };
    save({title:current, replacement_title:document.getElementById('cz-pool-title').value, topics:split(document.getElementById('cz-pool-topics').value), types:split(document.getElementById('cz-pool-types').value), difficulty:document.getElementById('cz-pool-difficulty').value, enabled:document.getElementById('cz-pool-enabled').checked});
  });
  load();
})();
