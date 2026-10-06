(function (global) {
  'use strict';

  function combineTargets(nodes) {
    return {
      contains: function (element) {
        return nodes.some(function (node) { return node === element || (node.contains && node.contains(element)); });
      },
      getBoundingClientRect: function () {
        var rects = nodes.map(function (node) { return node.getBoundingClientRect(); });
        var left = Math.min.apply(Math, rects.map(function (rect) { return rect.left; }));
        var top = Math.min.apply(Math, rects.map(function (rect) { return rect.top; }));
        var right = Math.max.apply(Math, rects.map(function (rect) { return rect.right; }));
        var bottom = Math.max.apply(Math, rects.map(function (rect) { return rect.bottom; }));
        return {left: left, top: top, right: right, bottom: bottom, width: right - left, height: bottom - top};
      },
      scrollIntoView: function () {
        if (nodes[0] && nodes[0].scrollIntoView) nodes[0].scrollIntoView({block: 'nearest'});
      },
    };
  }

  function track(name, root, step) {
    try {
      if (global.interovesAnalytics && typeof global.interovesAnalytics.trackYandexGoal === 'function') {
        global.interovesAnalytics.trackYandexGoal(name, {
          game: root.dataset.tutorialGame || 'unknown',
          tutorial_version: root.dataset.tutorialVersion,
          step: step,
        });
      }
    } catch (_) {}
  }

  global.InterovesTutorial = {
    combineTargets: combineTargets,
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
      var index = 0;
      var closed = false;
      var completed = false;
      var activeTargets = [];
      var calloutTargets = [];
      var rafPending = false;
      var returnFocus = null;

      function persist(status) {
        try {
          var game = root.dataset.tutorialGame || 'unknown';
          var key = 'interoves_tutorial_' + game + '_v1';
          var saved = JSON.parse(global.localStorage.getItem(key) || '{}');
          var version = root.dataset.tutorialVersion || '';
          if (saved.version !== version) saved = {};
          saved.version = version;
          if (status === 'started') {
            delete saved.completedAt;
            delete saved.skippedAt;
          }
          saved.status = status;
          saved.step = index + 1;
          saved.updatedAt = new Date().toISOString();
          if (status === 'started') saved.startedAt = saved.updatedAt;
          if (status === 'completed') saved.completedAt = saved.updatedAt;
          if (status === 'skipped') saved.skippedAt = saved.updatedAt;
          global.localStorage.setItem(key, JSON.stringify(saved));
        } catch (_) {}
      }

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
        positionCallout(calloutTargets.length ? calloutTargets : nodes);
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
        var boxWidth = boxRect.width || Math.min(432, global.innerWidth - 24);
        var boxHeight = boxRect.height || 220;
        var left;
        var top;
        var step = options.steps[index];
        if (step.calloutSide === 'below') {
          box.style.width = '';
          boxWidth = box.getBoundingClientRect().width || Math.min(432, global.innerWidth - 24);
          left = Math.max(margin, Math.min((leftEdge + rightEdge - boxWidth) / 2, global.innerWidth - boxWidth - margin));
          var belowSpace = global.innerHeight - bottomEdge - margin;
          var aboveSpace = topEdge - margin;
          if (belowSpace >= boxHeight + 14 || belowSpace >= aboveSpace) {
            top = Math.min(bottomEdge + 14, global.innerHeight - boxHeight - margin);
          } else {
            top = Math.max(margin, topEdge - boxHeight - 14);
          }
        } else if (step.calloutSide === 'right-above-or-left-below' && global.innerWidth > 720) {
          var availableRight = global.innerWidth - rightEdge - 28;
          var aboveSpace = topEdge - margin;
          var desiredWidth = Math.min(432, availableRight);
          if (availableRight >= 230 && aboveSpace >= boxHeight + 12) {
            box.style.width = desiredWidth + 'px';
            boxWidth = box.getBoundingClientRect().width;
            left = rightEdge + 16;
            top = topEdge - boxHeight - 14;
          } else {
            box.style.width = '';
            boxWidth = box.getBoundingClientRect().width || Math.min(432, global.innerWidth - 24);
            left = Math.max(margin, Math.min(leftEdge, global.innerWidth - boxWidth - margin));
            top = Math.min(bottomEdge + 14, global.innerHeight - boxHeight - margin);
          }
        } else if (step.calloutSide === 'right' && global.innerWidth > 720) {
          var availableRight = global.innerWidth - rightEdge - 28;
          if (availableRight >= 230) {
            box.style.width = Math.min(432, availableRight) + 'px';
            boxWidth = box.getBoundingClientRect().width;
            left = rightEdge + 16;
            top = Math.max(margin, Math.min(topEdge, global.innerHeight - boxHeight - margin));
          } else {
            box.style.width = '';
          }
        } else {
          box.style.width = '';
        }
        boxWidth = box.getBoundingClientRect().width || Math.min(432, global.innerWidth - 24);
        if (left === undefined && global.innerWidth > 720 && global.innerWidth - rightEdge >= boxWidth + 24) {
          left = rightEdge + 16;
          top = Math.max(margin, Math.min(topEdge, global.innerHeight - boxHeight - margin));
        } else if (left === undefined && global.innerWidth > 720 && leftEdge >= boxWidth + 24) {
          left = leftEdge - boxWidth - 16;
          top = Math.max(margin, Math.min(topEdge, global.innerHeight - boxHeight - margin));
        } else if (left === undefined) {
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
        if (back) back.hidden = index === 0 || step.kind !== 'info' || options.steps[index - 1].kind !== 'info';
        activeTargets = targets(step.targets);
        calloutTargets = targets(step.calloutTarget || step.targets);
        if (options.onStep) options.onStep(index);
        root.dispatchEvent(new CustomEvent('tutorial:step', {detail: index}));
        var focus = activeTargets[0];
        if (focus && focus.scrollIntoView) focus.scrollIntoView({block: step.calloutSide === 'below' ? 'center' : 'nearest', behavior: 'auto'});
        global.requestAnimationFrame(function () { renderSpotlight(activeTargets); });
        if (step.kind === 'action' && !activeTargets.some(function (node) { return node === document.activeElement || (node.contains && node.contains(document.activeElement)); })) {
          global.requestAnimationFrame(function () {
            var candidate = activeTargets.reduce(function (found, node) {
              if (found) return found;
              if (node.matches && node.matches('[tabindex="0"], input:not([readonly]), button:not([disabled])')) return node;
              return node.querySelector ? node.querySelector('[tabindex="0"], input:not([readonly]), button:not([disabled])') : null;
            }, null);
            if (candidate) candidate.focus();
          });
        }
      }
      function open() {
        closed = false;
        returnFocus = document.activeElement;
        shell.hidden = false;
        shell.classList.add('is-open');
        persist('started');
        track('tutorial_start', root, index + 1);
        render();
        next.focus();
      }
      function close(eventName) {
        closed = true;
        shell.hidden = true;
        shell.classList.remove('is-open');
        clearSpotlight();
        activeTargets = [];
        if (eventName && !completed) track(eventName, root, index + 1);
        if (!completed) persist('skipped');
        if (options.onClose) options.onClose(eventName);
        if (returnFocus && returnFocus.focus && returnFocus.getClientRects().length) returnFocus.focus();
      }
      function go(delta) {
        var nextIndex = index + delta;
        if (nextIndex < 0 || nextIndex >= options.steps.length) return;
        if (delta > 0) track('tutorial_step_complete', root, index + 1);
        index = nextIndex;
        render();
      }
      function setStep(value) {
        if (value === index) return;
        track('tutorial_step_complete', root, index + 1);
        index = value;
        render();
      }
      function finish() {
        completed = true;
        persist('completed');
        track('tutorial_step_complete', root, index + 1);
        track('tutorial_complete', root, options.steps.length);
        title.textContent = 'Готово!';
        text.textContent = 'Теперь ты знаешь, как играть.\nПродолжай связывать слова подсказками, пока верхняя и нижняя части Лесенки не встретятся. Закрой окно крестиком, чтобы доиграть эту Лесенку.';
        progress.textContent = options.steps.length + ' / ' + options.steps.length;
        actions.hidden = true;
        clearSpotlight();
        if (options.onFinish) options.onFinish();
        global.requestAnimationFrame(function () { positionCallout(calloutTargets); });
        shell.querySelector('[data-tutorial-close]').focus();
      }
      next.addEventListener('click', function () { if (!closed) go(1); });
      if (back) back.addEventListener('click', function () { if (!closed) go(-1); });
      shell.querySelectorAll('[data-tutorial-close]').forEach(function (node) { node.addEventListener('click', function () { close('tutorial_skip'); }); });
      function targetContains(target, element) {
        return target === element || !!(target.contains && target.contains(element));
      }
      document.addEventListener('click', function (event) {
        if (shell.hidden || shell.contains(event.target)) return;
        var step = options.steps[index];
        var allowed = step.kind === 'action' && activeTargets.some(function (target) { return targetContains(target, event.target); });
        if (!allowed) {
          event.preventDefault();
          event.stopImmediatePropagation();
        }
      }, true);
      document.addEventListener('keydown', function (event) {
        if (event.key !== 'Tab' || shell.hidden) return;
        var nodes = Array.prototype.slice.call(shell.querySelectorAll('button:not([disabled]):not([hidden]), a[href], input:not([readonly]):not([disabled]), [tabindex="0"]'));
        if (options.steps[index].kind === 'action') {
          activeTargets.forEach(function (target) {
            if (target.matches && target.matches('button:not([disabled]), input:not([readonly]):not([disabled]), [tabindex="0"]')) nodes.push(target);
            if (target.querySelectorAll) nodes = nodes.concat(Array.prototype.slice.call(target.querySelectorAll('button:not([disabled]), input:not([readonly]):not([disabled]), [tabindex="0"]')));
          });
        }
        nodes = nodes.filter(function (node, position) { return nodes.indexOf(node) === position && node.getClientRects().length; });
        if (!nodes.length) { event.preventDefault(); return; }
        var first = nodes[0];
        var last = nodes[nodes.length - 1];
        if (event.shiftKey && (document.activeElement === first || !nodes.includes(document.activeElement))) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !nodes.includes(document.activeElement))) {
          event.preventDefault(); first.focus();
        }
      });
      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !shell.hidden) close('tutorial_skip');
      });
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
