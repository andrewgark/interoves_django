'use strict';

// site_version.js against a minimal fake DOM: the hint, the switch links and
// their Metrika goals. Run: node static/js/site_version.test.js

var assert = require('assert');
var fs = require('fs');
var path = require('path');
var vm = require('vm');

var SOURCE = fs.readFileSync(path.join(__dirname, 'site_version.js'), 'utf8');

function element(attrs, parent, classes) {
  var el = {
    attrs: Object.assign({}, attrs || {}),
    parent: parent || null,
    href: (attrs && attrs.href) || '',
    target: '',
    classList: {
      list: (classes || []).slice(),
      add: function (c) { if (this.list.indexOf(c) < 0) this.list.push(c); },
      remove: function (c) { this.list = this.list.filter(function (x) { return x !== c; }); },
      contains: function (c) { return this.list.indexOf(c) >= 0; },
    },
    getAttribute: function (name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    hasAttribute: function (name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); },
    setAttribute: function (name, value) { this.attrs[name] = String(value); },
    removeAttribute: function (name) { delete this.attrs[name]; },
    closest: function (selector) {
      var name = selector.replace(/^\[|\]$/g, '');
      for (var node = this; node; node = node.parent) {
        if (node.hasAttribute(name)) return node;
      }
      return null;
    },
    contains: function (other) {
      for (var node = other; node; node = node.parent) if (node === this) return true;
      return false;
    },
    querySelector: function () { return null; },
  };
  return el;
}

function memoryStorage() {
  var values = Object.create(null);
  return {
    getItem: function (k) { return k in values ? values[k] : null; },
    setItem: function (k, v) { values[k] = String(v); },
    removeItem: function (k) { delete values[k]; },
  };
}

function eventTarget(target) {
  var listeners = Object.create(null);
  target.addEventListener = function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); };
  target.removeEventListener = function (type, fn) {
    listeners[type] = (listeners[type] || []).filter(function (x) { return x !== fn; });
  };
  target.dispatchEvent = function (event) {
    (listeners[event.type] || []).slice().forEach(function (fn) { fn(event); });
    return true;
  };
  return target;
}

// Loads site_version.js on interoves.com with a hint suggesting ru.
// options.analytics: 'deliver' (callback inside trackYandexGoalOnce),
// 'pending' (accepted, delivered later), 'absent'.
function load(options) {
  options = options || {};
  var hint = element({
    'data-site-version-hint': 'ru',
    'data-site-version-current': 'intl',
    hidden: '',
  });
  var hintSwitch = element({ 'data-site-version-choice': 'ru', href: 'https://ru.interoves.com/ladder/' }, hint);
  var stayButton = element({ 'data-site-version-stay': '' }, hint);
  var closeButton = element({ 'data-site-version-stay': '' }, hint, ['new-site-version-hint__close']);
  var menu = element({}, null, ['new-site-version--nav']);
  var menuSwitch = element({ 'data-site-version-choice': 'ru', href: 'https://ru.interoves.com/ladder/' }, menu);

  var timers = [];
  var goals = [];
  var assigned = [];
  var cookieJar = '';
  var documentObj = eventTarget({
    hidden: false,
    querySelector: function (selector) {
      if (selector === '[data-site-version-hint]') return hint;
      if (selector === '[data-site-version-hint]:not([hidden])') return hint.hasAttribute('hidden') ? null : hint;
      return null;
    },
    querySelectorAll: function () { return []; },
  });
  Object.defineProperty(documentObj, 'cookie', {
    get: function () { return cookieJar; },
    set: function (value) {
      var pair = value.split(';')[0];
      cookieJar = /=$/.test(pair) || /Max-Age=0/.test(value) ? '' : pair;
    },
  });

  var sandbox = eventTarget({
    document: documentObj,
    location: {
      hostname: 'interoves.com',
      protocol: 'https:',
      hash: '',
      pathname: '/ladder/',
      search: '',
      assign: function (url) { assigned.push(url); },
    },
    navigator: { cookieEnabled: true },
    history: { replaceState: function () {} },
    localStorage: memoryStorage(),
    sessionStorage: memoryStorage(),
    requestAnimationFrame: function (fn) { fn(); },
    setTimeout: function (fn, ms) { timers.push({ fn: fn, ms: ms, active: true }); return timers.length; },
    clearTimeout: function (id) { if (timers[id - 1]) timers[id - 1].active = false; },
    CustomEvent: function (type, init) { this.type = type; this.detail = init && init.detail; },
  });
  sandbox.window = sandbox;
  if (options.analytics !== 'absent') {
    sandbox.interovesAnalytics = {
      trackYandexGoalOnce: function (key, goal, params) {
        goals.push({ key: key, goal: goal, params: params });
        if (options.analytics === 'deliver') {
          sandbox.dispatchEvent(new sandbox.CustomEvent('interoves:analytics-goal-delivered', { detail: { key: key, goal: goal } }));
        }
        return true;
      },
    };
  }
  vm.runInNewContext(SOURCE, sandbox);

  function click(target, init) {
    var event = Object.assign({
      type: 'click', target: target, button: 0, defaultPrevented: false,
      preventDefault: function () { this.defaultPrevented = true; },
    }, init || {});
    documentObj.dispatchEvent(event);
    return event;
  }
  function runTimers() {
    timers.splice(0).forEach(function (t) { if (t.active) t.fn(); });
  }
  return {
    sandbox: sandbox, hint: hint, hintSwitch: hintSwitch, stayButton: stayButton,
    closeButton: closeButton, menuSwitch: menuSwitch, goals: goals, assigned: assigned,
    timers: timers, click: click, runTimers: runTimers,
    cookie: function () { return cookieJar; },
    deliver: function (key) {
      sandbox.dispatchEvent(new sandbox.CustomEvent('interoves:analytics-goal-delivered', { detail: { key: key } }));
    },
  };
}

