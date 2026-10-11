(function () {
  'use strict';
  var config = document.getElementById('censorly-offer-config');
  var input = document.getElementById('censorly-wiki-title');
  if (!config || !input) return;

  var offers = JSON.parse(document.getElementById('censorly-offers-bootstrap').textContent || '[]');
  var suggestions = document.getElementById('censorly-wiki-suggestions');
  var offerList = document.getElementById('censorly-offer-list');
  var status = document.getElementById('censorly-create-status');
  var submit = document.getElementById('censorly-create-submit');
  var comment = document.getElementById('censorly-offer-comment');
  var timer = null;
  var controller = null;
  var busy = false;
  var activeIndex = -1;

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch];
    });
  }
  function csrf() {
    var token = document.querySelector('input[name=csrfmiddlewaretoken]');
    return token ? token.value : '';
  }
  function requestJson(url, options) {
    options = options || {};
    return fetch(url, Object.assign({credentials: 'same-origin'}, options)).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data || data.ok === false) throw new Error((data && data.error) || ('HTTP ' + response.status));
        return data;
      });
    });
  }
  function setStatus(message, isError) {
    status.textContent = message || '';
    status.classList.toggle('new-field-error', !!isError);
  }
  function closeSuggestions() {
    suggestions.hidden = true;
    suggestions.innerHTML = '';
    input.setAttribute('aria-expanded', 'false');
    activeIndex = -1;
  }
  function selectTitle(title) {
    input.value = title;
    closeSuggestions();
    submit.disabled = !title.trim() || busy;
    input.focus();
  }
  function showSuggestions(titles) {
    if (!titles.length) {
      closeSuggestions();
      return;
    }
    suggestions.innerHTML = titles.map(function (title, index) {
      return '<button type="button" role="option" aria-selected="false" class="support-ladder-item support-censorly-suggestion" data-title-index="' + index + '">' + esc(title) + '</button>';
    }).join('');
    suggestions.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }
  function markActive(index) {
    var options = suggestions.querySelectorAll('[role="option"]');
    if (!options.length) return;
    activeIndex = (index + options.length) % options.length;
    options.forEach(function (option, i) {
      option.setAttribute('aria-selected', i === activeIndex ? 'true' : 'false');
    });
    options[activeIndex].scrollIntoView({block: 'nearest'});
  }
  function search() {
    var query = input.value.trim();
    if (query.length < 2) {
      closeSuggestions();
      return;
    }
    if (controller) controller.abort();
    controller = new AbortController();
    var url = new URL(config.dataset.suggestUrl, window.location.href);
    url.searchParams.set('q', query);
    requestJson(url.toString(), {signal: controller.signal})
      .then(function (data) {
        if (input.value.trim() !== query) return;
        showSuggestions(data.titles || []);
      })
      .catch(function (error) {
        if (error.name !== 'AbortError' && input.value.trim() === query) closeSuggestions();
      });
  }
  function renderOffers() {
    offerList.innerHTML = offers.length ? offers.map(function (offer) {
      var number = offer.accepted_number ? '№' + esc(offer.accepted_number) : '#' + offer.id;
      return '<div class="support-ladder-item"><div class="support-ladder-item__num">' + number + '</div>' +
        '<div class="support-ladder-item__body"><div class="support-ladder-item__title">' + esc(offer.wiki_title) +
        ' <span class="support-flag">' + esc(offer.status_label) + '</span></div><div class="support-ladder-item__meta">' +
        esc(offer.created_at || '') + (offer.admin_note ? ' · ' + esc(offer.admin_note) : '') + '</div>' +
        (offer.comment ? '<div class="support-ladder-item__preview">' + esc(offer.comment) + '</div>' : '') + '</div>' +
        '<div class="support-ladder-item__actions"><a class="new-btn new-btn--mini new-btn--ghost" href="' + esc(offer.play_url) + '" target="_blank" rel="noopener">Решить</a></div></div>';
    }).join('') : '<p class="support-empty">Пока нет предложений. Выберите статью выше.</p>';
  }

  input.setAttribute('aria-expanded', 'false');
  submit.disabled = true;
  renderOffers();
  input.addEventListener('input', function () {
    submit.disabled = !input.value.trim() || busy;
    setStatus('');
    clearTimeout(timer);
    timer = setTimeout(search, 250);
  });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowDown' && !suggestions.hidden) {
      event.preventDefault(); markActive(activeIndex + 1);
    } else if (event.key === 'ArrowUp' && !suggestions.hidden) {
      event.preventDefault(); markActive(activeIndex <= 0 ? suggestions.querySelectorAll('[role="option"]').length - 1 : activeIndex - 1);
    } else if (event.key === 'Enter' && activeIndex >= 0 && !suggestions.hidden) {
      event.preventDefault(); selectTitle(suggestions.querySelectorAll('[role="option"]')[activeIndex].textContent);
    } else if (event.key === 'Escape') {
      closeSuggestions();
    }
  });
  suggestions.addEventListener('click', function (event) {
    var option = event.target.closest('[data-title-index]');
    if (option) selectTitle(option.textContent);
  });
  document.addEventListener('click', function (event) {
    if (!suggestions.contains(event.target) && event.target !== input) closeSuggestions();
  });
  submit.addEventListener('click', function () {
    if (busy || !input.value.trim()) return;
    busy = true;
    submit.disabled = true;
    submit.classList.add('is-loading');
    submit.setAttribute('aria-busy', 'true');
    setStatus('Загружаем статью и собираем Цензурку…');
    requestJson(config.dataset.createUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      body: JSON.stringify({title: input.value.trim(), comment: comment.value.trim()})
    }).then(function (data) {
      offers = offers.filter(function (offer) { return offer.id !== data.offer.id; });
      offers.unshift(data.offer);
      renderOffers();
      input.value = '';
      comment.value = '';
      closeSuggestions();
      setStatus('Цензурка создана и отправлена редактору. Спасибо!');
    }).catch(function (error) {
      setStatus(error.message, true);
    }).finally(function () {
      busy = false;
      submit.classList.remove('is-loading');
      submit.removeAttribute('aria-busy');
      submit.disabled = !input.value.trim();
    });
  });
})();
