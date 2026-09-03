'use strict';

var assert = require('assert');

function loadActions(fakeRoot) {
  global.window = fakeRoot;
  Object.keys(require.cache).forEach(function (key) {
    if (key.indexOf('daily_share_actions.js') !== -1) delete require.cache[key];
  });
  return require('./daily_share_actions.js');
}

function blockFixture(textLines, payload) {
  var status = { textContent: '' };
  var share = {
    querySelectorAll: function () {
      return textLines.map(function (line) { return { textContent: line }; });
    },
    innerText: textLines.join('\n'),
    textContent: textLines.join('\n'),
  };
  return {
    getAttribute: function (name) {
      return name === 'data-share-card' ? JSON.stringify(payload || {}) : null;
    },
    querySelector: function (sel) {
      if (sel === '[data-raddle-share-text]') return share;
      if (sel === '[data-share-status]') return status;
      return null;
    },
    status: status,
  };
}

(function testCopyTextUsesClipboardAndExistingText() {
  var writes = [];
  var goals = [];
  var root = {
    navigator: {
      clipboard: {
        writeText: function (text) {
          writes.push(text);
          return Promise.resolve();
        },
      },
    },
    interovesAnalytics: {
      trackYandexGoalOnce: function (key, goal, params) {
        goals.push({ key: key, goal: goal, params: params });
        return true;
      },
    },
    DailyShareCard: {},
  };
  var actions = loadActions(root);
  var block = blockFixture(['🪜 Лесенка #1', '🟩🟩', '🔗 interoves.com/ladder/1'], {
    game_kind: 'ladder',
    number: '1',
    locale: 'ru',
  });
  var btn = { classList: { add: function () {}, remove: function () {} }, setAttribute: function () {}, getAttribute: function () { return 'Скопировать результат'; } };
  return actions.copyShareText(block, btn).then(function () {
    assert.strictEqual(writes[0], '🪜 Лесенка #1\n🟩🟩\n🔗 interoves.com/ladder/1');
    assert.strictEqual(block.status.textContent, 'Результат скопирован');
    assert.strictEqual(goals[0].goal, 'result_text_copy');
    assert.strictEqual(goals[0].params.game_kind, 'ladder');
  });
})();

(function testCopyImageUsesPngBlob() {
  var written = [];
  var blob = { type: 'image/png', size: 12 };
  function ClipboardItem(items) { this.items = items; }
  var root = {
    ClipboardItem: ClipboardItem,
    navigator: {
      clipboard: {
        write: function (items) {
          written.push(items);
          return Promise.resolve();
        },
      },
    },
    interovesAnalytics: { trackYandexGoalOnce: function () { return true; } },
    DailyShareCard: {
      renderShareCardPng: function () { return Promise.resolve(blob); },
    },
  };
  var actions = loadActions(root);
  var block = blockFixture(['x'], { game_kind: 'salad', number: '2', locale: 'ru', filename: 'salad-2.png' });
  var btn = { classList: { add: function () {}, remove: function () {} }, setAttribute: function () {}, getAttribute: function () { return ''; } };
  return actions.copyShareImage(block, btn).then(function () {
    assert.strictEqual(written.length, 1);
    assert.strictEqual(written[0][0].items['image/png'], blob);
    assert.strictEqual(block.status.textContent, 'Картинка скопирована');
  });
})();

(function testCopyImageUnsupportedIsNotACrash() {
  var root = {
    navigator: { clipboard: { writeText: function () { return Promise.resolve(); } } },
    interovesAnalytics: { trackYandexGoalOnce: function () { return true; } },
    DailyShareCard: { renderShareCardPng: function () { return Promise.reject(new Error('nope')); } },
  };
  var actions = loadActions(root);
  var block = blockFixture(['x'], { game_kind: 'ladder', number: '1' });
  return actions.copyShareImage(block, null).then(function () {
    assert.ok(block.status.textContent.indexOf('не умеет копировать') !== -1);
  });
})();