(function testShownThenStay() {
  var page = load({ analytics: 'pending' });
  page.runTimers();
  assert.strictEqual(page.hint.hasAttribute('hidden'), false);
  assert.strictEqual(page.goals[0].goal, 'site_version_prompt_shown');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(page.goals[0].params)), { from: 'intl', to: 'ru' });

  page.click(page.stayButton);
  assert.strictEqual(page.hint.hasAttribute('hidden'), true);
  assert.strictEqual(page.goals[1].goal, 'site_version_prompt_dismissed');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(page.goals[1].params)), { from: 'intl', to: 'ru', method: 'stay' });
  assert.ok(/^interoves_site_version=stayed:intl:\d+$/.test(page.cookie()));
  // A hidden hint is not dismissed twice.
  page.click(page.closeButton);
  assert.strictEqual(page.goals.length, 2);
})();

(function testCloseAndEscapeAreDismissals() {
  var page = load({ analytics: 'pending' });
  page.runTimers();
  page.click(page.closeButton);
  assert.strictEqual(page.goals[1].params.method, 'close');

  page = load({ analytics: 'pending' });
  page.runTimers();
  page.sandbox.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
  assert.strictEqual(page.goals[1].goal, 'site_version_prompt_dismissed');
  assert.strictEqual(page.goals[1].params.method, 'escape');
})();

(function testSwitchWaitsForDelivery() {
  var page = load({ analytics: 'pending' });
  var event = page.click(page.hintSwitch);
  assert.strictEqual(event.defaultPrevented, true);
  assert.strictEqual(page.goals[0].goal, 'site_version_switch_clicked');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(page.goals[0].params)), { from: 'intl', to: 'ru', source: 'prompt' });
  assert.ok(/^interoves_site_version=chosen:ru:\d+$/.test(page.cookie()));
  assert.deepStrictEqual(page.assigned, []);
  // A second click while leaving neither navigates nor tracks again.
  assert.strictEqual(page.click(page.hintSwitch).defaultPrevented, true);
  assert.strictEqual(page.goals.length, 1);

  page.deliver(page.goals[0].key);
  assert.deepStrictEqual(page.assigned, ['https://ru.interoves.com/ladder/']);
  page.runTimers(); // the cancelled timeout must not navigate again
  assert.strictEqual(page.assigned.length, 1);
})();

(function testSwitchGivesUpWaitingAfterTimeout() {
  var page = load({ analytics: 'pending' });
  page.click(page.menuSwitch);
  assert.strictEqual(page.goals[0].params.source, 'menu');
  var wait = page.timers.filter(function (t) { return t.active && t.ms === 400; });
  assert.strictEqual(wait.length, 1);
  wait[0].fn();
  assert.deepStrictEqual(page.assigned, ['https://ru.interoves.com/ladder/']);
})();

(function testSwitchDeliveredSynchronouslyOrWithoutMetrika() {
  var page = load({ analytics: 'deliver' });
  page.click(page.menuSwitch);
  assert.strictEqual(page.assigned.length, 1);

  page = load({ analytics: 'absent' });
  page.click(page.menuSwitch);
  assert.strictEqual(page.assigned.length, 1);
})();

(function testModifiedClickKeepsTheBrowserBehaviour() {
  var page = load({ analytics: 'pending' });
  var event = page.click(page.menuSwitch, { ctrlKey: true });
  assert.strictEqual(event.defaultPrevented, false);
  assert.strictEqual(page.goals[0].goal, 'site_version_switch_clicked');
  page.runTimers();
  assert.deepStrictEqual(page.assigned, []);
})();

console.log('site_version.test.js: ok');
