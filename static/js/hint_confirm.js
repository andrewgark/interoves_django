/**
 * Confirmation shared by all player hint actions.
 * Russian point forms mirror games.templatetags.filters.ru_points_word.
 * Local check: node static/js/hint_confirm.test.js
 */
(function (global) {
  'use strict';

  function normalizeNumberString(value) {
    var text = String(value == null ? '' : value).trim().replace(',', '.');
    if (!text) return '0';
    if (text.indexOf('.') !== -1) {
      text = text.replace(/0+$/g, '').replace(/\.$/g, '');
    }
    return text || '0';
  }

  function pointsWord(value) {
    var text = normalizeNumberString(value);
    var number = Number(text);
    if (!isFinite(number)) return 'баллов';
    if (!Number.isInteger(number)) return 'балла';
    var absolute = Math.abs(number);
    var mod100 = absolute % 100;
    if (mod100 >= 11 && mod100 <= 14) return 'баллов';
    var mod10 = absolute % 10;
    if (mod10 === 1) return 'балл';
    if (mod10 >= 2 && mod10 <= 4) return 'балла';
    return 'баллов';
  }

  function message(value, explicitWord) {
    var amount = normalizeNumberString(value);
    var word = explicitWord || pointsWord(amount);
    return 'Снимется ' + amount + ' ' + word +
      '. Баллы за задание не опустятся ниже нуля.';
  }

  function create(options) {
    options = options || {};
    var doc = options.document || global.document;
    var modal = doc && doc.getElementById(options.modalId || 'new-hint-confirm-modal');
    var text = doc && doc.getElementById(options.textId || 'new-hint-confirm-text');
    var pending = null;
    var trigger = null;
    var previousOverflow = '';

    function close(restoreFocus) {
      if (!modal) return;
      modal.setAttribute('hidden', '');
      modal.classList.remove('is-open');
      if (doc.body) doc.body.style.overflow = previousOverflow;
      pending = null;
      if (restoreFocus !== false && trigger && typeof trigger.focus === 'function') {
        try { trigger.focus({ preventScroll: true }); }
        catch (err) { try { trigger.focus(); } catch (ignored) {} }
      }
      trigger = null;
    }

    function confirmPending() {
      var callback = pending;
      close(false);
      if (typeof callback === 'function') callback();
    }

    if (modal) {
      modal.querySelectorAll('[data-hint-confirm-close]').forEach(function (el) {
        el.addEventListener('click', function () { close(true); });
      });
      var yes = modal.querySelector('[data-hint-confirm-yes]');
      if (yes) yes.addEventListener('click', confirmPending);
      modal.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        close(true);
      });
    }

    return {
      open: function (params) {
        params = params || {};
        var copy = message(params.penalty, params.pointsWord);
        if (!modal || !text) {
          if (typeof global.confirm !== 'function' || global.confirm(copy)) {
            if (typeof params.onConfirm === 'function') params.onConfirm();
          }
          return;
        }
        pending = params.onConfirm;
        trigger = params.trigger || null;
        text.textContent = copy;
        previousOverflow = doc.body ? doc.body.style.overflow : '';
        modal.removeAttribute('hidden');
        modal.classList.add('is-open');
        if (doc.body) doc.body.style.overflow = 'hidden';
        var yes = modal.querySelector('[data-hint-confirm-yes]');
        if (yes) yes.focus();
      },
      close: close,
    };
  }

  global.InterovesHintConfirm = {
    normalizeNumberString: normalizeNumberString,
    pointsWord: pointsWord,
    message: message,
    create: create,
  };
})(typeof window !== 'undefined' ? window : globalThis);
