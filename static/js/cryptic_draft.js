/**
 * Страница криптика игрока до приёма (site/cryptic_offer_play.html).
 *
 * Попыток нет: ответ проверяется на странице по солёному хешу
 * (#cryptic-draft-spec, static/js/offline_verify.js) — неверный без сети,
 * за верным идём на сервер за разбором. Поле ответа — маска из
 * static/js/answer_mask.js. Подсказки дня здесь бесплатные: 💡 рядом с
 * «Проверить», как у дня; текст лежит в <template>, вторая заменяет первую.
 */
(function () {
  'use strict';

  var form = document.getElementById('cryptic-draft-form');
  if (!form) return;
  var msg = form.querySelector('.new-attempt-msg');
  var input = form.querySelector('input[name="text"]');
  var csrf = document.querySelector('input[name=csrfmiddlewaretoken]');
  var specNode = document.getElementById('cryptic-draft-spec');
  var spec = null;
  try { spec = specNode ? JSON.parse(specNode.textContent) : null; } catch (e) {}

  // Verdict without the network when the page can tell: 'Ok', 'Wrong' or null.
  function localVerdict(text) {
    if (!spec || !window.OfflineVerify) return Promise.resolve(null);
    return window.OfflineVerify.verify(spec, text).catch(function () { return null; });
  }

  function showMessage(text, wrong) {
    msg.classList.toggle('new-attempt-msg--wrong', !!wrong);
    msg.textContent = text;
  }

  function showWrong() {
    showMessage('Неверно, попробуйте ещё', true);
    input.select();
  }

  function showSolved(data) {
    form.hidden = true;
    // The full breakdown follows; the hint box and its note go.
    ['cryptic-draft-hint', 'cryptic-draft-hint-note'].forEach(function (id) {
      var node = document.getElementById(id);
      if (node) node.hidden = true;
    });
    document.getElementById('cryptic-draft-answer').textContent = data.answer;
    document.getElementById('cryptic-draft-solved').hidden = false;
    if (data.explanation_html) {
      document.getElementById('cryptic-draft-explanation-body').innerHTML = data.explanation_html;
      document.getElementById('cryptic-draft-explanation').hidden = false;
    }
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value;
    if (!text.trim()) return;
    showMessage('');
    localVerdict(text).then(function (verdict) {
      // A wrong answer needs no server; a right one fetches the breakdown.
      if (verdict === 'Wrong') { showWrong(); return; }
      var body = new FormData();
      body.append('text', text);
      return fetch(form.action, {
        method: 'POST', body: body, credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf ? csrf.value : '' },
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.empty) return;
        if (data.ok) showSolved(data);
        else showWrong();
      }).catch(function () {
        showMessage(verdict === 'Ok'
          ? 'Верно! Разбор покажем, когда появится связь.'
          : 'Не получилось проверить, попробуйте ещё раз');
      });
    });
  });

  // 💡 shows the first hint; 💡💡 replaces it with the second one.
  var hintButton = form.querySelector('[data-draft-hint-open]');
  var hintBox = document.getElementById('cryptic-draft-hint');
  var hintLevel = 0;
  function hintTemplate(level) {
    return document.querySelector('template[data-draft-hint="' + level + '"]');
  }
  if (hintButton && hintBox) {
    hintButton.addEventListener('click', function () {
      var shown = hintTemplate(hintLevel + 1);
      if (!shown) return;
      hintLevel += 1;
      hintBox.querySelector('.cx-box__title').textContent = shown.getAttribute('data-title');
      var body = hintBox.querySelector('[data-draft-hint-body]');
      body.innerHTML = '';
      body.appendChild(shown.content.cloneNode(true));
      hintBox.hidden = false;
      var next = hintTemplate(hintLevel + 1);
      if (!next) {
        hintButton.remove();
        return;
      }
      var label = next.getAttribute('data-button');
      hintButton.classList.add('new-raddle-assist-btn--answer');
      hintButton.setAttribute('data-tooltip', label);
      hintButton.setAttribute('aria-label', label);
      hintButton.insertAdjacentHTML('beforeend', '<i class="ph ph-lightbulb" aria-hidden="true"></i>');
    });
  }
})();
