'use strict';

var assert = require('assert');
require('./word_salad_grid_editor.js');
var Editor = global.WordSaladGridEditor;

assert.strictEqual(Editor.normalizeWord('ёлка'), 'ЕЛКА');
assert.deepStrictEqual(
  Editor.parseGrid('B C D E\nI H G F\nJ K L M\nQ P O N'),
  ['B', 'C', 'D', 'E', 'I', 'H', 'G', 'F', 'J', 'K', 'L', 'M', 'Q', 'P', 'O', 'N']
);
assert.strictEqual(
  Editor.formatGridText(['B', 'C', 'D', 'E', 'I', 'H', 'G', 'F', 'J', 'K', 'L', 'M', 'Q', 'P', 'O', 'N']),
  'B C D E\nI H G F\nJ K L M\nQ P O N'
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
