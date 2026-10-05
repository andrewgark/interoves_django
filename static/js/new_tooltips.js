'use strict';

/* Viewport-aware tooltips for controls whose hint should work on touch too. */
(function () {
  var activeElement = null;
  var tooltip = null;
  var hideTimer = null;
  var addedDescription = false;

  function ensureTooltip() {
    if (tooltip) return tooltip;
    tooltip = document.createElement('div');
    tooltip.className = 'new-floating-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.hidden = true;
    document.body.appendChild(tooltip);
    return tooltip;
  }

  function getTarget(event) {
    var target = event.target;
    return target && target.closest ? target.closest('[data-tooltip]') : null;
  }

  function positionTooltip(element) {
    var tip = ensureTooltip();
    var rect = element.getBoundingClientRect();
    var gap = 8;
    var margin = 8;
    var tipWidth = tip.offsetWidth;
    var tipHeight = tip.offsetHeight;
    var left = rect.left + (rect.width - tipWidth) / 2;
    var top = rect.bottom + gap;

    if (top + tipHeight > window.innerHeight - margin && rect.top - gap - tipHeight >= margin) {
      top = rect.top - gap - tipHeight;
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - tipWidth - margin));
    tip.style.left = Math.round(left) + 'px';
    tip.style.top = Math.round(top) + 'px';
  }

  function show(element) {
    var text = (element.getAttribute('data-tooltip') || '').trim();
    if (!text) return;
    clearTimeout(hideTimer);
    activeElement = element;
    var tip = ensureTooltip();
    tip.textContent = text;
    tip.hidden = false;
    tip.classList.add('is-visible');
    positionTooltip(element);
    if (document.activeElement === element && !element.hasAttribute('aria-describedby')) {
      element.setAttribute('aria-describedby', 'new-floating-tooltip');
      tip.id = 'new-floating-tooltip';
      addedDescription = true;
    }
  }

  function hide(element) {
    if (element && activeElement !== element) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      if (!tooltip) return;
      tooltip.classList.remove('is-visible');
      tooltip.hidden = true;
      if (activeElement && addedDescription && activeElement.getAttribute('aria-describedby') === 'new-floating-tooltip') {
        activeElement.removeAttribute('aria-describedby');
      }
      addedDescription = false;
      activeElement = null;
    }, 80);
  }

  document.addEventListener('pointerover', function (event) {
    var element = getTarget(event);
    if (element) show(element);
  });

  document.addEventListener('pointerout', function (event) {
    var element = getTarget(event);
    if (element && (!event.relatedTarget || !element.contains(event.relatedTarget))) hide(element);
  });

  document.addEventListener('focusin', function (event) {
    var element = getTarget(event);
    if (element) show(element);
  });

  document.addEventListener('focusout', function (event) {
    var element = getTarget(event);
    if (element && (!event.relatedTarget || !element.contains(event.relatedTarget))) hide(element);
  });

  document.addEventListener('pointerdown', function (event) {
    if (event.pointerType === 'touch') {
      var element = getTarget(event);
      if (element) {
        show(element);
        hideTimer = setTimeout(function () { hide(element); }, 2200);
      }
    }
  });

  window.addEventListener('resize', function () {
    if (activeElement) positionTooltip(activeElement);
  });
  window.addEventListener('scroll', function () { hide(); }, true);
})();
