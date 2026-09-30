(function () {
  'use strict';

  function csrfToken() {
    var input = document.querySelector('#censorly-form input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function ruAttempts(n) {
    n = Math.abs(n | 0);
    var n10 = n % 10;
    var n100 = n % 100;
    if (n10 === 1 && n100 !== 11) return n + ' попытка';
    if (n10 >= 2 && n10 <= 4 && !(n100 >= 12 && n100 <= 14)) return n + ' попытки';
    return n + ' попыток';
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderToken(tok) {
    var kind = tok.kind || 'content';
    if (kind === 'space' || kind === 'punct' || kind === 'stop') {
      var open = el('span', 'censorly-tok censorly-tok--' + kind);
      open.textContent = tok.text || '';
      return open;
    }
    if (tok.revealed) {
      var revealed = el(
        'span',
        'censorly-tok censorly-tok--revealed' + (tok.just_revealed ? ' censorly-tok--just' : '')
      );
      revealed.dataset.id = String(tok.id);
      revealed.textContent = tok.text || '';
      return revealed;
    }
    var mask = el('span', 'censorly-tok censorly-tok--mask');
    mask.dataset.id = String(tok.id);
    mask.style.setProperty('--ch', String(tok.length || 1));
    mask.title = (tok.length || 0) + ' букв';
    mask.setAttribute('aria-label', 'скрытое слово, ' + (tok.length || 0) + ' букв');
    return mask;
  }

  function renderTokens(container, tokens) {
    container.textContent = '';
    (tokens || []).forEach(function (tok) {
      container.appendChild(renderToken(tok));
    });
  }

  function renderGuessList(listEl, guesses) {
    listEl.textContent = '';
    var items = (guesses || []).slice().reverse();
    items.forEach(function (g) {
      var word = typeof g === 'string' ? g : (g.word || '');
      var hits = typeof g === 'object' ? (g.hits | 0) : 0;
      var li = el('li', null, word + (hits ? ' · ' + hits : ' · —'));
      li.dataset.hits = String(hits);
      listEl.appendChild(li);
    });
  }

  function applyState(root, state) {
    var title = root.querySelector('#censorly-title');
    var body = root.querySelector('#censorly-body');
    var attempts = root.querySelector('#censorly-attempts');
    var won = root.querySelector('#censorly-won');
    var list = root.querySelector('#censorly-guess-list');
    var input = root.querySelector('#censorly-word');
    var submit = root.querySelector('#censorly-submit');

    renderTokens(title, state.title_tokens || []);
    renderTokens(body, state.body_tokens || []);
    if (attempts) attempts.textContent = ruAttempts(state.attempts || 0);
    if (won) won.hidden = !state.won;
    renderGuessList(list, state.guesses || []);

    if (state.won) {
      if (input) {
        input.disabled = true;
        input.placeholder = state.wiki_title || 'Готово';
      }
      if (submit) submit.disabled = true;
      document.title = state.wiki_title
        ? ('Цензурка · ' + state.wiki_title)
        : document.title;
    }
  }

  function setFeedback(root, text, kind) {
    var node = root.querySelector('#censorly-feedback');
    if (!node) return;
    if (!text) {
      node.hidden = true;
      node.textContent = '';
      node.className = 'censorly__feedback';
      return;
    }
    node.hidden = false;
    node.textContent = text;
    node.className = 'censorly__feedback' + (kind ? ' is-' + kind : '');
  }

  function boot() {
    var root = document.getElementById('censorly-root');
    var bootEl = document.getElementById('censorly-bootstrap');
    if (!root || !bootEl) return;

    var bootstrap = {};
    try {
      bootstrap = JSON.parse(bootEl.textContent || '{}');
    } catch (e) {
      bootstrap = {};
    }

    var guessUrl = root.getAttribute('data-guess-url') || bootstrap.guess_url;
    var form = root.querySelector('#censorly-form');
    var input = root.querySelector('#censorly-word');
    var submit = root.querySelector('#censorly-submit');
    var busy = false;

    applyState(root, bootstrap.state || {});
    if (input) input.focus();

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (busy || !input || input.disabled) return;
      var word = (input.value || '').trim();
      if (!word) return;
      busy = true;
      if (submit) submit.disabled = true;
      setFeedback(root, '');
      fetch(guessUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ word: word }),
      })
        .then(function (res) {
          return res.text().then(function (text) {
            var data = null;
            try {
              data = text ? JSON.parse(text) : {};
            } catch (err) {
              data = null;
            }
            if (!data) {
              var clean = (text || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
              throw new Error(clean.slice(0, 240) || ('HTTP ' + res.status));
            }
            return { ok: res.ok, data: data };
          });
        })
        .then(function (pack) {
          var data = pack.data || {};
          applyState(root, data);
          if (data.status === 'hit') {
            setFeedback(root, 'Найдено: ' + (data.hits || 0), 'ok');
          } else if (data.status === 'miss') {
            setFeedback(root, 'Нет в тексте', 'error');
          } else if (data.status === 'already_open') {
            setFeedback(root, 'Уже открыто', 'ok');
          } else if (data.status === 'won') {
            setFeedback(root, 'Название открыто: ' + (data.wiki_title || ''), 'ok');
          } else if (data.status === 'duplicate') {
            setFeedback(root, data.error || 'Уже вводили', 'error');
          } else if (data.status === 'invalid' || data.status === 'error') {
            setFeedback(root, data.error || 'Ошибка', 'error');
          } else if (data.status === 'already_won') {
            setFeedback(root, 'Уже решено', 'ok');
          }
          if (input && !data.won) {
            input.value = '';
            input.focus();
          }
        })
        .catch(function (err) {
          setFeedback(root, (err && err.message) || 'Не удалось отправить', 'error');
        })
        .finally(function () {
          busy = false;
          if (submit && !(bootstrap.state && bootstrap.state.won)) {
            var wonEl = root.querySelector('#censorly-won');
            if (!wonEl || wonEl.hidden) submit.disabled = false;
          }
        });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
