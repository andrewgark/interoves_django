/**
 * Player hint panel on new-UI task cards: open/close, restore after HTML replace,
 * scroll the revealed hint into view.
 * Local check: node static/js/new_task_hints.test.js
 */
(function (global) {
  'use strict';

  function rootDocument() {
    return global.document;
  }

  function attrSelector(name, value) {
    return '[' + name + '="' + String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"]';
  }

  function hintsBody(taskId) {
    var doc = rootDocument();
    if (!doc || !doc.querySelector) return null;
    return doc.querySelector(attrSelector('data-hints-body', taskId));
  }

  function hintsToggle(taskId) {
    var doc = rootDocument();
    if (!doc || !doc.querySelector) return null;
    return doc.querySelector('[data-hints-toggle]' + attrSelector('data-task-id', taskId));
  }

  function hintsRoot(taskId) {
    var doc = rootDocument();
    if (!doc || !doc.getElementById) return null;
    return doc.getElementById('new-task-hints-' + taskId);
  }

  function hintCard(taskId, hintNumber) {
    if (hintNumber == null || hintNumber === '') return null;
    var root = hintsRoot(taskId);
    if (!root || !root.querySelector) return null;
    return root.querySelector('.new-hint' + attrSelector('data-hint-number', hintNumber));
  }

  function setButtonLabel(btn, open) {
    if (!btn) return;
    btn.textContent = open ? 'скрыть подсказки' : 'подсказки';
  }

  function isOpen(taskId) {
    var body = hintsBody(taskId);
    return !!(body && !body.hidden);
  }

  function captureOpen(taskIds) {
    var open = {};
    var ids = taskIds;
    if (!ids || !ids.length) {
      var doc = rootDocument();
      if (!doc || !doc.querySelectorAll) return open;
      doc.querySelectorAll('[data-hints-body]').forEach(function (body) {
        var tid = body.getAttribute('data-hints-body');
        if (tid && !body.hidden) open[tid] = true;
      });
      return open;
    }
    ids.forEach(function (tid) {
      if (isOpen(tid)) open[String(tid)] = true;
    });
    return open;
  }

  function restoreOpen(openMap) {
    if (!openMap) return;
    Object.keys(openMap).forEach(function (tid) {
      if (openMap[tid]) setOpen(tid, true);
    });
  }

  function scrollTo(taskId, hintNumber) {
    var target = hintCard(taskId, hintNumber) || hintsRoot(taskId) || hintsBody(taskId);
    if (!target || typeof target.scrollIntoView !== 'function') return;
    target.scrollIntoView({
      block: hintNumber ? 'nearest' : 'start',
      behavior: 'auto',
    });
  }

  function afterPreservedScroll(fn) {
    if (typeof fn !== 'function') return;
    if (typeof global.requestAnimationFrame !== 'function') {
      fn();
      return;
    }
    global.requestAnimationFrame(function () {
      global.requestAnimationFrame(function () {
        global.requestAnimationFrame(fn);
      });
    });
  }

  function markJustRevealed(taskId, hintNumber) {
    var card = hintCard(taskId, hintNumber);
    if (!card || !card.classList) return;
    card.classList.add('is-just-revealed');
    if (typeof global.setTimeout !== 'function') return;
    global.setTimeout(function () {
      if (card.classList) card.classList.remove('is-just-revealed');
    }, 2400);
  }

  function setOpen(taskId, open, opts) {
    opts = opts || {};
    var body = hintsBody(taskId);
    if (!body) return false;
    body.hidden = !open;
    setButtonLabel(hintsToggle(taskId), open);
    if (open && opts.hintNumber) markJustRevealed(taskId, opts.hintNumber);
    if (open && opts.scroll) scrollTo(taskId, opts.hintNumber);
    return true;
  }

  function reveal(taskId, hintNumber) {
    setOpen(taskId, true, { hintNumber: hintNumber });
    afterPreservedScroll(function () {
      setOpen(taskId, true, { scroll: true, hintNumber: hintNumber });
    });
  }

  global.InterovesTaskHints = {
    isOpen: isOpen,
    captureOpen: captureOpen,
    restoreOpen: restoreOpen,
    setOpen: setOpen,
    scrollTo: scrollTo,
    afterPreservedScroll: afterPreservedScroll,
    reveal: reveal,
  };
})(typeof window !== 'undefined' ? window : global);
