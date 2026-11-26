/**
 * Строка проверки под живым превью разбора криптика: консоль редакторов
 * (support/simple_daily.html) и «Создать свой Криптик» (site/create_cryptic.html).
 *
 * ``data`` — ответ cryptic_editor_preview (games/daily/catalog/cryptic.py):
 * html, errors, warnings, answer, empty и answer_error — ответ не годится
 * или не сходится с маской (такой ответ игрок не сможет ввести в поле).
 */
(function (global) {
  'use strict';

  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function list(items, cls) {
    return '<ul' + (cls ? ' class="' + cls + '"' : '') + '>' +
      items.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') + '</ul>';
  }

  /** Fill ``check`` from ``data``; ``emptyText`` is shown while the breakdown is empty. */
  function render(check, data, emptyText) {
    var parts = [];
    var bad = false;
    if (data.answer_error) {
      bad = true;
      parts.push('<p class="cx-check__answer"><i class="ph ph-warning-circle" aria-hidden="true"></i> ' +
        escapeHtml(data.answer_error) + '</p>');
    }
    if (data.empty) {
      parts.push(escapeHtml(emptyText));
    } else if (data.errors && data.errors.length) {
      bad = true;
      parts.push('<i class="ph ph-warning-circle" aria-hidden="true"></i> Разбор не сходится:' + list(data.errors));
    } else {
      parts.push('<i class="ph ph-check-circle" aria-hidden="true"></i> Формула даёт ' + escapeHtml(data.answer || '') +
        (data.warnings && data.warnings.length ? list(data.warnings, 'cx-check__warn') : ''));
    }
    check.className = 'cx-check' + (bad ? ' cx-check--err' : (data.empty ? '' : ' cx-check--ok'));
    check.innerHTML = parts.join('');
  }

  global.CrypticPreviewCheck = { render: render };
})(typeof window !== 'undefined' ? window : globalThis);
