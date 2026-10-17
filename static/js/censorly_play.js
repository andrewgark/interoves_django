(function () {
  'use strict';

  var guessFeedbackHelper = window.CensorlyGuessFeedback || {
    feedback: function (data, submittedWord) {
      var word = (data && data.guess_word) || submittedWord || '';
      if (data && data.status === 'duplicate') return 'Слово «' + word + '» уже вводили';
      if (data && (data.status === 'already_open' || data.status === 'initially_open')) {
        return 'Слово «' + word + '» уже открыто';
      }
      return (data && data.error) || 'Не удалось отправить';
    },
    shouldClearInput: function (status) {
      return status === 'duplicate' || status === 'initially_open' || status === 'already_open';
    },
  };

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

  function letterWord(count) {
    var value = Math.abs(Number(count)) || 0;
    var lastTwo = value % 100;
    var last = value % 10;
    if (lastTwo >= 11 && lastTwo <= 14) return 'букв';
    if (last === 1) return 'буква';
    if (last >= 2 && last <= 4) return 'буквы';
    return 'букв';
  }

  function lettersLabel(count) {
    return count + ' ' + letterWord(count);
  }

  var DISPLAY_PREFERENCES_KEY = 'interoves:censorly:display-preferences:v1';

  function readDisplayPreferences() {
    try {
      var raw = window.localStorage.getItem(DISPLAY_PREFERENCES_KEY);
      var data = raw ? JSON.parse(raw) : {};
      return data && typeof data === 'object' ? data : {};
    } catch (err) {
      return {};
    }
  }

  function saveDisplayPreferences(preferences) {
    try {
      window.localStorage.setItem(DISPLAY_PREFERENCES_KEY, JSON.stringify(preferences));
    } catch (err) {}
  }

  function syncScrollSettings(root, menu, mode) {
    var valid = ['first', 'nearest', 'next', 'off'];
    mode = valid.indexOf(mode) >= 0 ? mode : 'first';
    root.setAttribute('data-submit-scroll-mode', mode);
    if (menu) menu.querySelectorAll('[data-scroll-option]').forEach(function (option) {
      var selected = option.getAttribute('data-scroll-option') === mode;
      option.classList.toggle('is-selected', selected);
      option.setAttribute('aria-checked', selected ? 'true' : 'false');
      option.tabIndex = selected ? 0 : -1;
    });
    return mode;
  }

  function updateMaskAccessibility(root, showEndings) {
    if (!root) return;
    root.querySelectorAll('.censorly-tok--mask').forEach(function (mask) {
      var lengthLabel = lettersLabel(mask.dataset.len || 0);
      var ending = showEndings ? (mask.dataset.ending || '') : '';
      var endingLabel = ending ? ', окончание «' + ending + '»' : '';
      var lockLabel = mask.dataset.lockLabel || 'скрытое слово';
      mask.setAttribute('aria-label', lockLabel + ', ' + lengthLabel + endingLabel);
    });
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
      var revealedClass = 'censorly-tok censorly-tok--revealed censorly-tok--reportable' + headingClass(tok);
      if (tok.guessed) revealedClass += ' censorly-tok--guessed';
      if (tok.hinted) revealedClass += ' censorly-tok--hinted';
      if (tok.title_lemma) revealedClass += ' censorly-tok--title-lock';
      else if (tok.title_related) revealedClass += ' censorly-tok--related-lock';
      var revealed = el('span', revealedClass);
      revealed.dataset.id = String(tok.id);
      revealed.dataset.surface = tok.text || '';
      if (tok.opened_by_guess) revealed.dataset.openedByGuess = tok.opened_by_guess;
      revealed.setAttribute('role', 'button');
      revealed.setAttribute('tabindex', '0');
      revealed.setAttribute('aria-label', 'Пожаловаться на слово «' + (tok.text || '') + '»');
      if (tok.lemma) revealed.dataset.lemma = tok.lemma;
      if (tok.title_lemma || tok.title_related) {
        revealed.dataset.titleLemma = '1';
        var revealedLockTip = tok.title_lemma
          ? 'Слово из названия статьи'
          : 'Слово, близкое к слову из названия статьи';
        revealed.appendChild(el(
          'i', 'ph ph-lock-simple censorly-tok__lock' +
            (tok.title_lemma ? ' censorly-tok__lock--strong' : ' censorly-tok__lock--weak')
        ));
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
    var lengthLabel = lettersLabel(totalLen);
    mask.setAttribute('data-tooltip', lengthLabel);
    if (ending) mask.dataset.ending = ending;
    if (ending) {
      mask.classList.add('censorly-tok--mask-ending');
      mask.setAttribute(
        'aria-label',
        'скрытое слово, ' + lengthLabel + ', окончание «' + ending + '»'
      );
    } else {
      mask.setAttribute('aria-label', 'скрытое слово, ' + lengthLabel);
    }
    if (overrides.lenForced != null) {
      mask.dataset.lenForced = overrides.lenForced ? '1' : '0';
    }
    if (tok.title_lemma || tok.title_related) {
      mask.dataset.titleLemma = '1';
      var lockTip = tok.title_lemma
        ? 'Слово из названия статьи'
        : 'Слово, близкое к слову из названия статьи';
      mask.dataset.lockLabel = lockTip;
      mask.setAttribute('data-tooltip', lockTip + ' · ' + lengthLabel);
      mask.setAttribute('tabindex', '0');
      mask.setAttribute('aria-label', lockTip + ', ' + lengthLabel);
      mask.classList.add(tok.title_lemma ? 'censorly-tok--title-lock' : 'censorly-tok--related-lock');
    }
    var root = document.getElementById('censorly-root');
    if (root && root.classList.contains('censorly--bars')) {
      mask.style.setProperty('--ch', String(ch));
      var stem = el('span', 'censorly-tok__stem');
      stem.setAttribute('aria-hidden', 'true');
      stem.dataset.len = String(totalLen);
      if (tok.title_lemma || tok.title_related) {
        stem.appendChild(el(
          'i', 'ph ph-lock-simple censorly-tok__lock' +
            (tok.title_lemma ? ' censorly-tok__lock--strong' : ' censorly-tok__lock--weak')
        ));
      }
      if (ending) stem.appendChild(el('span', 'censorly-tok__ending', ending));
      mask.appendChild(stem);
      return mask;
    }
    mask.classList.add('censorly-tok--glyphs');
    if (tok.title_lemma || tok.title_related) {
      mask.appendChild(el(
        'i', 'ph ph-lock-simple censorly-tok__lock' +
          (tok.title_lemma ? ' censorly-tok__lock--strong' : ' censorly-tok__lock--weak')
      ));
    }
    var blocks = el('span', 'censorly-tok__blocks');
    blocks.dataset.len = String(totalLen);
    blocks.textContent = '\u2588'.repeat(ch);
    if (ending) blocks.appendChild(el('span', 'censorly-tok__ending', ending));
    mask.appendChild(blocks);
    return mask;
  }

  function syncEndingsToggle(root, button, on) {
    if (!root || !button) return;
    root.classList.toggle('censorly--show-endings', on);
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    var label = on ? 'Скрыть окончания' : 'Показать окончания';
    button.setAttribute('aria-label', label);
    button.setAttribute('data-tooltip', label);
    updateMaskAccessibility(root, on);
  }

  function syncLengthsToggle(root, button, on) {
    if (!root || !button) return;
    root.classList.toggle('censorly--show-lengths', on);
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    var label = on ? 'Скрыть длины слов' : 'Показать длины слов';
    button.setAttribute('aria-label', label);
    button.setAttribute('data-tooltip', label);
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

  function isCompactArticleBreak(tok) {
    return !!(tok && tok.kind === 'space' && /\u2028/.test(tok.text || ''));
  }

  function isHeadingEdge(tok) {
    return !!(tok && (tok.kind === 'heading_break' || tok.kind === 'heading'));
  }

  function headingLevel(tok) {
    var n = parseInt(tok && tok.heading_level, 10);
    if (n >= 2 && n <= 6) return n;
    return 2;
  }

  function appendToken(container, tok, lenForced, extraClass) {
    var forced = lenForced[String(tok.id)];
    var node = renderToken(tok, {
      lenForced: forced === '1' ? true : (forced === '0' ? false : null),
    });
    if (extraClass) extraClass.split(/\s+/).forEach(function (name) {
      if (name) node.classList.add(name);
    });
    container.appendChild(node);
  }

  function isWordDisplayToken(tok) {
    return !!tok && (tok.kind === 'content' || tok.kind === 'stop' || tok.revealed);
  }

  function punctuationAttachments(tok, hasPreviousWord, hasNextWord) {
    var text = String((tok && (tok.text || tok.surface)) || '');
    return window.CensorlyPunctuation.attachments(text, hasPreviousWord, hasNextWord);
  }

  function appendWordWithPunctuation(container, tokens, index, lenForced) {
    appendToken(container, tokens[index], lenForced);
    var next = index + 1;
    while (next < tokens.length && tokens[next].kind === 'punct') {
      var attachments = punctuationAttachments(
        tokens[next], true, isWordDisplayToken(tokens[next + 1])
      );
      if (!attachments.before) break;
      var classes = [];
      if (attachments.before) classes.push('censorly-tok--no-break-before');
      if (attachments.after) classes.push('censorly-tok--no-break-after');
      appendToken(container, tokens[next], lenForced, classes.join(' '));
      next += 1;
    }
    return next;
  }

  function appendOpeningPunctuationWithWord(container, tokens, index, lenForced) {
    var start = index;
    var next = index;
    while (next < tokens.length && tokens[next].kind === 'punct') next += 1;
    if (!isWordDisplayToken(tokens[next])) return index;
    var attachmentsByToken = [];
    var hasAfterAttachment = false;
    for (var cursor = index; cursor < next; cursor += 1) {
      var attachments = punctuationAttachments(tokens[cursor], isWordDisplayToken(tokens[index - 1]), true);
      attachmentsByToken.push(attachments);
      hasAfterAttachment = hasAfterAttachment || attachments.after;
    }
    if (!hasAfterAttachment) return index;
    while (index < next) {
      var tokenAttachments = attachmentsByToken[index - start];
      var classes = [];
      if (tokenAttachments.before) classes.push('censorly-tok--no-break-before');
      if (tokenAttachments.after) classes.push('censorly-tok--no-break-after');
      appendToken(container, tokens[index], lenForced, classes.join(' '));
      index += 1;
    }
    return appendWordWithPunctuation(container, tokens, next, lenForced);
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
      if (isCompactArticleBreak(tok)) {
        var compactBreakText = tok.text || '';
        var compactBreakIndex = compactBreakText.indexOf('\u2028');
        var compactPrefix = compactBreakText.slice(0, compactBreakIndex);
        var compactIndent = compactBreakText.slice(compactBreakIndex + 1);
        if (compactPrefix) container.appendChild(document.createTextNode(compactPrefix));
        container.appendChild(el('span', 'censorly-tok censorly-tok--compact-break'));
        if (compactIndent) container.appendChild(document.createTextNode(compactIndent));
        i += 1;
        continue;
      }
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
        var partIndex = 0;
        while (partIndex < inner.length) {
          var part = inner[partIndex];
          if (part.kind === 'heading') {
            block.appendChild(document.createTextNode(part.text || ''));
            partIndex += 1;
            continue;
          }
          if (isWordDisplayToken(part)) {
            partIndex = appendWordWithPunctuation(block, inner, partIndex, lenForced);
            continue;
          }
          if (part.kind === 'punct') {
            var nextPartIndex = appendOpeningPunctuationWithWord(block, inner, partIndex, lenForced);
            if (nextPartIndex !== partIndex) {
              partIndex = nextPartIndex;
              continue;
            }
          }
          appendToken(block, part, lenForced);
          partIndex += 1;
        }
        container.appendChild(block);
        started = true;
        prevWasPara = false;
        continue;
      }
      prevWasPara = false;
      if (tok.kind !== 'space') started = true;
      if (isWordDisplayToken(tok)) {
        i = appendWordWithPunctuation(container, list, i, lenForced);
        continue;
      }
      if (tok.kind === 'punct') {
        var nextIndex = appendOpeningPunctuationWithWord(container, list, i, lenForced);
        if (nextIndex !== i) {
          i = nextIndex;
          continue;
        }
      }
      appendToken(container, tok, lenForced);
      i += 1;
    }
  }

  function digitClass(n) {
    if (n >= 100) return 'censorly--digits-3';
    if (n >= 10) return 'censorly--digits-2';
    return '';
  }

  function activeGuessIndex(root, guesses) {
    var total = (guesses || []).length;
    if (!total) {
      root.removeAttribute('data-active-guess-index');
      return null;
    }
    var stored = parseInt(root.getAttribute('data-active-guess-index'), 10);
    if (Number.isInteger(stored) && stored >= 0 && stored < total) return stored;
    var latest = total - 1;
    root.setAttribute('data-active-guess-index', String(latest));
    return latest;
  }

  function applyGuessHighlight(root, guesses, index) {
    root.querySelectorAll('.censorly-tok--selected').forEach(function (node) {
      node.classList.remove('censorly-tok--selected');
    });
    root.querySelectorAll('#censorly-guess-list tr.is-active').forEach(function (row) {
      row.classList.remove('is-active');
      row.removeAttribute('aria-selected');
    });
    if (index == null || !guesses[index]) return;

    var row = root.querySelector('#censorly-guess-list tr[data-guess-index="' + String(index) + '"]');
    if (row) {
      row.classList.add('is-active');
      row.setAttribute('aria-selected', 'true');
    }
    var tokenIds = Array.isArray(guesses[index].token_ids) ? guesses[index].token_ids : [];
    if (!tokenIds.length) return;
    var wanted = {};
    tokenIds.forEach(function (id) { wanted[String(id)] = true; });
    root.querySelectorAll('.censorly-tok[data-id]').forEach(function (node) {
      if (wanted[node.dataset.id]) node.classList.add('censorly-tok--selected');
    });
  }

  function focusGuess(root, index, scroll) {
    if (!Number.isInteger(index) || index < 0) return;
    root.setAttribute('data-active-guess-index', String(index));
    var state = root._censorlyState || {};
    applyGuessHighlight(root, state.guesses || [], index);
    if (!scroll) return;
    var row = root.querySelector('#censorly-guess-list tr[data-guess-index="' + String(index) + '"]');
    if (row) {
      try {
        row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } catch (e) {
        row.scrollIntoView(false);
      }
    }
  }

  function guessTokenIds(root, index) {
    var state = root && root._censorlyState;
    var guess = state && Array.isArray(state.guesses) ? state.guesses[index] : null;
    return guess && Array.isArray(guess.token_ids) ? guess.token_ids : [];
  }

  function focusTokenIds(root, tokenIds, scroll) {
    var ids = Array.isArray(tokenIds) ? tokenIds : [];
    var nodes = ids.map(function (id) {
      return root.querySelector('.censorly-tok[data-id="' + String(id) + '"]');
    }).filter(Boolean);
    if (!nodes.length) return;
    root.querySelectorAll('.censorly-tok--selected').forEach(function (node) {
      node.classList.remove('censorly-tok--selected');
    });
    nodes.forEach(function (node) { node.classList.add('censorly-tok--selected'); });
    if (scroll) scrollToNodes(root, nodes);
  }

  function flashTokenIds(root, tokenIds) {
    var ids = {};
    (Array.isArray(tokenIds) ? tokenIds : []).forEach(function (id) {
      ids[String(id)] = true;
    });
    Object.keys(ids).forEach(function (id) {
      var node = root.querySelector('.censorly-tok[data-id="' + id + '"]');
      if (!node) return;
      // Restart the animation only for an explicit reveal or history selection.
      node.classList.remove('censorly-tok--just');
      void node.offsetWidth;
      node.classList.add('censorly-tok--just');
    });
  }

  function selectGuessRow(root, row) {
    var guessIndex = parseInt(row.dataset.guessIndex, 10);
    if (!Number.isInteger(guessIndex)) return;
    root.setAttribute('data-active-guess-index', String(guessIndex));
    var currentState = root._censorlyState || {};
    applyGuessHighlight(root, currentState.guesses || [], guessIndex);
    var guess = currentState.guesses && currentState.guesses[guessIndex];
    flashTokenIds(root, guess && guess.token_ids);
    scrollToGuess(root, row, guessIndex);
  }

  function renderGuessTable(root, guesses, activeIndex) {
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
      var tokenIds = (typeof g === 'object' && Array.isArray(g.token_ids)) ? g.token_ids : [];
      var tr = document.createElement('tr');
      tr.dataset.guessIndex = String(i);
      tr.dataset.lemma = lemma || word;
      tr.dataset.word = word;
      if (tokenIds.length) tr.dataset.tokenIds = tokenIds.join(',');
      tr.appendChild(el('td', null, String(i + 1)));
      var hitsCell = el('td');
      if (hits) {
        hitsCell.textContent = String(hits);
      } else {
        var noHits = el('i', 'ph ph-x censorly__no-hits');
        noHits.setAttribute('aria-hidden', 'true');
        noHits.setAttribute('title', 'Совпадений нет');
        hitsCell.setAttribute('aria-label', 'Совпадений нет');
        hitsCell.appendChild(noHits);
      }
      tr.appendChild(hitsCell);
      var wordCell = el('td');
      var wordButton = el('button', 'censorly__guess-word', word);
      wordButton.type = 'button';
      wordButton.setAttribute('aria-label', 'Показать слово «' + word + '» в тексте');
      wordCell.appendChild(wordButton);
      tr.appendChild(wordCell);
      tbody.appendChild(tr);
    }
    applyGuessHighlight(root, items, activeIndex);
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

  function mobileDockBounds(root) {
    var dock = root && root.querySelector('.censorly__dock');
    var rootRect = root && root.getBoundingClientRect();
    var dockRect = dock && dock.getBoundingClientRect();
    var viewport = window.visualViewport;
    var viewportTop = viewport ? Math.max(0, viewport.offsetTop || 0) : 0;
    var viewportBottom = viewport
      ? viewportTop + viewport.height
      : window.innerHeight;
    var mobileDock = dockRect && rootRect &&
      dockRect.top < viewportBottom && dockRect.bottom > viewportTop &&
      dockRect.width >= rootRect.width * 0.8;
    if (!mobileDock) return null;
    var top = Math.max(12, viewportTop);
    return {
      top: top,
      bottom: Math.max(top + 1, Math.min(viewportBottom, dockRect.top - 12)),
    };
  }

  function visibleAreaTop(root) {
    var bounds = mobileDockBounds(root);
    var viewport = window.visualViewport;
    var top = Math.max(
      bounds ? bounds.top : 0,
      viewport ? Math.max(0, viewport.offsetTop || 0) : 0
    );
    var nav = document.querySelector('.new-nav');
    if (nav) {
      var navRect = nav.getBoundingClientRect();
      if (navRect.top <= top && navRect.bottom > top) top = navRect.bottom;
    }
    return top;
  }

  function visibleAreaCenter(root) {
    var bounds = mobileDockBounds(root);
    var viewport = window.visualViewport;
    var top = visibleAreaTop(root);
    var bottom = bounds
      ? bounds.bottom
      : (viewport ? (viewport.offsetTop || 0) + viewport.height : window.innerHeight);
    return (top + Math.max(top + 1, bottom)) / 2;
  }

  function scrollToNode(root, node) {
    if (!node) return;
    // On mobile the dock spans the game width and sticks over the bottom of
    // the article. Center within the unobscured area, not the whole viewport.
    var bounds = mobileDockBounds(root);
    if (bounds) {
      var availableCenter = visibleAreaCenter(root);
      var rect = node.getBoundingClientRect();
      var delta = rect.top + rect.height / 2 - availableCenter;
      if (rect.height > bounds.bottom - bounds.top) delta = rect.top - bounds.top;
      if (Math.abs(delta) > 1) {
        try {
          window.scrollBy({ top: delta, behavior: 'smooth' });
        } catch (e) {
          window.scrollBy(0, delta);
        }
      }
      return;
    }
    try {
      node.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    } catch (e) {
      node.scrollIntoView(true);
    }
  }

  function scrollToTokenId(root, tokenId) {
    if (tokenId == null) return;
    scrollToNode(root, root.querySelector('.censorly-tok[data-id="' + String(tokenId) + '"]'));
  }

  function nearestScrollNode(root, nodes) {
    if (!nodes.length) return null;
    var viewportCenter = visibleAreaCenter(root);
    return Array.prototype.reduce.call(nodes, function (nearest, node) {
      if (!nearest) return node;
      var nodeDistance = Math.abs(node.getBoundingClientRect().top + node.offsetHeight / 2 - viewportCenter);
      var nearestDistance = Math.abs(nearest.getBoundingClientRect().top + nearest.offsetHeight / 2 - viewportCenter);
      return nodeDistance < nearestDistance ? node : nearest;
    }, null);
  }

  function scrollToNodes(root, nodes) {
    if (!nodes.length) return;
    var mode = root.getAttribute('data-scroll-mode') || 'first';
    scrollToNode(root, mode === 'nearest' ? nearestScrollNode(root, nodes) : nodes[0]);
  }

  function scrollAfterGuess(root, tokenIds) {
    var mode = root.getAttribute('data-submit-scroll-mode') || 'first';
    if (mode === 'off') return;
    var ids = Array.isArray(tokenIds) ? tokenIds : [];
    var nodes = ids.map(function (id) {
      return root.querySelector('.censorly-tok[data-id="' + String(id) + '"]');
    }).filter(Boolean);
    if (!nodes.length) return;
    var node = nodes[0];
    if (mode === 'nearest') node = nearestScrollNode(root, nodes);
    if (mode === 'next') {
      var visibleTop = visibleAreaTop(root);
      node = nodes.find(function (candidate) {
        var rect = candidate.getBoundingClientRect();
        return rect.top > visibleTop + 2;
      }) || nodes[0];
    }
    scrollToNode(root, node);
  }

  function scrollToGuess(root, row, guessIndex) {
    var raw = row.dataset.tokenIds || '';
    var ids = raw ? raw.split(',') : [];
    var nodes = [];
    if (ids.length) {
      nodes = ids.map(function (id) {
        return root.querySelector('.censorly-tok[data-id="' + String(id) + '"]');
      }).filter(Boolean);
    } else {
      var lemma = row.dataset.lemma || row.dataset.word;
      if (lemma) {
        nodes = Array.prototype.slice.call(
          root.querySelectorAll('.censorly-tok[data-lemma="' + CSS.escape(lemma) + '"]')
        );
      }
    }
    if (!nodes.length) return;

    var cycle = root._censorlyGuessScrollCycle;
    var nextIndex = cycle && cycle.guessIndex === guessIndex ? cycle.nextIndex : 0;
    var occurrenceIndex = nextIndex % nodes.length;
    root._censorlyGuessScrollCycle = {
      guessIndex: guessIndex,
      nextIndex: (occurrenceIndex + 1) % nodes.length,
    };
    scrollToNode(root, nodes[occurrenceIndex]);
  }

  function hasRenderableState(data) {
    return !!(data && (
      Array.isArray(data.title_tokens) ||
      Array.isArray(data.body_tokens) ||
      Array.isArray(data.guesses)
    ));
  }

  function syncMobileDockOffset(root) {
    var dock = root.querySelector('.censorly__dock');
    if (!dock) return;
    var update = function () {
      var rootWidth = root.getBoundingClientRect().width;
      var dockWidth = dock.getBoundingClientRect().width;
      if (rootWidth && dockWidth >= rootWidth * 0.8) {
        root.style.setProperty('--censorly-dock-height', (dock.offsetHeight + 16) + 'px');
      } else {
        root.style.removeProperty('--censorly-dock-height');
      }
    };
    update();
    if (window.ResizeObserver) {
      var observer = new ResizeObserver(update);
      observer.observe(dock);
      root._censorlyDockObserver = observer;
    }
  }

  function syncMobileKeyboardOffset(root) {
    if (!root) return;
    var update = function () {
      var viewport = window.visualViewport;
      if (!viewport) {
        root.style.removeProperty('--censorly-keyboard-offset');
        return;
      }
      // On browsers that keep the layout viewport full-sized, the visual
      // viewport shrinks above the soft keyboard. Resize-mode browsers report
      // zero here because their layout viewport already shrinks.
      var offset = Math.max(
        0,
        window.innerHeight - viewport.height - viewport.offsetTop
      );
      root.style.setProperty('--censorly-keyboard-offset', offset + 'px');
    };
    update();
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', update);
      window.visualViewport.addEventListener('scroll', update);
    }
    window.addEventListener('resize', update);
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
      link.setAttribute('aria-label', 'Открыть статью на Википедии');
      link.setAttribute('data-tooltip', 'Открыть статью на Википедии');
      link.appendChild(el('i', 'ph ph-wikipedia-logo'));
      title.appendChild(link);
    }
    title.appendChild(el('span', 'censorly__solved', '— Решено!'));
  }

  function applyStateDelta(root, data) {
    if (!data || !data.state_delta) return data;
    var previous = root._censorlyState || {};
    var delta = data.state_delta;
    var next = Object.assign({}, previous, delta);
    var titleTokens = (previous.title_tokens || []).slice();
    var bodyTokens = (previous.body_tokens || []).slice();
    var byId = {};
    titleTokens.forEach(function (tok, index) { byId[String(tok.id)] = { list: titleTokens, index: index }; });
    bodyTokens.forEach(function (tok, index) { byId[String(tok.id)] = { list: bodyTokens, index: index }; });
    (delta.token_updates || []).forEach(function (tok) {
      var target = byId[String(tok.id)];
      if (target) target.list[target.index] = tok;
    });
    delete next.token_updates;
    next.title_tokens = titleTokens;
    next.body_tokens = bodyTokens;
    var merged = Object.assign({}, next, data);
    merged.title_tokens = titleTokens;
    merged.body_tokens = bodyTokens;
    delete merged.state_delta;
    return merged;
  }

  function applyState(root, state, opts) {
    opts = opts || {};
    root._censorlyState = state;
    var lenForced = opts.preserveLen ? collectLenForced(root) : {};
    var title = root.querySelector('#censorly-title');
    var body = root.querySelector('#censorly-body');
    var input = root.querySelector('#censorly-word');
    var submit = root.querySelector('#censorly-submit');
    var hintBtn = root.querySelector('#censorly-hint-btn');

    var openingGuessByToken = {};
    (state.guesses || []).forEach(function (guess) {
      (guess.token_ids || []).forEach(function (id) {
        // Keep the first opening guess for a token, matching the original cause.
        if (openingGuessByToken[id] == null) openingGuessByToken[id] = guess.word || '';
      });
    });
    (state.title_tokens || []).concat(state.body_tokens || []).forEach(function (token) {
      token.opened_by_guess = openingGuessByToken[token.id] || '';
    });
    renderTokens(title, state.title_tokens || [], lenForced);
    renderTitleDecoration(title, state);
    var fetched = root.querySelector('#censorly-fetched');
    if (fetched) {
      var fetchedLabel = state.wiki_fetched_label || '';
      fetched.hidden = !fetchedLabel;
      fetched.textContent = fetchedLabel;
    }
    var openedWords = root.querySelector('#censorly-opened-words');
    if (openedWords) {
      var openedCount = Number(state.opened_words_count) || 0;
      var totalCount = Number(state.total_words_count) || 0;
      var openedPercent = totalCount > 0 ? Math.round(openedCount * 100 / totalCount) : 0;
      openedWords.textContent = 'Открыто слов: ' + openedPercent + '% (' + openedCount +
        ' / ' + totalCount + ')';
    }
    renderTokens(body, state.body_tokens || [], lenForced);
    if (state.won && body) {
      body.querySelectorAll('.censorly-tok--revealed:not(.censorly-tok--guessed)').forEach(function (token) {
        token.classList.add('censorly-tok--solution-revealed');
      });
    }
    updateMaskAccessibility(root, root.classList.contains('censorly--show-endings'));
    var trunc = root.querySelector('#censorly-truncated');
    if (trunc) trunc.hidden = !state.truncated;
    var guesses = state.guesses || [];
    renderGuessTable(root, guesses, activeGuessIndex(root, guesses));
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

  function handleReplayFlags(root, data) {
    if (!data) return;
    if (data.reload_required) {
      var feedback = root && root.querySelector('#censorly-feedback');
      if (window.CensorlyReloadRequired) {
        window.CensorlyReloadRequired.show(feedback, window.location.href, document);
      }
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
    var timerRoot = document.querySelector('[data-daily-timer]');
    var timingSessionId = timerRoot && timerRoot.getAttribute('data-timing-session-id');
    var timingStateRevision = timerRoot && Number(
      timerRoot.getAttribute('data-timing-state-revision')
    );
    if (timingSessionId) body.timing_session_id = timingSessionId;
    var headers = {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken(),
      'X-Requested-With': 'XMLHttpRequest',
    };
    if (token) headers['X-Interoves-Gameplay-Context'] = token;
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
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
        if (data.daily_timing && document) {
          if (isFinite(timingStateRevision)) {
            data.daily_timing._client_revision = timingStateRevision;
          }
          document.dispatchEvent(new CustomEvent('interoves:daily-timing', {
            detail: data.daily_timing,
          }));
        }
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
    var endingsBtn = root.querySelector('#censorly-endings-toggle');
    var scrollToggle = root.querySelector('#censorly-scroll-settings-toggle');
    var scrollMenu = root.querySelector('#censorly-scroll-settings-menu');
    var hintBtn = root.querySelector('#censorly-hint-btn');
    var hintMode = root.querySelector('#censorly-hint-mode');
    var busy = false;
    var hasLocalAction = false;
    var actorSyncInFlight = false;
    var actorSyncPending = false;
    var actorSyncGeneration = 0;
    var actorSyncPromise = null;
    var actorSyncWaiters = [];
    var preferences = readDisplayPreferences();
    var scrollMode = syncScrollSettings(root, scrollMenu, preferences.scrollMode || root.getAttribute('data-scroll-mode'));
    var showEndings = typeof preferences.showEndings === 'boolean'
      ? preferences.showEndings
      : state.show_mask_endings !== false;
    var showLengths = preferences.showLengths === true;

    function syncActorState() {
      if (!stateUrl) return Promise.reject(new Error('state unavailable'));
      if (busy) {
        actorSyncPending = true;
        return new Promise(function (resolve, reject) {
          actorSyncWaiters.push({resolve: resolve, reject: reject});
        });
      }
      if (actorSyncInFlight) {
        actorSyncPending = true;
        return actorSyncPromise;
      }
      actorSyncInFlight = true;
      actorSyncPending = false;
      var requestGeneration = actorSyncGeneration;
      actorSyncPromise = fetch(stateUrl, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      }).then(function (response) {
        if (!response.ok) throw new Error('state unavailable');
        return response.json();
      }).then(function (data) {
        if (!data || data.status !== 'ok') throw new Error('state unavailable');
        if (requestGeneration !== actorSyncGeneration) {
          actorSyncPending = true;
          return;
        }
        if (busy) actorSyncPending = true;
        else {
          var current = root._censorlyState || {};
          var before = JSON.stringify([
            current.guesses || [], current.hints || current.hints_taken || 0,
            current.hinted_lemmas || [], !!current.won,
          ]);
          var after = JSON.stringify([
            data.guesses || [], data.hints || data.hints_taken || 0,
            data.hinted_lemmas || [], !!data.won,
          ]);
          if (before !== after) applyState(root, data, { preserveLen: true });
        }
      }).then(function (data) {
        actorSyncInFlight = false;
        if (actorSyncPending && !busy) {
          actorSyncPending = false;
          return syncActorState().then(function () { return data; });
        }
        return data;
      }, function (error) {
        actorSyncInFlight = false;
        if (actorSyncPending && !busy) {
          actorSyncPending = false;
          return syncActorState();
        }
        throw error;
      });
      actorSyncPromise.then(function () {
        if (actorSyncPending || actorSyncInFlight) return;
        var waiters = actorSyncWaiters.splice(0);
        waiters.forEach(function (waiter) { waiter.resolve(); });
      }, function (error) {
        var waiters = actorSyncWaiters.splice(0);
        waiters.forEach(function (waiter) { waiter.reject(error); });
      });
      return actorSyncPromise;
    }

    function onActorTrackEvent(event) {
      var detail = event && event.detail || {};
      if (String(detail.gameId || '') !== String(root.getAttribute('data-game-id') || '')) return;
      if (detail.taskId && String(detail.taskId) !== String(root.getAttribute('data-task-id') || '')) return;
      actorSyncGeneration += 1;
      syncActorState().catch(function () {});
    }

    document.addEventListener('interoves:track-task-changed', onActorTrackEvent);
    document.addEventListener('interoves:track-synced', function (event) {
      var detail = event && event.detail || {};
      if (!detail.initial || String(detail.gameId || '') !== String(root.getAttribute('data-game-id') || '')) return;
      actorSyncGeneration += 1;
      detail.promise = syncActorState();
    });
    document.addEventListener('interoves:track-resync', function (event) {
      var detail = event && event.detail || {};
      if (String(detail.gameId || '') !== String(root.getAttribute('data-game-id') || '')) return;
      actorSyncGeneration += 1;
      detail.promise = syncActorState();
    });

    // Anonymous pages do not have a Track socket. Reconcile when a player
    // returns to this tab so a guess made in another tab is reflected without
    // polling throughout the whole session.
    if (root.getAttribute('data-authenticated') !== '1') {
      document.addEventListener('visibilitychange', function () {
        if (document.visibilityState !== 'visible') return;
        actorSyncGeneration += 1;
        syncActorState().catch(function () {});
      });
    }

    root.classList.toggle('censorly--show-endings', showEndings);
    root.classList.toggle('censorly--show-lengths', showLengths);
    applyState(root, state);
    syncEndingsToggle(root, endingsBtn, showEndings);
    syncLengthsToggle(root, lengthsBtn, showLengths);
    if (scrollToggle && scrollMenu) {
      scrollToggle.addEventListener('click', function () {
        var open = scrollMenu.hidden;
        scrollMenu.hidden = !open;
        scrollToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
          var selectedOption = scrollMenu.querySelector('[aria-checked="true"]');
          if (selectedOption) selectedOption.focus();
        }
      });
      scrollMenu.addEventListener('click', function (event) {
        var option = event.target.closest('[data-scroll-option]');
        if (!option) return;
        scrollMode = syncScrollSettings(root, scrollMenu, option.getAttribute('data-scroll-option'));
        preferences.scrollMode = scrollMode;
        saveDisplayPreferences(preferences);
        scrollMenu.hidden = true;
        scrollToggle.setAttribute('aria-expanded', 'false');
        scrollToggle.focus();
      });
      scrollMenu.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        var options = Array.prototype.slice.call(scrollMenu.querySelectorAll('[data-scroll-option]'));
        var currentIndex = options.indexOf(document.activeElement);
        if (!options.length || currentIndex < 0) return;
        event.preventDefault();
        var step = event.key === 'ArrowDown' ? 1 : -1;
        var nextOption = options[(currentIndex + step + options.length) % options.length];
        scrollMode = syncScrollSettings(root, scrollMenu, nextOption.getAttribute('data-scroll-option'));
        preferences.scrollMode = scrollMode;
        saveDisplayPreferences(preferences);
        nextOption.focus();
      });
      document.addEventListener('click', function (event) {
        if (!scrollMenu.hidden && !event.target.closest('.censorly__scroll-settings')) {
          scrollMenu.hidden = true;
          scrollToggle.setAttribute('aria-expanded', 'false');
        }
      });
      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !scrollMenu.hidden) {
          scrollMenu.hidden = true;
          scrollToggle.setAttribute('aria-expanded', 'false');
          scrollToggle.focus();
        }
      });
    }
    syncMobileDockOffset(root);
    syncMobileKeyboardOffset(root);

    if (stateUrl) {
      fetch(stateUrl, {
        credentials: 'same-origin',
        cache: 'no-store',
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
          if (!hasLocalAction && data && data.status !== 'error') {
            applyState(root, data, { preserveLen: true });
          }
        })
        .catch(function () {});
    }

    if (historyBtn && historyWrap) {
      // On a narrow component the dock should start in its compact state:
      // history and its optional controls are available from the history
      // button, but do not cover the article while playing.
      var wasNarrowComponent = null;
      function syncHistoryLayout() {
        var isNarrowComponent = root.getBoundingClientRect().width <= 919;
        if (wasNarrowComponent === null) {
          wasNarrowComponent = isNarrowComponent;
          if (isNarrowComponent) historyWrap.hidden = true;
        } else if (wasNarrowComponent !== isNarrowComponent) {
          // Re-enter the compact mode after rotation, and restore the full
          // history panel when returning to the wide layout.
          wasNarrowComponent = isNarrowComponent;
          historyWrap.hidden = isNarrowComponent;
        }
        var open = !historyWrap.hidden;
        root.classList.toggle('censorly--history-open', open);
        historyBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
        historyBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      }
      syncHistoryLayout();
      window.addEventListener('resize', syncHistoryLayout);
      if (window.ResizeObserver) {
        var historyLayoutObserver = new ResizeObserver(syncHistoryLayout);
        historyLayoutObserver.observe(root);
        root._censorlyHistoryLayoutObserver = historyLayoutObserver;
      }
      historyBtn.addEventListener('click', function () {
        var open = historyWrap.hidden;
        historyWrap.hidden = !open;
        historyBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
        historyBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        root.classList.toggle('censorly--history-open', open);
      });
    }

    if (lengthsBtn) {
      lengthsBtn.addEventListener('click', function () {
        var on = !root.classList.contains('censorly--show-lengths');
        syncLengthsToggle(root, lengthsBtn, on);
        preferences.showLengths = on;
        saveDisplayPreferences(preferences);
        // Global toggle wins: drop per-word length overrides.
        root.querySelectorAll('.censorly-tok--mask[data-len-forced]').forEach(function (node) {
          node.removeAttribute('data-len-forced');
        });
      });
    }

    if (endingsBtn) {
      endingsBtn.addEventListener('click', function () {
        var on = !root.classList.contains('censorly--show-endings');
        syncEndingsToggle(root, endingsBtn, on);
        preferences.showEndings = on;
        saveDisplayPreferences(preferences);
      });
    }

    root.addEventListener('click', function (ev) {
      var mask = ev.target.closest('.censorly-tok--mask');
      if (mask && root.contains(mask)) {
        if (root.classList.contains('censorly--hint-pick')) {
          if (mask.dataset.titleLemma === '1') {
            setFeedback(root, 'Нельзя открывать слова, связанные со словами из названия', 'error');
            return;
          }
          if (busy) return;
          busy = true;
          actorSyncGeneration += 1;
          hasLocalAction = true;
          postJson(hintUrl, { token_id: Number(mask.dataset.id) }, root)
            .then(function (data) {
              if (handleReplayFlags(root, data)) return;
              if (data.status === 'error') {
                setFeedback(root, data.error || 'Не удалось взять подсказку', 'error');
                return;
              }
              root.classList.remove('censorly--hint-pick');
              if (hintMode) hintMode.hidden = true;
              applyState(root, data, { preserveLen: true });
              if (data.status === 'already_open') focusTokenIds(root, data.active_token_ids, true);
              var newly = data.newly_revealed || [];
              flashTokenIds(root, newly);
              if (newly.length) scrollToTokenId(root, newly[0]);
              if (data.status === 'already_open') {
                setFeedback(root, 'Это слово уже открыто', 'info');
              } else if (data.status !== 'won') {
                setFeedback(root, 'Подсказка открыта', '');
              }
            })
            .catch(function () {
              setFeedback(root, 'Сеть недоступна', 'error');
            })
            .finally(function () {
              busy = false;
              if (actorSyncPending) syncActorState().catch(function () {});
            });
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
        selectGuessRow(root, row);
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
        actorSyncGeneration += 1;
        var submit = root.querySelector('#censorly-submit');
        if (submit) {
          submit.classList.add('is-loading');
          submit.disabled = true;
          submit.setAttribute('aria-busy', 'true');
          submit.setAttribute('aria-label', 'Проверяем слово');
          submit.setAttribute('data-tooltip', 'Проверяем слово');
        }
        hasLocalAction = true;
        var submitScrollTokenIds = null;
        setFeedback(root, '');
        postJson(guessUrl, { word: word }, root)
          .then(function (data) {
            data = applyStateDelta(root, data);
            if (handleReplayFlags(root, data)) return;
            if (data.status === 'invalid' || data.status === 'duplicate' || data.status === 'initially_open' || data.status === 'error') {
              var feedbackKind = data.status === 'initially_open' ? 'info' : 'error';
              setFeedback(root, guessFeedbackHelper.feedback(data, word), feedbackKind);
              if (data.status !== 'error' || hasRenderableState(data)) {
                applyState(root, data, { preserveLen: true });
              }
              if (data.status === 'duplicate' && Number.isInteger(data.active_guess_index)) {
                focusGuess(root, data.active_guess_index, true);
                submitScrollTokenIds = guessTokenIds(root, data.active_guess_index);
              }
              if (guessFeedbackHelper.shouldClearInput(data.status)) input.value = '';
              return;
            }
            if (Array.isArray(data.guesses) && data.guesses.length) {
              root.setAttribute('data-active-guess-index', String(data.guesses.length - 1));
            }
            applyState(root, data, { preserveLen: true });
            if (data.status === 'already_open') {
              if (Number.isInteger(data.active_guess_index)) {
                focusGuess(root, data.active_guess_index, true);
                submitScrollTokenIds = Array.isArray(data.active_token_ids) && data.active_token_ids.length
                  ? data.active_token_ids
                  : guessTokenIds(root, data.active_guess_index);
              } else {
                focusTokenIds(root, data.active_token_ids, false);
                submitScrollTokenIds = data.active_token_ids;
              }
            }
            input.value = '';
            var newly = data.newly_revealed || [];
            flashTokenIds(root, newly);
            if (newly.length) submitScrollTokenIds = newly;
            if (data.status === 'hit') setFeedback(root, 'Есть совпадения: ' + (data.hits || 0), 'hit');
            else if (data.status === 'miss') setFeedback(root, 'Нет в тексте', 'error');
            else if (data.status === 'already_open') setFeedback(root, guessFeedbackHelper.feedback(data, word), '');
          })
          .catch(function () {
            setFeedback(root, 'Сеть недоступна', 'error');
          })
          .finally(function () {
            busy = false;
            if (actorSyncPending) syncActorState().catch(function () {});
            if (submit) {
              submit.classList.remove('is-loading');
              submit.setAttribute('aria-busy', 'false');
              submit.setAttribute('aria-label', 'Угадать');
              submit.setAttribute('data-tooltip', 'Угадать');
              submit.disabled = !!(input && input.disabled);
            }
            if (input && !input.disabled) input.focus();
            if (submitScrollTokenIds && submitScrollTokenIds.length) {
              window.requestAnimationFrame(function () {
                scrollAfterGuess(root, submitScrollTokenIds);
              });
            }
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
