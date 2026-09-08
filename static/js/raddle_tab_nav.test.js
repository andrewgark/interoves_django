'use strict';

var assert = require('assert');
require('./raddle_tab_nav.js');
var N = global.RaddleTabNav;

function attrGetter(attrs) {
  return function (name) {
    if (!attrs || attrs[name] == null) return null;
    return String(attrs[name]);
  };
}

function makeLadder(specs) {
  var rows = [];
  var realInputs = [];
  var pinInputs = [];

  function rowForIndex(idx) {
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute('data-word-index') === String(idx)) return rows[i];
    }
    return null;
  }

  var task = {
    querySelector: function (sel) {
      if (sel === '.new-raddle-ladder') return ladder;
      var m = /^\.new-raddle-ladder \.new-raddle-row\[data-word-index="([^"]+)"\]$/.exec(sel);
      if (m) return rowForIndex(m[1]);
      return null;
    },
    querySelectorAll: function () { return []; },
  };

  var table = {
    querySelectorAll: function (sel) {
      if (sel === 'input.new-raddle-input') return realInputs.slice();
      return [];
    },
  };

  var ladder = {
    querySelector: function (sel) {
      if (sel === '.new-raddle-table') return table;
      return null;
    },
    querySelectorAll: function (sel) {
      if (sel === 'input.new-raddle-input') return realInputs.slice();
      return [];
    },
  };

  specs.forEach(function (spec) {
    var row = {
      getAttribute: attrGetter({ 'data-word-index': String(spec.index) }),
    };
    var input = {
      disabled: !!spec.disabled,
      getAttribute: attrGetter(spec.pin ? { 'data-raddle-pin-proxy': '1' } : {}),
      closest: function (sel) {
        if (sel === 'input.new-raddle-input') return input;
        if (sel === '.new-raddle-task') return task;
        if (sel === '.new-raddle-row' || sel === '.new-raddle-row[data-word-index]') return row;
        return null;
      },
    };
    row.querySelector = function (sel) {
      if (sel === 'input.new-raddle-input') return spec.pin ? null : input;
      return null;
    };
    rows.push(row);
    if (spec.pin) pinInputs.push(input);
    else realInputs.push(input);
  });

  return {
    task: task,
    inputs: realInputs,
    pins: pinInputs,
  };
}

function testIsTabNavigationEvent() {
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab' }), true);
  assert.strictEqual(N.isTabNavigationEvent({ keyCode: 9 }), true);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab', shiftKey: true }), true);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Enter' }), false);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab', ctrlKey: true }), false);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab', altKey: true }), false);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab', metaKey: true }), false);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab', isComposing: true }), false);
  assert.strictEqual(N.isTabNavigationEvent({ key: 'Tab', defaultPrevented: true }), false);
}

function testListSkipsPinAndDisabled() {
  var ladder = makeLadder([
    { index: 1 },
    { index: 2, disabled: true },
    { index: 3 },
    { index: 3, pin: true },
  ]);
  var listed = N.listLadderInputs(ladder.task);
  assert.strictEqual(listed.length, 2);
  assert.strictEqual(listed[0], ladder.inputs[0]);
  assert.strictEqual(listed[1], ladder.inputs[2]);
}

function testTabMovesToNextAndPrev() {
  var ladder = makeLadder([{ index: 1 }, { index: 2 }, { index: 3 }]);
  var a = ladder.inputs[0];
  var b = ladder.inputs[1];
  var c = ladder.inputs[2];
  assert.strictEqual(N.destination(a, false), b);
  assert.strictEqual(N.destination(b, false), c);
  assert.strictEqual(N.destination(c, false), null);
  assert.strictEqual(N.destination(c, true), b);
  assert.strictEqual(N.destination(a, true), null);
}

function testTabFromPinProxyUsesRealRow() {
  var ladder = makeLadder([
    { index: 1 },
    { index: 2 },
    { index: 3 },
    { index: 2, pin: true },
  ]);
  var pin = ladder.pins[0];
  assert.strictEqual(N.resolveLadderInput(pin), ladder.inputs[1]);
  assert.strictEqual(N.destination(pin, false), ladder.inputs[2]);
  assert.strictEqual(N.destination(pin, true), ladder.inputs[0]);
}

function testEventHelper() {
  var ladder = makeLadder([{ index: 1 }, { index: 2 }]);
  assert.strictEqual(N.tabDestinationFromEvent({
    key: 'Tab',
    shiftKey: false,
    target: ladder.inputs[0],
  }), ladder.inputs[1]);
  assert.strictEqual(N.tabDestinationFromEvent({
    key: 'Tab',
    shiftKey: true,
    target: ladder.inputs[0],
  }), null);
  assert.strictEqual(N.tabDestinationFromEvent({
    key: 'Tab',
    target: { closest: function () { return null; } },
  }), null);
}

testIsTabNavigationEvent();
testListSkipsPinAndDisabled();
testTabMovesToNextAndPrev();
testTabFromPinProxyUsesRealRow();
testEventHelper();
console.log('raddle_tab_nav.test.js: ok');
