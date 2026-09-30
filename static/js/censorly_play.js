(function () {
  'use strict';

  function csrfToken() {
    var input = document.querySelector('#censorly-form input[name=csrfmiddlewaretoken]');
    if (input && input.value) return input.value;
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function contextToken(root) {
    return (root && root.getAttribute('data-context-token')) || '';
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

  function lemmaKey(tok) {
    return (tok && tok.lemma) || '';
  }

  function renderToken(tok, overrides) {
    overrides = overrides || {};
    var kind = tok.kind || 'content';
    if (kind === 'heading') {
      var heading = el('span', 'censorly-tok censorly-tok--heading');
      heading.textContent = tok.text || '';
      return heading;
    }
    if (kind === 'space' || kind === 'punct' || kind === 'stop') {
      var open = el('span', 'censorly-tok censorly-tok--' + kind);
      open.textContent = tok.text || '';
      return open;
    }
    if (tok.revealed) {
      var revealedClass = 'censorly-tok censorly-tok--revealed';
      if (tok.guessed) revealedClass += ' censorly-tok--guessed';
      if (tok.just_revealed) revealedClass += ' censorly-tok--just';
      var revealed = el('span', revealedClass);
      revealed.dataset.id = String(tok.id);
      if (tok.lemma) revealed.dataset.lemma = tok.lemma;
      if (tok.title_lemma) {
        revealed.dataset.titleLemma = '1';
        revealed.appendChild(el('i', 'ph ph-lock-simple censorly-tok__lock'));
      }
      revealed.appendChild(document.createTextNode(tok.text || ''));
      return revealed;
    }
    var mask = el('span', 'censorly-tok censorly-tok--mask');
    mask.dataset.id = String(tok.id);
    if (tok.lemma) mask.dataset.lemma = tok.lemma;
    if (tok.title_lemma) {
      mask.dataset.titleLemma = '1';
      mask.appendChild(el('i', 'ph ph-lock-simple censorly-tok__lock'));
    }
    var stemLen = tok.stem_length || tok.length || 1;
    var ending = tok.ending || '';
    if (ending) {
      mask.classList.add('censorly-tok--mask-ending');
      mask.style.setProperty('--ch', String(stemLen));
      var bars = el('span', 'censorly-tok__bars');
      bars.setAttribute('aria-hidden', 'true');
      mask.appendChild(bars);
      var endEl = el('span', 'censorly-tok__ending', ending);
      mask.appendChild(endEl);
      mask.dataset.len = String(tok.length || 0);
      mask.title = (tok.length || 0) + ' букв';
      mask.setAttribute(
        'aria-label',
        'скрытое слово, ' + (tok.length || 0) + ' букв, окончание «' + ending + '»'
      );
    } else {
      mask.style.setProperty('--ch', String(tok.length || 1));
      mask.dataset.len = String(tok.length || 0);
      mask.title = (tok.length || 0) + ' букв';
      mask.setAttribute('aria-label', 'скрытое слово, ' + (tok.length || 0) + ' букв');
    }
    if (overrides.lenForced != null) {
      mask.dataset.lenForced = overrides.lenForced ? '1' : '0';
    }
    return mask;
  }

  function collectLenForced(root) {
    var map = {};
    root.querySelectorAll('.censorly-tok--mask[data-len-forced]').forEach(function (node) {
      map[node.dataset.id] = node.dataset.lenForced;
    });
    return map;
  }

  function renderTokens(container, tokens, lenForced) {
    lenForced = lenForced || {};
    container.textContent = '';
    (tokens || []).forEach(function (tok) {
      var forced = lenForced[String(tok.id)];
      container.appendChild(renderToken(tok, {
        lenForced: forced === '1' ? true : (forced === '0' ? false : null),
      }));
    });
  }

  function digitClass(n) {
    if (n >= 100) return 'censorly--digits-3';
    if (n >= 10) return 'censorly--digits-2';
    return '';
  }

  function renderGuessTable(root, guesses) {
    var tbody = root.querySelector('#censorly-guess-list');
    var wrap = root.querySelector('#censorly-history-wrap');
    if (!tbody) return;
    tbody.textContent = '';
    var items = guesses || [];
    var total = items.length;
    root.classList.remove('censorly--digits-2', 'censorly--digits-3');
    var dc = digitClass(total);
    if (dc) root.classList.add(dc);
    if (!total) {
      var empty = document.createElement('tr');
      var td = el('td', 'censorly__history-empty', 'Пока нет догадок');
      td.colSpan = 3;
      empty.appendChild(td);
      tbody.appendChild(empty);
      return;
    }
    for (var i = total - 1; i >= 0; i--) {
      var g = items[i];
      var word = typeof g === 'string' ? g : (g.word || '');
      var lemma = typeof g === 'object' ? (g.lemma || '') : '';
      var hits = typeof g === 'object' ? (g.hits | 0) : 0;
      var tr = document.createElement('tr');
      tr.dataset.lemma = lemma || word;
      tr.dataset.word = word;
      tr.appendChild(el('td', null, String(i + 1)));
      tr.appendChild(el('td', null, hits ? String(hits) : '—'));
      tr.appendChild(el('td', null, word));
      tbody.appendChild(tr);
    }
  }

  function updateMetaBar(html) {
    if (!html) return;
    var bar = document.querySelector('#censorly-root .new-taskcard__meta-bar') ||
      document.querySelector('#censorly-root .new-proportions-compact-bar');
    if (bar) {
      var wrap = document.createElement('div');
      wrap.innerHTML = html;
      var next = wrap.firstElementChild;
      if (next) bar.replaceWith(next);
      return;
    }
    var host = document.getElementById('censorly-meta-host');
    if (host) {
      host.hidden = false;
      host.innerHTML = html;
    }
  }

  function scrollToTokenId(root, tokenId) {
    if (tokenId == null) return;
    var node = root.querySelector('.censorly-tok[data-id="' + String(tokenId) + '"]');
    if (!node) return;
    try {
      node.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    } catch (e) {
      node.scrollIntoView(true);
    }
  }

  function scrollToLemma(root, lemma, cycleState) {
    if (!lemma) return;
    var nodes = root.querySelectorAll('.censorly-tok[data-lemma="' + CSS.escape(lemma) + '"]');
    if (!nodes.length) return;
    var idx = cycleState[lemma] | 0;
    var node = nodes[idx % nodes.length];
    cycleState[lemma] = idx + 1;
    try {
      node.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    } catch (e) {
      node.scrollIntoView(true);
    }
  }

  function showShare(state) {
    var box = document.getElementById('censorly-result');
    var text = document.getElementById('censorly-result-share');
    if (!box || !text) return;
    if (!state.won) {
      box.hidden = true;
      return;
    }
    var lines = state.share_lines;
    if (Array.isArray(lines) && lines.length) {
      text.textContent = lines.join('\n');
    } else if (state.share_text) {
      text.textContent = state.share_text;
    } else {
      text.textContent = '';
    }
    box.hidden = false;
  }

  function applyState(root, state, opts) {
    opts = opts || {};
    var lenForced = opts.preserveLen ? collectLenForced(root) : {};
    var title = root.querySelector('#censorly-title');
    var body = root.querySelector('#censorly-body');
    var attempts = root.querySelector('#censorly-attempts');
    var won = root.querySelector('#censorly-won');
    var input = root.querySelector('#censorly-word');
    var submit = root.querySelector('#censorly-submit');
    var hintBtn = root.querySelector('#censorly-hint-btn');

    renderTokens(title, state.title_tokens || [], lenForced);
    renderTokens(body, state.body_tokens || [], lenForced);
    var trunc = root.querySelector('#censorly-truncated');
    var wikiLink = root.querySelector('#censorly-wiki-link');
    if (trunc) {
      trunc.hidden = !state.truncated;
      if (wikiLink) {
        if (state.won && state.wiki_title) {
          wikiLink.href = 'https://ru.wikipedia.org/wiki/' + encodeURIComponent(state.wiki_title);
          wikiLink.hidden = false;
        } else if (state.won && state.wiki_pageid) {
          wikiLink.href = 'https://ru.wikipedia.org/?curid=' + encodeURIComponent(String(state.wiki_pageid));
          wikiLink.hidden = false;
        } else {
          wikiLink.removeAttribute('href');
          wikiLink.hidden = true;
        }
      }
    }
    if (attempts) attempts.textContent = ruAttempts(state.attempts || 0);
    if (won) won.hidden = !state.won;
    renderGuessTable(root, state.guesses || []);
    showShare(state);
    if (state.meta_bar_html) updateMetaBar(state.meta_bar_html);

    if (state.won) {
      root.classList.remove('censorly--hint-pick');
      var hintMode = root.querySelector('#censorly-hint-mode');
      if (hintMode) hintMode.hidden = true;
      if (input) {
        input.disabled = true;
        input.placeholder = state.wiki_title || 'Готово';
      }
      if (submit) submit.disabled = true;
      if (hintBtn) hintBtn.disabled = true;
      document.title = state.wiki_title
        ? ('Цензурка · ' + state.wiki_title)
        : document.title;
      if (typeof window.revealReplayControl === 'function') {
        window.revealReplayControl();
      }
    }
  }

  function handleReplayFlags(data) {
    if (!data) return;
    if (data.reload_required) {
      window.location.reload();
      return true;
    }
    if (data.replay_available && typeof window.revealReplayControl === 'function') {
      window.revealReplayControl();
    }
    return false;
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

  function postJson(url, payload, root) {
    var body = Object.assign({}, payload || {});
    var token = contextToken(root);
    if (token) body.gameplay_context = token;
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify(body),
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : {};
        } catch (err) {
          data = null;
        }
        if (!data || typeof data !== 'object') {
          var clean = (text || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
          return {
            status: 'error',
            error: clean.slice(0, 240) || ('HTTP ' + res.status),
            __http: res.status,
          };
        }
        data.__http = res.status;
        return data;
      });
    });
  }

  function boot() {
    var root = document.getElementById('censorly-root');
    if (!root) return;
    var bootEl = document.getElementById('censorly-bootstrap');
    var state = bootEl ? JSON.parse(bootEl.textContent) : {};
    var guessUrl = root.getAttribute('data-guess-url') || state.guess_url;
    var stateUrl = root.getAttribute('data-state-url') || state.state_url;
    var hintUrl = root.getAttribute('data-hint-url') || state.hint_url;
    var hintPenalty = root.getAttribute('data-hint-penalty') || '1';
    var hintConfirm = window.InterovesHintConfirm
      ? window.InterovesHintConfirm.create()
      : { open: function (p) { if (p && p.onConfirm) p.onConfirm(); } };
    var form = root.querySelector('#censorly-form');
    var input = root.querySelector('#censorly-word');
    var historyWrap = root.querySelector('#censorly-history-wrap');
    var historyBtn = root.querySelector('#censorly-history-toggle');
    var lengthsBtn = root.querySelector('#censorly-lengths-toggle');
    var hintBtn = root.querySelector('#censorly-hint-btn');
    var hintMode = root.querySelector('#censorly-hint-mode');
    var lemmaCycle = {};
    var busy = false;

    applyState(root, state);

    if (stateUrl) {
      fetch(stateUrl, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (r) {
          return r.text().then(function (text) {
            try {
              return text ? JSON.parse(text) : null;
            } catch (err) {
              return null;
            }
          });
        })
        .then(function (data) {
          if (data && data.status !== 'error') applyState(root, data, { preserveLen: true });
        })
        .catch(function () {});
    }

    if (historyBtn && historyWrap) {
      historyBtn.addEventListener('click', function () {
        var open = historyWrap.hidden;
        historyWrap.hidden = !open;
        historyBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
      });
    }

    if (lengthsBtn) {
      lengthsBtn.addEventListener('click', function () {
        var on = root.classList.toggle('censorly--show-lengths');
        lengthsBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
        lengthsBtn.title = on ? 'Скрыть длины слов' : 'Показать длины слов';
      });
    }

    root.addEventListener('click', function (ev) {
      var mask = ev.target.closest('.censorly-tok--mask');
      if (mask && root.contains(mask)) {
        if (root.classList.contains('censorly--hint-pick')) {
          if (mask.dataset.titleLemma === '1') {
            setFeedback(root, 'Нельзя открывать слова из названия', 'error');
            return;
          }
          if (busy) return;
          busy = true;
          postJson(hintUrl, { token_id: Number(mask.dataset.id) }, root)
            .then(function (data) {
              if (handleReplayFlags(data)) return;
              if (data.status === 'error') {
                setFeedback(root, data.error || 'Не удалось взять подсказку', 'error');
                return;
              }
              root.classList.remove('censorly--hint-pick');
              if (hintMode) hintMode.hidden = true;
              applyState(root, data, { preserveLen: true });
              var newly = data.newly_revealed || [];
              if (newly.length) scrollToTokenId(root, newly[0]);
              setFeedback(root, data.status === 'won' ? 'Решено!' : 'Подсказка открыта', data.status === 'won' ? 'hit' : '');
            })
            .catch(function () {
              setFeedback(root, 'Сеть недоступна', 'error');
            })
            .finally(function () { busy = false; });
          return;
        }
        // Per-token length override
        var cur = mask.dataset.lenForced;
        if (cur === '1') mask.dataset.lenForced = '0';
        else if (cur === '0') mask.removeAttribute('data-len-forced');
        else mask.dataset.lenForced = '1';
        return;
      }

      var row = ev.target.closest('#censorly-guess-list tr');
      if (row && root.contains(row)) {
        scrollToLemma(root, row.dataset.lemma || row.dataset.word, lemmaCycle);
      }
    });

    if (hintBtn) {
      hintBtn.addEventListener('click', function () {
        if (state.won || root.classList.contains('censorly--hint-pick')) {
          root.classList.remove('censorly--hint-pick');
          if (hintMode) hintMode.hidden = true;
          return;
        }
        hintConfirm.open({
          penalty: hintPenalty,
          onConfirm: function () {
            root.classList.add('censorly--hint-pick');
            if (hintMode) hintMode.hidden = false;
            setFeedback(root, 'Кликните скрытое слово (не из названия)', '');
          },
          trigger: hintBtn,
        });
      });
    }

    if (form) {
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (busy || !input || input.disabled) return;
        var word = (input.value || '').trim();
        if (!word) return;
        busy = true;
        setFeedback(root, '');
        postJson(guessUrl, { word: word }, root)
          .then(function (data) {
            if (handleReplayFlags(data)) return;
            if (data.status === 'invalid' || data.status === 'duplicate' || data.status === 'error') {
              setFeedback(root, data.error || 'Не удалось отправить', 'error');
              applyState(root, data, { preserveLen: true });
              return;
            }
            applyState(root, data, { preserveLen: true });
            input.value = '';
            var newly = data.newly_revealed || [];
            if (newly.length) scrollToTokenId(root, newly[0]);
            if (data.status === 'hit') setFeedback(root, 'Есть совпадения: ' + (data.hits || 0), 'hit');
            else if (data.status === 'miss') setFeedback(root, 'Нет в тексте', 'error');
            else if (data.status === 'already_open') setFeedback(root, 'Уже открыто', '');
            else if (data.status === 'won') setFeedback(root, 'Решено!', 'hit');
            else if (data.status === 'already_won') setFeedback(root, 'Уже решено', 'hit');
          })
          .catch(function () {
            setFeedback(root, 'Сеть недоступна', 'error');
          })
          .finally(function () {
            busy = false;
            if (input && !input.disabled) input.focus();
          });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}());
