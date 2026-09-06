'use strict';

var assert = require('assert');
require('./new_task_hints.js');
var H = global.InterovesTaskHints;

function makeDoc(taskId, hidden) {
  var body = {
    hidden: hidden,
    getAttribute: function (name) {
      return name === 'data-hints-body' ? String(taskId) : null;
    },
  };
  var btn = {
    textContent: hidden ? 'подсказки' : 'скрыть подсказки',
    getAttribute: function (name) {
      return name === 'data-task-id' ? String(taskId) : null;
    },
  };
  var hint = {
    classList: {
      _cls: {},
      add: function (name) { this._cls[name] = true; },
      remove: function (name) { delete this._cls[name]; },
      contains: function (name) { return !!this._cls[name]; },
    },
    scrollIntoView: function (opts) { hint._scroll = opts; },
  };
  var root = {
    id: 'new-task-hints-' + taskId,
    querySelector: function (sel) {
      if (sel.indexOf('data-hint-number="1"') !== -1) return hint;
      return null;
    },
    scrollIntoView: function (opts) { root._scroll = opts; },
  };
  return {
    querySelector: function (sel) {
      if (sel === '[data-hints-body="' + taskId + '"]') return body;
      if (sel === '[data-hints-toggle][data-task-id="' + taskId + '"]') return btn;
      return null;
    },
    querySelectorAll: function (sel) {
      if (sel === '[data-hints-body]') return [body];
      return [];
    },
    getElementById: function (id) {
      return id === 'new-task-hints-' + taskId ? root : null;
    },
    _body: body,
    _btn: btn,
    _root: root,
    _hint: hint,
  };
}

global.document = makeDoc(12, true);
global.setTimeout = function () { return 0; };

assert.strictEqual(H.isOpen(12), false);
assert.deepStrictEqual(H.captureOpen(['12']), {});

assert.strictEqual(H.setOpen(12, true, { scroll: true }), true);
assert.strictEqual(global.document._body.hidden, false);
assert.strictEqual(global.document._btn.textContent, 'скрыть подсказки');
assert.strictEqual(H.isOpen(12), true);
assert.deepStrictEqual(H.captureOpen(['12']), { '12': true });
assert.ok(global.document._root._scroll);
assert.strictEqual(global.document._root._scroll.block, 'start');

H.setOpen(12, false);
assert.strictEqual(global.document._body.hidden, true);
assert.strictEqual(global.document._btn.textContent, 'подсказки');

H.restoreOpen({ '12': true });
assert.strictEqual(global.document._body.hidden, false);
assert.strictEqual(global.document._btn.textContent, 'скрыть подсказки');

global.document._root._scroll = null;
H.setOpen(12, true, { scroll: true, hintNumber: '1' });
assert.ok(global.document._hint.classList.contains('is-just-revealed'));
assert.ok(global.document._hint._scroll);
assert.strictEqual(global.document._hint._scroll.block, 'nearest');

var rafs = [];
global.requestAnimationFrame = function (fn) { rafs.push(fn); return rafs.length; };
var ran = false;
H.afterPreservedScroll(function () { ran = true; });
assert.strictEqual(rafs.length, 1);
rafs[0]();
assert.strictEqual(rafs.length, 2);
rafs[1]();
assert.strictEqual(rafs.length, 3);
rafs[2]();
assert.strictEqual(ran, true);

console.log('new_task_hints.test.js: ok');
