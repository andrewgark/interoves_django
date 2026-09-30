'use strict';

var assert = require('assert');
require('./word_salad_grid_editor.js');
var Editor = global.WordSaladGridEditor;

function FakeElement(tagName) {
  this.tagName = tagName;
  this.children = [];
  this.listeners = {};
  this.dataset = {};
  this.classList = {
    add: function () {},
    toggle: function () {}
  };
}
FakeElement.prototype.appendChild = function (child) {
  this.children.push(child);
  return child;
};
FakeElement.prototype.addEventListener = function (name, handler) {
  this.listeners[name] = handler;
};
FakeElement.prototype.removeEventListener = function () {};
FakeElement.prototype.setAttribute = function () {};
FakeElement.prototype.focus = function () {};
FakeElement.prototype.select = function () {};
Object.defineProperty(FakeElement.prototype, 'innerHTML', {
  get: function () { return ''; },
  set: function () { this.children = []; }
});

global.document = {
  createElement: function (tagName) { return new FakeElement(tagName); }
};

var editorHost = new FakeElement('div');
var editorApi = Editor.mount(editorHost, { grid: 'A B C D\nE F G H\nI J K L\nM N O P' });
var editorControls = editorHost.children[1].children;
assert.strictEqual(editorControls.length, 4);
editorControls[0].listeners.click();
assert.strictEqual(editorApi.getGridText(), 'M I E A\nN J F B\nO K G C\nP L H D');
editorApi.setDisabled(true);
assert.ok(editorControls.every(function (button) { return button.disabled; }));
editorApi.destroy();

assert.strictEqual(Editor.normalizeWord('ёлка'), 'ЕЛКА');
assert.deepStrictEqual(
  Editor.parseGrid('B C D E\nI H G F\nJ K L M\nQ P O N'),
  ['B', 'C', 'D', 'E', 'I', 'H', 'G', 'F', 'J', 'K', 'L', 'M', 'Q', 'P', 'O', 'N']
);
assert.strictEqual(
  Editor.formatGridText(['B', 'C', 'D', 'E', 'I', 'H', 'G', 'F', 'J', 'K', 'L', 'M', 'Q', 'P', 'O', 'N']),
  'B C D E\nI H G F\nJ K L M\nQ P O N'
);
assert.strictEqual(
  Editor.formatGridText(Editor.transformGrid('A B C D\nE F G H\nI J K L\nM N O P', 'rotate-clockwise')),
  'M I E A\nN J F B\nO K G C\nP L H D'
);
assert.strictEqual(
  Editor.formatGridText(Editor.transformGrid('A B C D\nE F G H\nI J K L\nM N O P', 'rotate-counterclockwise')),
  'D H L P\nC G K O\nB F J N\nA E I M'
);
assert.strictEqual(
  Editor.formatGridText(Editor.transformGrid('A B C D\nE F G H\nI J K L\nM N O P', 'flip-horizontal')),
  'D C B A\nH G F E\nL K J I\nP O N M'
);
assert.strictEqual(
  Editor.formatGridText(Editor.transformGrid('A B C D\nE F G H\nI J K L\nM N O P', 'flip-vertical')),
  'M N O P\nI J K L\nE F G H\nA B C D'
);

var valid = Editor.validateLive(
  'B C D E\nI H G F\nJ K L M\nQ P O N',
  'BCDEFGHIJKLMNOPQ'
);
assert.strictEqual(valid.ok, true, valid.errors.join('; '));
assert.deepStrictEqual(valid.missingWords, []);
assert.deepStrictEqual(valid.removableCells, []);

var missing = Editor.validateLive(
  'A B C D\nH G F E\nI J K L\nP O N M',
  'XYZ'
);
assert.strictEqual(missing.ok, false);
assert.ok(missing.missingWords.indexOf('XYZ') >= 0);

var removable = Editor.validateLive(
  'A B C D\nH G F E\nI J K L\nP O N M',
  'ABCD'
);
assert.strictEqual(removable.ok, false);
assert.ok(removable.removableCells.length > 0);
assert.ok(removable.errors[0].indexOf('можно убрать') >= 0);

var overlap = Editor.validateLive(
  'A B C D\nH G F E\nI J K L\nP O N M',
  'ABCDEFGHIJKLMNOP',
  'ABCDEFGHIJKLMNOP'
);
assert.strictEqual(overlap.ok, false);
assert.ok(overlap.errors[0].indexOf('совпадать') >= 0);

var rareOk = Editor.validateLive(
  'A B C D\nH G F E\nI J K L\nP O N M',
  'ABCDEFGHIJKLMNOP',
  'ABCD'
);
assert.strictEqual(rareOk.ok, true, rareOk.errors.join('; '));

assert.ok(Editor.findPaths(
  Editor.parseGrid('B C D E\nI H G F\nJ K L M\nQ P O N'),
  'BCDE',
  null,
  1
).length >= 1);

console.log('word_salad_grid_editor.test.js ok');