(function testShareUsesFilesWhenSupported() {
  var shared = [];
  var blob = { type: 'image/png' };
  function FakeFile(parts, name, opts) {
    this.parts = parts;
    this.name = name;
    this.type = opts.type;
  }
  var root = {
    File: FakeFile,
    navigator: {
      canShare: function (data) { return !!(data && data.files); },
      share: function (data) {
        shared.push(data);
        return Promise.resolve();
      },
    },
    interovesAnalytics: { trackYandexGoalOnce: function (key, goal) { shared.push(goal); return true; } },
    DailyShareCard: { renderShareCardPng: function () { return Promise.resolve(blob); } },
  };
  var actions = loadActions(root);
  var block = blockFixture(['text'], { game_kind: 'ladder', number: '3', filename: 'ladder-3.png', headline: 'Лесенка · 1:00' });
  return actions.shareNative(block, null).then(function () {
    var payload = shared.filter(function (item) { return item && item.files; })[0];
    assert.ok(payload);
    assert.strictEqual(payload.files[0].name, 'ladder-3.png');
    assert.strictEqual(payload.files[0].type, 'image/png');
    assert.ok(shared.indexOf('result_share_click') !== -1);
    assert.ok(shared.indexOf('result_share_success') !== -1);
  });
})();

(function testShareFallsBackWithoutNavigatorShare() {
  var writes = [];
  var root = {
    navigator: {
      clipboard: { writeText: function (text) { writes.push(text); return Promise.resolve(); } },
    },
    interovesAnalytics: { trackYandexGoalOnce: function () { return true; } },
    DailyShareCard: { renderShareCardPng: function () { return Promise.resolve({ type: 'image/png' }); } },
  };
  var actions = loadActions(root);
  var block = blockFixture(['copied-text'], { game_kind: 'alphabetty', number: '4' });
  var btn = { classList: { add: function () {}, remove: function () {} }, setAttribute: function () {}, getAttribute: function () { return 'Поделиться'; } };
  return actions.shareNative(block, btn).then(function () {
    assert.strictEqual(writes[0], 'copied-text');
    assert.ok(block.status.textContent.indexOf('скопирован') !== -1);
  });
})();

(function testShareFallsBackWhenFileShareUnsupported() {
  var shared = [];
  var root = {
    File: function (parts, name, opts) { this.name = name; this.type = opts.type; },
    navigator: {
      canShare: function () { return false; },
      share: function (data) { shared.push(data); return Promise.resolve(); },
    },
    interovesAnalytics: { trackYandexGoalOnce: function () { return true; } },
    DailyShareCard: { renderShareCardPng: function () { return Promise.resolve({ type: 'image/png' }); } },
  };
  var actions = loadActions(root);
  var block = blockFixture(['only-text'], { game_kind: 'salad', number: '5', headline: 'Салатик · 1:00' });
  return actions.shareNative(block, null).then(function () {
    assert.strictEqual(shared.length, 1);
    assert.ok(!shared[0].files);
    assert.strictEqual(shared[0].text, 'only-text');
  });
})();

(function testCancelIsNotError() {
  var goals = [];
  var err = new Error('closed');
  err.name = 'AbortError';
  var root = {
    File: function (parts, name, opts) { this.name = name; this.type = opts.type; },
    navigator: {
      canShare: function () { return true; },
      share: function () { return Promise.reject(err); },
    },
    interovesAnalytics: {
      trackYandexGoalOnce: function (key, goal) { goals.push(goal); return true; },
    },
    DailyShareCard: { renderShareCardPng: function () { return Promise.resolve({ type: 'image/png' }); } },
  };
  var actions = loadActions(root);
  var block = blockFixture(['x'], { game_kind: 'ladder', number: '6' });
  return actions.shareNative(block, null).then(function () {
    assert.ok(goals.indexOf('result_share_cancel') !== -1);
    assert.ok(goals.indexOf('result_share_error') === -1);
    assert.strictEqual(block.status.textContent, '');
  });
})();

(function testUnhandledRejectionDoesNotEscape() {
  var root = {
    navigator: {},
    interovesAnalytics: { trackYandexGoalOnce: function () { return true; } },
    DailyShareCard: { renderShareCardPng: function () { return Promise.reject(new Error('boom')); } },
  };
  var actions = loadActions(root);
  var block = blockFixture(['x'], { game_kind: 'ladder' });
  return actions.shareNative(block, null).then(function () {
    assert.ok(true);
  });
})();
