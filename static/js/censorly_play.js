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

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function headingClass(tok) {
    return tok && tok.in_heading ? ' is-heading' : '';
  }

  function renderToken(tok, overrides) {
    overrides = overrides || {};
    var kind = tok.kind || 'content';
    if (kind === 'heading_break') {
      return el('span', 'censorly-tok censorly-tok--heading-break');
    }
    if (kind === 'heading') {
      // Legacy whole-heading token from pre-refresh puzzles.
      var heading = el('span', 'censorly-tok censorly-tok--heading');
      heading.textContent = tok.text || '';
      return heading;
    }
    if (kind === 'space' || kind === 'punct' || kind === 'stop') {
      var open = el('span', 'censorly-tok censorly-tok--' + kind + headingClass(tok));
      open.textContent = tok.text || '';
      return open;
    }
    if (tok.revealed) {
      var revealedClass = 'censorly-tok censorly-tok--revealed' + headingClass(tok);
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
    // Mask. Default is redactle block glyphs (██████). Class censorly--bars
    // on #censorly-root keeps the older sized rectangle.
    var mask = el('span', 'censorly-tok censorly-tok--mask' + headingClass(tok));
    mask.dataset.id = String(tok.id);
    if (tok.lemma) mask.dataset.lemma = tok.lemma;
    var ending = tok.ending || '';
    var totalLen = tok.length || 0;
    var ch = Math.max(totalLen, 1);
    mask.dataset.ch = String(ch);
    mask.dataset.len = String(totalLen);
    mask.title = totalLen + ' букв';
    if (ending) {
      mask.classList.add('censorly-tok--mask-ending');
      mask.setAttribute(
        'aria-label',
        'скрытое слово, ' + totalLen + ' букв, окончание «' + ending + '»'
      );
    } else {
      mask.setAttribute('aria-label', 'скрытое слово, ' + totalLen + ' букв');
    }
    if (overrides.lenForced != null) {
      mask.dataset.lenForced = overrides.lenForced ? '1' : '0';
    }
    if (tok.title_lemma) mask.dataset.titleLemma = '1';
    var root = document.getElementById('censorly-root');
    if (root && root.classList.contains('censorly--bars')) {
      mask.style.setProperty('--ch', String(ch));
      var stem = el('span', 'censorly-tok__stem');
      stem.setAttribute('aria-hidden', 'true');
      stem.dataset.len = String(totalLen);
      if (tok.title_lemma) {
        stem.appendChild(el('i', 'ph ph-lock-simple censorly-tok__lock'));
      }
      if (ending) stem.appendChild(el('span', 'censorly-tok__ending', ending));
      mask.appendChild(stem);
      return mask;
    }
    mask.classList.add('censorly-tok--glyphs');
    if (tok.title_lemma) {
      mask.appendChild(el('i', 'ph ph-lock-simple censorly-tok__lock'));
    }
    var blocks = el('span', 'censorly-tok__blocks');
    blocks.dataset.len = String(totalLen);
    blocks.textContent = '\u2588'.repeat(ch);
    if (ending) blocks.appendChild(el('span', 'censorly-tok__ending', ending));
    mask.appendChild(blocks);
    return mask;
  }

  function collectLenForced(root) {
    var map = {};
    root.querySelectorAll('.censorly-tok--mask[data-len-forced]').forEach(function (node) {
      map[node.dataset.id] = node.dataset.lenForced;
    });
    return map;
  }

  function isNewlineSpace(tok) {
    return !!(tok && tok.kind === 'space' && /\n/.test(tok.text || ''));
  }

  function isHeadingEdge(tok) {
    return !!(tok && (tok.kind === 'heading_break' || tok.kind === 'heading'));
  }

  function headingLevel(tok) {
    var n = parseInt(tok && tok.heading_level, 10);
    if (n >= 2 && n <= 6) return n;
    return 2;
  }

  function appendToken(container, tok, lenForced) {
    var forced = lenForced[String(tok.id)];
    container.appendChild(renderToken(tok, {
      lenForced: forced === '1' ? true : (forced === '0' ? false : null),
    }));
  }

  function renderTokens(container, tokens, lenForced) {
    lenForced = lenForced || {};
    container.textContent = '';
    var list = tokens || [];
    var started = false;
    var prevWasPara = false;
    var i = 0;
    while (i < list.length) {
      var tok = list[i];
      if (isNewlineSpace(tok)) {
        var prev = list[i - 1];
        // A newline after a heading would stack on its margin. One before a
        // heading stays: that is the paragraph gap, and the heading margin adds.
        if (!started || isHeadingEdge(prev) || prevWasPara) {
          i += 1;
          continue;
        }
        prevWasPara = true;
        container.appendChild(el('span', 'censorly-tok censorly-tok--para'));
        i += 1;
        continue;
      }
      if (tok.kind === 'heading_break' || tok.kind === 'heading') {
        var level = headingLevel(tok);
        var inner = [];
        if (tok.kind === 'heading') {
          inner.push(tok);
          i += 1;
        } else {
          i += 1;
          while (i < list.length && list[i].kind !== 'heading_break') {
            var part = list[i];
            if (part.heading_level) level = headingLevel(part);
            if (!isNewlineSpace(part)) inner.push(part);
            i += 1;
          }
          if (i < list.length && list[i].kind === 'heading_break') i += 1;
        }
        if (!inner.length) continue;
        var block = el('span', 'censorly-heading censorly-heading--' + level);
        inner.forEach(function (part) {
          if (part.kind === 'heading') {
            block.appendChild(document.createTextNode(part.text || ''));
            return;
          }
          appendToken(block, part, lenForced);
        });
        container.appendChild(block);
        started = true;
        prevWasPara = false;
        continue;
      }
      prevWasPara = false;
      if (tok.kind !== 'space') started = true;
      appendToken(container, tok, lenForced);
      i += 1;
    }
  }

  function digitClass(n) {
    if (n >= 100) return 'censorly--digits-3';
    if (n >= 10) return 'censorly--digits-2';
    return '';
  }

  function renderGuessTable(root, guesses) {
    var tbody = root.querySelector('#censorly-guess-list');
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

  function setShareCard(box, state) {
    if (state && state.share_card) {
      try {
        box.setAttribute('data-share-card', JSON.stringify(state.share_card));
        return;
      } catch (err) {}
    }
    box.removeAttribute('data-share-card');
  }

  function showShare(state) {
    var box = document.getElementById('censorly-result');
    var text = document.getElementById('censorly-result-share');
    if (!box || !text) return;
    if (!state.won) {
      box.hidden = true;
      text.textContent = '';
      setShareCard(box, null);
      return;
    }
    var lines = state.share_lines;
    if (!Array.isArray(lines) || !lines.length) {
      if (state.share_text) {
        lines = String(state.share_text).split('\n');
      } else {
        // Replay wins intentionally omit share cards.
        box.hidden = true;
        text.textContent = '';
        setShareCard(box, null);
        return;
      }
    }
    // One <div> per line — DailyShareActions joins them with \n for copy.
    text.textContent = '';
    lines.forEach(function (line, i) {
      var div = document.createElement('div');
      if (i === 0) div.className = 'new-raddle-result__title';
      else if (i === lines.length - 1) div.className = 'new-raddle-result__link';
      else div.className = 'new-raddle-result__squares';
      div.textContent = line;
      text.appendChild(div);
    });
    setShareCard(box, state);
    box.hidden = false;
  }

  function wikiUrl(state) {
    if (state && state.wiki_title) {
      return 'https://ru.wikipedia.org/wiki/' + encodeURIComponent(state.wiki_title);
    }
    if (state && state.wiki_pageid) {
      return 'https://ru.wikipedia.org/?curid=' + encodeURIComponent(String(state.wiki_pageid));
    }
    return '';
  }

  function renderTitleDecoration(title, state) {
    if (!title || !state || !state.won) return;
    var url = wikiUrl(state);
    if (url) {
      var link = el('a', 'censorly__wiki-link');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.title = 'Открыть статью на Википедии';
      link.setAttribute('aria-label', 'Открыть статью на Википедии');
      link.appendChild(el('i', 'ph ph-wikipedia-logo'));
      title.appendChild(link);
    }
    title.appendChild(el('span', 'censorly__solved', '— Решено!'));
  }

  function applyState(root, state, opts) {
    opts = opts || {};
    var lenForced = opts.preserveLen ? collectLenForced(root) : {};
    var title = root.querySelector('#censorly-title');
    var body = root.querySelector('#censorly-body');
    var input = root.querySelector('#censorly-word');
    var submit = root.querySelector('#censorly-submit');
    var hintBtn = root.querySelector('#censorly-hint-btn');

    renderTokens(title, state.title_tokens || [], lenForced);
    renderTitleDecoration(title, state);
    var fetched = root.querySelector('#censorly-fetched');
    if (fetched) {
      var fetchedLabel = state.wiki_fetched_label || '';
      fetched.hidden = !fetchedLabel;
      fetched.textContent = fetchedLabel;
    }
    renderTokens(body, state.body_tokens || [], lenForced);
    var trunc = root.querySelector('#censorly-truncated');
    if (trunc) trunc.hidden = !state.truncated;
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
        // Global toggle wins: drop per-word length overrides.
        root.querySelectorAll('.censorly-tok--mask[data-len-forced]').forEach(function (node) {
          node.removeAttribute('data-len-forced');
        });
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
              if (data.status !== 'won') setFeedback(root, 'Подсказка открыта', '');
            })
            .catch(function () {
              setFeedback(root, 'Сеть недоступна', 'error');
            })
            .finally(function () { busy = false; });
          return;
        }
        // Per-token length override (2-state, depends on global toggle).
        var globalOn = root.classList.contains('censorly--show-lengths');
        var cur = mask.dataset.lenForced;
        if (globalOn) {
          // All lengths visible: click hides this word, click again restores.
          if (cur === '0') mask.removeAttribute('data-len-forced');
          else mask.dataset.lenForced = '0';
        } else {
          // Lengths hidden: click shows this word, click again hides.
          if (cur === '1') mask.removeAttribute('data-len-forced');
          else mask.dataset.lenForced = '1';
        }
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
