/**
 * "Не получается решить?" — a quiet pointer to a daily game's hint button.
 *
 * Stage 1: when the player has been stuck for a while, the hint button glows
 * a few times. Stage 2: if they stay stuck, a small non-modal callout points
 * at it. The callout is shown once per game kind ever; "Не подсказывать"
 * turns both stages off everywhere. Seen keys live in localStorage and, for a
 * signed-in player, on the server (games.identity.ui_notices).
 *
 * Time counts only while the tab is visible and the player is active, and a
 * stage starts only after a few seconds without typing.
 * Local check: node static/js/stuck_nudge.test.js
 */
(function (global) {
  'use strict';

  var OFF_KEY = 'stuck_nudge.off';
  var STORAGE_PREFIX = 'interoves_ui_notices:';
  var TICK_MS = 1000;
  // Without input for this long the player is away, not stuck.
  var AWAY_MS = 30000;
  // A stage waits for this pause in typing so nothing pops up mid-word.
  var QUIET_MS = 4000;
  // A guess that opens nothing within this window counts as a miss.
  var MISS_WINDOW_MS = 2500;
  var GLOW_MS = 3600;
  var EDGE_GAP = 12;
  var ARROW_GAP = 10;

  function reached(threshold, activeMs, misses) {
    if (!threshold) return false;
    if (threshold.ms && activeMs >= threshold.ms) return true;
    return !!(threshold.misses && misses >= threshold.misses);
  }

  /** Which stage is due now: 'callout', 'glow' or ''. */
  function dueStage(state, options) {
    if (state.off || state.done) return '';
    if (state.now - state.lastInputAt < QUIET_MS) return '';
    if (!state.calloutSeen && !state.hinted &&
        reached(options.calloutAfter, state.activeMs, state.misses)) {
      return 'callout';
    }
    if (!state.glowShown && reached(options.glowAfter, state.activeMs, state.misses)) {
      return 'glow';
    }
    return '';
  }

  /**
   * Fixed position for a callout of size ``box`` next to ``rect`` in a
   * ``viewport``: above the target when it fits, else below.
   */
  function place(rect, box, viewport) {
    var above = rect.top - box.height - ARROW_GAP >= EDGE_GAP;
    var top = above ? rect.top - box.height - ARROW_GAP : rect.bottom + ARROW_GAP;
    var center = rect.left + rect.width / 2;
    var maxLeft = Math.max(EDGE_GAP, viewport.width - box.width - EDGE_GAP);
    var left = Math.min(Math.max(center - box.width / 2, EDGE_GAP), maxLeft);
    var arrow = Math.min(Math.max(center - left, 16), box.width - 16);
    return {top: Math.round(top), left: Math.round(left), arrow: Math.round(arrow), side: above ? 'above' : 'below'};
  }

  function readLocal(storageKey) {
    try {
      var list = JSON.parse(global.localStorage.getItem(storageKey) || '[]');
      return Array.isArray(list) ? list : [];
    } catch (err) { return []; }
  }

  function writeLocal(storageKey, list) {
    try { global.localStorage.setItem(storageKey, JSON.stringify(list)); } catch (err) {}
  }

  function track(goal, params) {
    try {
      if (global.interovesAnalytics && typeof global.interovesAnalytics.trackYandexGoal === 'function') {
        global.interovesAnalytics.trackYandexGoal(goal, params);
      }
    } catch (err) {}
  }

  function csrfToken(doc) {
    if (global.InterovesPageCsrf) return global.InterovesPageCsrf.pageToken();
    var el = doc.querySelector('#interoves-page-csrf input[name="csrfmiddlewaretoken"], input[name="csrfmiddlewaretoken"]');
    return el ? el.value : '';
  }

  function usable(el) {
    if (!el || el.disabled || el.hidden || !el.isConnected) return false;
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function inViewport(rect, win) {
    return rect.bottom > 0 && rect.top < win.innerHeight;
  }

  function modalOpen(doc) {
    return !!doc.querySelector('.new-rules-modal:not([hidden]), [data-tutorial-shell]:not([hidden])');
  }

  /**
   * options: game, scope (selector of the game board; boards get re-rendered,
   * so it is matched per event), target() -> element, hintSelector, progress() -> number,
   * done() -> bool, hinted() -> bool, text, glowAfter, calloutAfter
   * ({ms, misses}), countMisses (default true).
   */
  function create(options) {
    var doc = options.document || global.document;
    var config = doc.getElementById('stuck-nudge-config');
    if (!config || !options.scope) return null;
    var gameKey = 'stuck_nudge.' + options.game;
    var storageKey = STORAGE_PREFIX + (config.getAttribute('data-user-id') || 'anon');
    var signedIn = !!config.getAttribute('data-user-id');
    var serverSeen = (config.getAttribute('data-seen') || '').split(/\s+/).filter(Boolean);
    var localSeen = readLocal(storageKey);
    var seen = {};
    serverSeen.concat(localSeen).forEach(function (key) { seen[key] = true; });

    var state = {
      activeMs: 0, misses: 0, lastInputAt: 0, lastTickAt: Date.now(),
      lastActivityAt: Date.now(), glowShown: false, callout: null,
      progress: safeProgress(), pendingGuess: null, stopped: false,
    };

    function safeProgress() {
      try { return Number(options.progress()) || 0; } catch (err) { return 0; }
    }

    function remember(key) {
      if (seen[key]) return;
      seen[key] = true;
      var list = readLocal(storageKey);
      if (list.indexOf(key) === -1) list.push(key);
      writeLocal(storageKey, list);
      if (signedIn) send(key);
    }

    function send(key) {
      var url = config.getAttribute('data-url');
      if (!url || typeof global.fetch !== 'function') return;
      global.fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': csrfToken(doc),
        },
        body: 'key=' + encodeURIComponent(key),
        credentials: 'same-origin',
        keepalive: true,
      }).catch(function () {});
    }

    // Keys seen while signed out on this browser follow the player's account.
    if (signedIn) {
      readLocal(STORAGE_PREFIX + 'anon').concat(localSeen).forEach(function (key) {
        if (serverSeen.indexOf(key) === -1) { seen[key] = false; remember(key); }
      });
    }

    function params(extra) {
      var out = {game: options.game};
      Object.keys(extra || {}).forEach(function (k) { out[k] = extra[k]; });
      return out;
    }

    function glow(target) {
      state.glowShown = true;
      target.classList.remove('new-stuck-glow');
      void target.offsetWidth;
      target.classList.add('new-stuck-glow');
      global.setTimeout(function () { target.classList.remove('new-stuck-glow'); }, GLOW_MS);
      track('stuck_nudge_glow', params());
    }

    function buildCallout() {
      var box = doc.createElement('div');
      box.className = 'new-stuck-callout';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      box.innerHTML =
        '<p class="new-stuck-callout__title">Не получается решить?</p>' +
        '<p class="new-stuck-callout__text"></p>' +
        '<div class="new-stuck-callout__actions">' +
        '<button type="button" class="new-btn new-btn--text new-btn--mini new-stuck-callout__off" data-stuck-off>Не подсказывать</button>' +
        '<button type="button" class="new-btn new-btn--mini" data-stuck-show hidden>Показать</button>' +
        '<button type="button" class="new-btn new-btn--ghost new-btn--mini" data-stuck-ok>Понятно</button>' +
        '</div>' +
        '<span class="new-stuck-callout__arrow" aria-hidden="true"></span>';
      box.querySelector('.new-stuck-callout__text').textContent = options.text;
      box.querySelector('[data-stuck-ok]').addEventListener('click', function () { hideCallout('ok'); });
      box.querySelector('[data-stuck-off]').addEventListener('click', function () {
        remember(OFF_KEY);
        hideCallout('off');
      });
      box.querySelector('[data-stuck-show]').addEventListener('click', function () {
        var target = options.target();
        if (!target) return;
        target.scrollIntoView({block: 'center', behavior: 'smooth'});
        global.setTimeout(function () {
          var current = options.target();
          if (current) glow(current);
        }, 450);
      });
      return box;
    }

    function showCallout() {
      remember(gameKey);
      state.callout = buildCallout();
      doc.body.appendChild(state.callout);
      position();
      track('stuck_nudge_shown', params());
    }

    function hideCallout(reason) {
      if (!state.callout) return;
      state.callout.remove();
      state.callout = null;
      if (reason) track('stuck_nudge_closed', params({reason: reason}));
    }

    // Anchored next to the button when it is on screen; otherwise docked to
    // the screen edge with a "Показать" button (salad's list may be below).
    function position() {
      var box = state.callout;
      if (!box) return;
      var target = options.target();
      if (!usable(target)) { hideCallout('gone'); return; }
      var rect = target.getBoundingClientRect();
      var docked = !inViewport(rect, global);
      box.classList.toggle('new-stuck-callout--docked', docked);
      box.querySelector('[data-stuck-show]').hidden = !docked;
      // Docked, a tap anywhere else closes it; "Понятно" comes back once anchored.
      box.querySelector('[data-stuck-ok]').hidden = docked;
      if (docked) {
        box.classList.toggle('new-stuck-callout--docked-top', rect.bottom <= 0);
        box.style.top = box.style.left = '';
        return;
      }
      var spot = place(rect, {width: box.offsetWidth, height: box.offsetHeight},
        {width: doc.documentElement.clientWidth, height: global.innerHeight});
      box.style.top = spot.top + 'px';
      box.style.left = spot.left + 'px';
      box.style.setProperty('--stuck-arrow-x', spot.arrow + 'px');
      box.setAttribute('data-side', spot.side);
    }

    function tick() {
      if (state.stopped) return;
      var now = Date.now();
      var elapsed = Math.min(now - state.lastTickAt, TICK_MS * 2);
      state.lastTickAt = now;

      var progress = safeProgress();
      if (progress !== state.progress) {
        var advanced = progress > state.progress;
        state.progress = progress;
        if (advanced) {
          state.activeMs = 0;
          state.misses = 0;
          state.glowShown = false;
          state.pendingGuess = null;
          hideCallout('progress');
        }
      }
      if (state.pendingGuess && now - state.pendingGuess.at >= MISS_WINDOW_MS) {
        if (state.progress <= state.pendingGuess.progress) state.misses += 1;
        state.pendingGuess = null;
      }

      var done = false;
      try { done = !!options.done(); } catch (err) {}
      if (seen[OFF_KEY] || done) {
        hideCallout(done ? 'done' : '');
        return;
      }
      if (doc.hidden || now - state.lastActivityAt > AWAY_MS) return;
      state.activeMs += elapsed;

      if (state.callout || modalOpen(doc)) return;
      var hinted = false;
      try { hinted = !!(options.hinted && options.hinted()); } catch (err) {}
      var stage = dueStage({
        now: now, lastInputAt: state.lastInputAt, activeMs: state.activeMs,
        misses: state.misses, glowShown: state.glowShown,
        calloutSeen: !!seen[gameKey], hinted: hinted, off: false, done: false,
      }, options);
      if (!stage) return;
      var target = options.target();
      if (!usable(target)) return;
      if (stage === 'callout') showCallout();
      else if (inViewport(target.getBoundingClientRect(), global)) glow(target);
    }

    function inScope(el) {
      return !!(el && el.closest && el.closest(options.scope));
    }

    function onActivity() { state.lastActivityAt = Date.now(); }
    function onInput() { state.lastInputAt = state.lastActivityAt = Date.now(); }
    function onGuess() {
      if (options.countMisses === false) return;
      state.pendingGuess = {at: Date.now(), progress: safeProgress()};
    }

    ['pointerdown', 'scroll', 'mousemove', 'touchstart'].forEach(function (name) {
      global.addEventListener(name, onActivity, {passive: true, capture: true});
    });
    doc.addEventListener('keydown', function (event) {
      onInput();
      if (event.key === 'Escape' && state.callout) hideCallout('esc');
      if (event.key === 'Enter' && inScope(event.target) &&
          event.target.matches('input, textarea')) {
        onGuess();
      }
    }, true);
    doc.addEventListener('input', onInput, true);
    doc.addEventListener('submit', function (event) {
      if (!inScope(event.target)) return;
      // Hint forms are submits too; they are handled below.
      if (options.hintSelector && event.target.querySelector(options.hintSelector)) return;
      onGuess();
    }, true);
    doc.addEventListener('click', function (event) {
      var el = event.target;
      if (state.callout && !state.callout.contains(el)) hideCallout('outside');
      if (options.hintSelector && el.closest && el.closest(options.hintSelector)) {
        // The player found the hint button: no callout for this game again.
        hideCallout('');
        if (!seen[gameKey]) track('stuck_nudge_hint', params({glowed: state.glowShown}));
        remember(gameKey);
      }
    }, true);
    global.addEventListener('resize', position);
    global.addEventListener('scroll', position, {passive: true, capture: true});
    global.setInterval(tick, TICK_MS);

    return {
      stop: function () { state.stopped = true; hideCallout(''); },
    };
  }

  global.InterovesStuckNudge = {
    create: create,
    dueStage: dueStage,
    place: place,
    QUIET_MS: QUIET_MS,
  };
})(typeof window !== 'undefined' ? window : globalThis);
