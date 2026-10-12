'use strict';

var assert = require('assert');
var reloadRequired = require('./censorly_reload_required.js');

var appended = [];
var feedback = {
  hidden: true,
  className: '',
  textContent: '',
  appendChild: function (node) { appended.push(node); },
};
var documentMock = {
  createElement: function (tag) {
    assert.strictEqual(tag, 'a');
    return {};
  },
};

reloadRequired.show(feedback, '/censorly/1/', documentMock);

assert.strictEqual(feedback.hidden, false);
assert.strictEqual(feedback.className, 'censorly__feedback is-error');
assert.strictEqual(feedback.textContent, 'Состояние игры обновилось. ');
assert.strictEqual(appended.length, 1);
assert.strictEqual(appended[0].textContent, 'Обновить страницу');
assert.strictEqual(appended[0].href, '/censorly/1/');

console.log('censorly_reload_required.test.js: ok');
