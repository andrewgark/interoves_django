(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.CensorlyReloadRequired = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function show(feedback, href, documentRef) {
    if (!feedback) return;
    feedback.hidden = false;
    feedback.className = 'censorly__feedback is-error';
    feedback.textContent = 'Состояние игры обновилось. ';
    var refresh = documentRef.createElement('a');
    refresh.textContent = 'Обновить страницу';
    refresh.href = href;
    feedback.appendChild(refresh);
  }

  return { show: show };
});
