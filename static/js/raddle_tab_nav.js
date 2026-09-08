/**
 * Tab / Shift+Tab between raddle (лесенка) word inputs, like a single HTML form.
 * Skips 💡 / check / clue-mark controls and sticky-pin clones.
 *
 * Local check: node static/js/raddle_tab_nav.test.js
 */
(function (global) {
  'use strict';

  function isTabNavigationEvent(event) {
    if (!event) return false;
    if (event.defaultPrevented) return false;
    if (event.isComposing) return false;
    if (event.ctrlKey || event.altKey || event.metaKey) return false;
    return event.key === 'Tab' || event.keyCode === 9;
  }

  function isLadderWordInput(input) {
    if (!input || input.disabled) return false;
    if (typeof input.getAttribute !== 'function') return false;
    if (input.getAttribute('data-raddle-pin-proxy') === '1') return false;
    return true;
  }

  function listLadderInputs(taskRoot) {
    if (!taskRoot || typeof taskRoot.querySelectorAll !== 'function') return [];
    var scope = taskRoot.querySelector('.new-raddle-ladder') || taskRoot;
    var list = scope.querySelectorAll('input.new-raddle-input');
    var out = [];
    for (var i = 0; i < list.length; i++) {
      if (isLadderWordInput(list[i])) out.push(list[i]);
    }
    return out;
  }

  function resolveLadderInput(el) {
    if (!el || typeof el.closest !== 'function') return null;
    var input = el.closest('input.new-raddle-input');
    if (!input) return null;
    if (input.getAttribute('data-raddle-pin-proxy') !== '1') return input;
    var taskRoot = input.closest('.new-raddle-task');
    var row = input.closest('.new-raddle-row');
    var idx = row && typeof row.getAttribute === 'function'
      ? row.getAttribute('data-word-index')
      : null;
    if (!taskRoot || idx == null || idx === '') return null;
    var realRow = taskRoot.querySelector(
      '.new-raddle-ladder .new-raddle-row[data-word-index="' + idx + '"]'
    );
    if (!realRow) return null;
    return realRow.querySelector('input.new-raddle-input');
  }

  function destination(fromEl, shiftKey) {
    var input = fromEl && fromEl.closest
      ? fromEl.closest('input.new-raddle-input')
      : fromEl;
    if (!input || typeof input.closest !== 'function') return null;
    var taskRoot = input.closest('.new-raddle-task');
    var current = resolveLadderInput(input);
    if (!current || !taskRoot) return null;
    var inputs = listLadderInputs(taskRoot);
    var i = inputs.indexOf(current);
    if (i < 0) return null;
    var j = shiftKey ? i - 1 : i + 1;
    if (j < 0 || j >= inputs.length) return null;
    return inputs[j];
  }

  function tabDestinationFromEvent(event) {
    if (!isTabNavigationEvent(event)) return null;
    var from = event.target && event.target.closest
      ? event.target.closest('input.new-raddle-input')
      : null;
    if (!from) return null;
    return destination(from, !!event.shiftKey);
  }

  global.RaddleTabNav = {
    isTabNavigationEvent: isTabNavigationEvent,
    isLadderWordInput: isLadderWordInput,
    listLadderInputs: listLadderInputs,
    resolveLadderInput: resolveLadderInput,
    destination: destination,
    tabDestinationFromEvent: tabDestinationFromEvent,
  };
})(typeof window !== 'undefined' ? window : globalThis);
