'use strict';

// node static/js/answer_mask.test.js
var assert = require('assert');
require('./imask.min.js');
require('./raddle_masked_input.js');
require('./answer_mask.js');
require('./cryptic_preview_check.js');

var A = global.AnswerMask;

function fakeForm(taskId) {
  var form = {
    dataset: {},
    submitted: 0,
    attrs: { 'data-task-id': taskId, 'data-answer-script': 'cyrillic' },
    getAttribute: function (name) { return this.attrs[name] || null; },
    requestSubmit: function () { this.submitted += 1; },
    querySelector: function () { return this.input; },
  };
  form.input = { form: form, value: '', readOnly: false, disabled: false };
  return form;
}

function testAutoCheckSendsAFullMaskOnce() {
  var form = fakeForm('101');
  var input = form.input;
  input.value = 'ИЗ-ЗА';
  assert.strictEqual(A.autoCheck(input, 'ИЗЗА'), true);
  // The page sends it: remembered before the request goes out.
  A.rememberChecked(form);
  assert.strictEqual(form.submitted, 1);
  // The same letters again (a restored wrong answer, retyping the last letter).
  assert.strictEqual(A.autoCheck(input, 'ИЗЗА'), false);
  assert.strictEqual(form.submitted, 1);
  // A changed answer goes.
  input.value = 'ИЗ-ЗУ';
  assert.strictEqual(A.autoCheck(input, 'ИЗЗУ'), true);
  assert.strictEqual(form.submitted, 2);
}

function testAutoCheckWaitsForPendingOrLockedFields() {
  var form = fakeForm('102');
  form.dataset.submitting = '1';
  assert.strictEqual(A.autoCheck(form.input, 'КЛОП'), false);
  form.dataset.submitting = '0';
  form.input.readOnly = true;  // answered offline, waiting for the network
  assert.strictEqual(A.autoCheck(form.input, 'КЛОП'), false);
  form.input.readOnly = false;
  assert.strictEqual(A.autoCheck(form.input, 'КЛОП'), true);
  assert.strictEqual(form.submitted, 1);
}

function testAutoCheckKeepsFormsApart() {
  var first = fakeForm('103');
  var second = fakeForm('104');
  first.input.value = 'КЛОП';
  A.autoCheck(first.input, 'КЛОП');
  A.rememberChecked(first);
  assert.strictEqual(A.autoCheck(second.input, 'КЛОП'), true);
}

function fakeCheck() {
  return { className: '', innerHTML: '' };
}

function testPreviewCheck() {
  var R = global.CrypticPreviewCheck.render;
  var check = fakeCheck();
  R(check, { empty: true, answer_error: null }, 'пусто');
  assert.strictEqual(check.className, 'cx-check');
  assert.strictEqual(check.innerHTML, 'пусто');

  R(check, { errors: [], warnings: ['w<1>'], answer: 'КЛОП' }, '');
  assert.strictEqual(check.className, 'cx-check cx-check--ok');
  assert.ok(check.innerHTML.indexOf('Формула даёт КЛОП') !== -1);
  assert.ok(check.innerHTML.indexOf('w&lt;1&gt;') !== -1);

  R(check, { errors: ['<b>'], warnings: [] }, '');
  assert.strictEqual(check.className, 'cx-check cx-check--err');
  assert.ok(check.innerHTML.indexOf('&lt;b&gt;') !== -1);

  // A mask that does not fit the answer is an error even with a good breakdown.
  R(check, { errors: [], warnings: [], answer: 'КЛОП', answer_error: 'В маске (5) букв 5, а в ответе 4' }, '');
  assert.strictEqual(check.className, 'cx-check cx-check--err');
  assert.ok(check.innerHTML.indexOf('В маске (5)') !== -1);
  assert.ok(check.innerHTML.indexOf('Формула даёт КЛОП') !== -1);
  R(check, { empty: true, answer_error: 'В ответе могут быть только русские буквы' }, 'пусто');
  assert.strictEqual(check.className, 'cx-check cx-check--err');
}

function testStackedWhenCellsWouldShrinkBelow15px() {
  // Phone row 294px: «Проверить» + 💡 + gaps take 165px.
  assert.strictEqual(A.stacked(294, 165, 10), true);
  assert.strictEqual(A.stacked(294, 120, 10), false);  // only «Проверить»
  assert.strictEqual(A.stacked(294, 165, 5), false);
  assert.strictEqual(A.stacked(0, 165, 5), null);  // not laid out yet
}

testStackedWhenCellsWouldShrinkBelow15px();
testAutoCheckSendsAFullMaskOnce();
testAutoCheckWaitsForPendingOrLockedFields();
testAutoCheckKeepsFormsApart();
testPreviewCheck();

console.log('answer_mask.test.js: ok');
