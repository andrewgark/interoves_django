/*
 * «Версия сайта»: the interoves.com / ru.interoves.com menu and hint
 * (games/site/http/site_version.py, site/partials/site_version_*.html).
 *
 * The decision is kept in the interoves_site_version cookie on .interoves.com
 * so both hosts see it: "chosen:<version>:<unix time>" after a switch,
 * "stayed:<version>:<unix time>" after "Остаться здесь". When cookies are
 * blocked it falls back to this host's localStorage, and a switch carries
 * the choice in the URL fragment. If nothing can be stored the hint is not
 * shown at all, so it never nags on every page.
 *
 * Metrika goals (docs/analytics/events.md): site_version_prompt_shown,
 * site_version_prompt_dismissed and site_version_switch_clicked, each with
 * from/to versions. A switch leaves for the other host, whose localStorage
 * never retries this one's queue, so the navigation waits briefly for the
 * goal's delivery.
 */
(function () {
  'use strict';

  var COOKIE = 'interoves_site_version';
  var CHOSEN_MAX_AGE = 365 * 86400;
  var STAYED_MAX_AGE = 30 * 86400;
  var SEEN_KEY = 'interoves_site_version_hint_seen';
  var FRAGMENT = 'iv-site-version=chosen';
  var SHOW_DELAY_MS = 2500;
  var LEAVE_WAIT_MS = 400;
  var DELIVERED_EVENT = 'interoves:analytics-goal-delivered';
  var MAX_AGE = { chosen: CHOSEN_MAX_AGE, stayed: STAYED_MAX_AGE };
  var CLOCK_SKEW = 86400; /* as in site_version.py */

  function now() {
    return Math.floor(Date.now() / 1000);
  }

  function cookieDomain() {
    var host = location.hostname;
    return host === 'interoves.com' || /\.interoves\.com$/.test(host) ? '; Domain=.interoves.com' : '';
  }

  function readCookie() {
    try {
      var parts = document.cookie ? document.cookie.split('; ') : [];
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].indexOf(COOKIE + '=') === 0) return parts[i].slice(COOKIE.length + 1);
      }
    } catch (e) { /* cookies disabled */ }
    return '';
  }

  function setCookie(value, maxAge, domain) {
    /* Plain value ([a-z0-9:]): Django reads cookies without URL-decoding. */
    document.cookie = COOKIE + '=' + value + '; Path=/; Max-Age=' + maxAge +
      domain + '; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
  }

  function writeCookie(value, maxAge) {
    try {
      setCookie(value, maxAge, cookieDomain());
      return readCookie() === value || maxAge === 0;
    } catch (e) {
      return false;
    }
  }

  function storage(kind) {
    try {
      var s = window[kind];
      var probe = '__iv_probe__';
      s.setItem(probe, '1');
      s.removeItem(probe);
      return s;
    } catch (e) {
      return null;
    }
  }

  function parse(value) {
    var parts = String(value || '').split(':');
    if (parts.length !== 3 || !MAX_AGE[parts[0]] || (parts[1] !== 'ru' && parts[1] !== 'intl')) return null;
    var at = parseInt(parts[2], 10);
    var age = now() - at;
    if (!isFinite(at) || age < -CLOCK_SKEW || age >= MAX_AGE[parts[0]]) return null;
    return { kind: parts[0], version: parts[1] };
  }

  function decision() {
    var local = storage('localStorage');
    return parse(readCookie()) || (local ? parse(local.getItem(COOKIE)) : null);
  }

  /* Returns true when the decision was stored somewhere. */
  function remember(kind, version) {
    var value = kind + ':' + version + ':' + now();
    var local = storage('localStorage');
    if (local) local.setItem(COOKIE, value);
    return writeCookie(value, MAX_AGE[kind]) || !!local;
  }

  function forget() {
    try {
      /* Also a host-only copy, which a Domain= deletion leaves behind. */
      setCookie('', 0, cookieDomain());
      setCookie('', 0, '');
    } catch (e) { /* cookies disabled */ }
    var local = storage('localStorage');
    if (local) local.removeItem(COOKIE);
  }

  function canRemember() {
    return !!storage('localStorage') || navigator.cookieEnabled === true;
  }

  function currentVersion() {
    var host = location.hostname;
    if (/^ru\./.test(host)) return 'ru';
    return host === 'interoves.com' || /\.interoves\.com$/.test(host) ? 'intl' : 'other';
  }

  /* Calls done() once the goal is delivered, or after LEAVE_WAIT_MS, or at
     once when Metrika is not there (blocked, not loaded yet). */
  function trackGoal(goal, params, done) {
    var analytics = window.interovesAnalytics;
    var key = 'site-version:' + goal + ':' + Date.now() + ':' + String(Math.random()).slice(2);
    var finished = false;
    var timer = null;
    function finish() {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      window.removeEventListener(DELIVERED_EVENT, onDelivered);
      if (done) done();
    }
    function onDelivered(e) {
      if (e.detail && e.detail.key === key) finish();
    }
    /* Listen first: delivery can complete inside trackYandexGoalOnce. */
    if (done) window.addEventListener(DELIVERED_EVENT, onDelivered);
    var dispatched = false;
    try {
      dispatched = !!(analytics && typeof analytics.trackYandexGoalOnce === 'function' &&
        analytics.trackYandexGoalOnce(key, goal, params));
    } catch (e) { /* analytics never breaks the page */ }
    if (!done) return;
    if (dispatched) timer = setTimeout(finish, LEAVE_WAIT_MS);
    else finish();
  }

  function hintParams(hint, extra) {
    var params = { from: hint.getAttribute('data-site-version-current'), to: hint.getAttribute('data-site-version-hint') };
    for (var name in extra) params[name] = extra[name];
    return params;
  }

  function dismissHint(hint, method) {
    if (!hint || hint.hasAttribute('hidden')) return;
    hideHint(hint);
    trackGoal('site_version_prompt_dismissed', hintParams(hint, { method: method }));
  }

  /* An ordinary click that would open the link in this tab. */
  function plainClick(e, link) {
    return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey &&
      !e.defaultPrevented && (!link.target || link.target === '_self');
  }

  /* A switch made while cookies were blocked arrives as #iv-site-version=chosen. */
  function adoptFragmentChoice() {
    if (location.hash !== '#' + FRAGMENT) return;
    remember('chosen', currentVersion() === 'ru' ? 'ru' : 'intl');
    try {
      history.replaceState(history.state, '', location.pathname + location.search);
    } catch (e) { /* ignore */ }
  }

  function hideHint(hint) {
    if (!hint) return;
    hint.classList.remove('is-visible');
    hint.setAttribute('hidden', '');
  }

  var leaving = false;

  document.addEventListener('click', function (e) {
    var choice = e.target.closest('[data-site-version-choice]');
    if (choice) {
      if (leaving) {
        e.preventDefault();
        return;
      }
      var version = choice.getAttribute('data-site-version-choice');
      var shared = writeCookie('chosen:' + version + ':' + now(), CHOSEN_MAX_AGE);
      if (!shared && choice.href && choice.href.indexOf('#') < 0) choice.href += '#' + FRAGMENT;
      var params = {
        from: currentVersion(),
        to: version,
        source: choice.closest('[data-site-version-hint]') ? 'prompt' : 'menu',
      };
      if (!plainClick(e, choice)) {
        trackGoal('site_version_switch_clicked', params); /* new tab: this page stays */
        return;
      }
      e.preventDefault();
      leaving = true;
      var href = choice.href;
      trackGoal('site_version_switch_clicked', params, function () { location.assign(href); });
      return;
    }
    if (e.target.closest('[data-site-version-reset]')) {
      forget();
      syncResetButtons();
      return;
    }
    var stay = e.target.closest('[data-site-version-stay]');
    if (stay) {
      var hint = stay.closest('[data-site-version-hint]');
      if (hint) {
        remember('stayed', hint.getAttribute('data-site-version-current'));
        dismissHint(hint, stay.classList.contains('new-site-version-hint__close') ? 'close' : 'stay');
      }
      return;
    }
    /* Close the desktop popover on an outside click. */
    var menus = document.querySelectorAll('.new-site-version--nav[open]');
    for (var i = 0; i < menus.length; i++) {
      if (!menus[i].contains(e.target)) menus[i].removeAttribute('open');
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = document.querySelector('.new-site-version--nav[open]');
    if (open) {
      open.removeAttribute('open');
      var summary = open.querySelector('summary');
      if (summary) summary.focus();
    }
    dismissHint(document.querySelector('[data-site-version-hint]:not([hidden])'), 'escape');
  });

  function syncResetButtons() {
    var has = !!decision();
    var buttons = document.querySelectorAll('[data-site-version-reset]');
    for (var i = 0; i < buttons.length; i++) {
      if (has) buttons[i].removeAttribute('hidden');
      else buttons[i].setAttribute('hidden', '');
    }
  }

  function scheduleHint() {
    var hint = document.querySelector('[data-site-version-hint]');
    if (!hint || decision() || !canRemember()) return;
    var session = storage('sessionStorage');
    if (!session || session.getItem(SEEN_KEY)) return;
    function show() {
      /* Not while a dialog (login, rules, tutorials) is open, nor in a
         background tab: being "seen" there would use up the one showing
         per session. */
      if (document.hidden || document.querySelector('.new-rules-modal.is-open')) {
        setTimeout(show, SHOW_DELAY_MS);
        return;
      }
      if (decision()) return; /* decided in another tab meanwhile */
      session.setItem(SEEN_KEY, '1');
      hint.removeAttribute('hidden');
      requestAnimationFrame(function () { hint.classList.add('is-visible'); });
      trackGoal('site_version_prompt_shown', hintParams(hint, {}));
    }
    setTimeout(show, SHOW_DELAY_MS);
  }

  adoptFragmentChoice();
  syncResetButtons();
  scheduleHint();
})();
