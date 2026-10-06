(function (global) {
  'use strict';

  function track(name, root, step) {
    try {
      if (global.interovesAnalytics && typeof global.interovesAnalytics.trackYandexGoal === 'function') {
        global.interovesAnalytics.trackYandexGoal(name, {game: 'ladder', tutorial_version: root.dataset.tutorialVersion, step: step});
      }
    } catch (_) {}
  }

  global.InterovesTutorial = {
    createShell: function (root, options) {
      var shell = root.querySelector('[data-tutorial-shell]');
      var spotlights = shell.querySelector('[data-tutorial-spotlights]');
      var title = shell.querySelector('[data-tutorial-title]');
      var text = shell.querySelector('[data-tutorial-text]');
      var progress = shell.querySelector('[data-tutorial-progress]');
      var next = shell.querySelector('[data-tutorial-next]');
      var back = shell.querySelector('[data-tutorial-back]');
      var feedback = shell.querySelector('[data-tutorial-feedback-global]');
      var actions = shell.querySelector('.new-rules-modal__actions');
      var finishActions = shell.querySelector('.tutorial-shell__finish');
      var continueButton = shell.querySelector('[data-tutorial-continue]');
      var index = 0;
      var closed = false;
      var activeTargets = [];
      var rafPending = false;

      function targets(list) {
        return (list || []).map(function (selector) {
          return typeof selector === 'function' ? selector() : root.querySelector(selector);
        }).filter(Boolean);
      }
      function clearSpotlight() {
        spotlights.innerHTML = '';
      }
      function visibleRect(node, padding) {
        var rect = node.getBoundingClientRect();
        var left = Math.max(0, rect.left - padding);
        var top = Math.max(0, rect.top - padding);
        var right = Math.min(global.innerWidth, rect.right + padding);
        var bottom = Math.min(global.innerHeight, rect.bottom + padding);
        if (right <= left || bottom <= top) return null;
        return {left: left, top: top, right: right, bottom: bottom, width: right - left, height: bottom - top};
      }
      function renderSpotlight(nodes) {
        clearSpotlight();
        if (!nodes.length) return;
        var width = global.innerWidth;
        var height = global.innerHeight;
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.setAttribute('aria-hidden', 'true');
        var defs = document.createElementNS(svg.namespaceURI, 'defs');
        var mask = document.createElementNS(svg.namespaceURI, 'mask');
        var maskId = 'tutorial-spotlight-mask';
        mask.setAttribute('id', maskId);
        mask.setAttribute('maskUnits', 'userSpaceOnUse');
        var full = document.createElementNS(svg.namespaceURI, 'rect');
        full.setAttribute('width', width); full.setAttribute('height', height); full.setAttribute('fill', 'white');
        mask.appendChild(full);
        nodes.forEach(function (node) {
          var rect = visibleRect(node, 6);
          if (!rect) return;
          var hole = document.createElementNS(svg.namespaceURI, 'rect');
          hole.setAttribute('x', rect.left);
          hole.setAttribute('y', rect.top);
          hole.setAttribute('width', rect.width);
          hole.setAttribute('height', rect.height);
          hole.setAttribute('rx', '8'); hole.setAttribute('fill', 'black');
          mask.appendChild(hole);
        });
        defs.appendChild(mask); svg.appendChild(defs);
        var shade = document.createElementNS(svg.namespaceURI, 'rect');
        shade.setAttribute('width', width); shade.setAttribute('height', height);
        shade.setAttribute('fill', 'rgb(11 15 25 / 0.66)'); shade.setAttribute('mask', 'url(#' + maskId + ')');
        svg.appendChild(shade);
        nodes.forEach(function (node) {
          var rect = visibleRect(node, 6);
          if (!rect) return;
          var outline = document.createElementNS(svg.namespaceURI, 'rect');
          outline.setAttribute('x', rect.left);
          outline.setAttribute('y', rect.top);
          outline.setAttribute('width', rect.width);
          outline.setAttribute('height', rect.height);
          outline.setAttribute('rx', '8'); outline.setAttribute('fill', 'none');
          outline.setAttribute('stroke', 'var(--warning)'); outline.setAttribute('stroke-width', '3');
          svg.appendChild(outline);
        });
        spotlights.appendChild(svg);
        positionCallout(nodes);
      }
      function positionCallout(nodes) {
        if (shell.hidden || !nodes.length) return;
        var box = shell.querySelector('.tutorial-shell__callout');
        var bounds = nodes.map(function (node) { return visibleRect(node, 6); }).filter(Boolean);
        if (!bounds.length) return;
        var leftEdge = Math.min.apply(Math, bounds.map(function (r) { return r.left; }));
        var rightEdge = Math.max.apply(Math, bounds.map(function (r) { return r.right; }));
        var topEdge = Math.min.apply(Math, bounds.map(function (r) { return r.top; }));
        var bottomEdge = Math.max.apply(Math, bounds.map(function (r) { return r.bottom; }));
        var margin = 12;
        var boxRect = box.getBoundingClientRect();
        var boxWidth = boxRect.width || Math.min(512, global.innerWidth - 24);
        var boxHeight = boxRect.height || 220;
        var left;
        var top;
        if (global.innerWidth > 720 && global.innerWidth - rightEdge >= boxWidth + 24) {
          left = rightEdge + 16;
          top = Math.max(margin, Math.min(topEdge, global.innerHeight - boxHeight - margin));
        } else if (global.innerWidth > 720 && leftEdge >= boxWidth + 24) {
          left = leftEdge - boxWidth - 16;
          top = Math.max(margin, Math.min(topEdge, global.innerHeight - boxHeight - margin));
        } else {
          left = Math.max(margin, Math.min((leftEdge + rightEdge - boxWidth) / 2, global.innerWidth - boxWidth - margin));
          var below = global.innerHeight - bottomEdge - margin;
          var above = topEdge - margin;
          top = below >= Math.min(boxHeight, 210) || below >= above ? bottomEdge + 14 : topEdge - boxHeight - 14;
          top = Math.max(margin, Math.min(top, global.innerHeight - boxHeight - margin));
        }
        box.style.left = Math.round(left) + 'px';
        box.style.top = Math.round(top) + 'px';
        box.style.bottom = 'auto';
        box.style.transform = 'none';
      }
      function scheduleReposition() {
        if (rafPending || shell.hidden || !activeTargets.length) return;
        rafPending = true;
        global.requestAnimationFrame(function () {
          rafPending = false;
          renderSpotlight(activeTargets);
        });
      }
      function render() {
        var step = options.steps[index];
        progress.textContent = (index + 1) + ' / ' + options.steps.length;
        title.textContent = step.title || 'Лесенка';
        text.textContent = step.text || '';
        feedback.textContent = '';
        next.hidden = step.kind !== 'info';
        next.textContent = step.nextLabel || 'Дальше';
        back.hidden = index === 0 || step.kind !== 'info' || options.steps[index - 1].kind !== 'info';
        activeTargets = targets(step.targets);
        if (options.onStep) options.onStep(index);
        var focus = activeTargets[0];
        if (focus && focus.scrollIntoView) focus.scrollIntoView({block: 'nearest', behavior: 'auto'});
        global.requestAnimationFrame(function () { renderSpotlight(activeTargets); });
      }
      function open() {
        closed = false;
        shell.hidden = false;
        shell.classList.add('is-open');
        track('tutorial_start', root, index + 1);
        render();
      }
      function close(eventName) {
        closed = true;
        shell.hidden = true;
        shell.classList.remove('is-open');
        clearSpotlight();
        activeTargets = [];
        if (eventName) track(eventName, root, index + 1);
        if (options.onClose) options.onClose(eventName);
      }
      function go(delta) {
        var nextIndex = index + delta;
        if (nextIndex < 0 || nextIndex >= options.steps.length) return;
        track('tutorial_step_complete', root, index + 1);
        index = nextIndex;
        render();
      }
      function setStep(value) { index = value; render(); }
      function finish() {
        track('tutorial_step_complete', root, index + 1);
        track('tutorial_complete', root, options.steps.length);
        title.textContent = 'Готово!';
        text.textContent = 'Теперь ты знаешь, как играть.\nПродолжай связывать слова подсказками, пока верхняя и нижняя части Лесенки не встретятся.';
        progress.textContent = options.steps.length + ' / ' + options.steps.length;
        actions.hidden = true;
        finishActions.hidden = false;
        clearSpotlight();
        if (options.onFinish) options.onFinish();
      }
      next.addEventListener('click', function () { if (!closed) go(1); });
      back.addEventListener('click', function () { if (!closed) go(-1); });
      shell.querySelector('[data-tutorial-skip]').addEventListener('click', function () { close('tutorial_skip'); });
      continueButton.addEventListener('click', function () { close(); });
      shell.querySelectorAll('[data-tutorial-close]').forEach(function (node) { node.addEventListener('click', function () { close('tutorial_skip'); }); });
      document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && !shell.hidden) close('tutorial_skip'); });
      global.addEventListener('resize', scheduleReposition);
      global.addEventListener('scroll', scheduleReposition, true);
      global.addEventListener('load', scheduleReposition, {once: true});
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleReposition);
      track('tutorial_view', root, 1);
      open();
      return {
        setStep: setStep, next: function () { go(1); }, back: function () { go(-1); },
        finish: finish, close: close, feedback: function (value) { feedback.textContent = value || ''; },
        isOpen: function () { return !shell.hidden; },
      };
    },
  };
}(window));
