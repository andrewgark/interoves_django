'use strict';

var assert = require('assert');

function fakeInput(value) {
  return { name: 'csrfmiddlewaretoken', value: value };
}

function fakeRoot(values) {
  var inputs = values.map(fakeInput);
  return {
    querySelectorAll: function (selector) {
      assert.strictEqual(selector, 'input[name="csrfmiddlewaretoken"]');
      return inputs;
    },
  };
}

global.document = {
  getElementById: function (id) {
    if (id !== 'interoves-page-csrf') return null;
    return {
      querySelector: function (selector) {
        assert.strictEqual(selector, 'input[name="csrfmiddlewaretoken"]');
        return fakeInput('page-token');
      },
    };
  },
};

require('./page_csrf.js');

var csrf = global.InterovesPageCsrf;
assert.strictEqual(csrf.pageToken(), 'page-token');

var fd = {
  value: 'teammate-token',
  set: function (name, value) {
    assert.strictEqual(name, 'csrfmiddlewaretoken');
    this.value = value;
  },
};
csrf.stampFormData(fd);
assert.strictEqual(fd.value, 'page-token');

var params = {
  value: 'teammate-token',
  set: function (name, value) {
    assert.strictEqual(name, 'csrfmiddlewaretoken');
    this.value = value;
  },
};
csrf.stampParams(params);
assert.strictEqual(params.value, 'page-token');

var root = fakeRoot(['teammate-a', 'teammate-b']);
csrf.stampRoot(root);
assert.strictEqual(root.querySelectorAll('input[name="csrfmiddlewaretoken"]')[0].value, 'page-token');
assert.strictEqual(root.querySelectorAll('input[name="csrfmiddlewaretoken"]')[1].value, 'page-token');

csrf.stampFormData(fd, 'explicit');
assert.strictEqual(fd.value, 'explicit');

console.log('page_csrf.test.js: ok');
