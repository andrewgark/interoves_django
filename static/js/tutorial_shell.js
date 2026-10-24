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
      var index = 0;
      var closed = false;
      var completed = false;
      var message = null;
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
        if (shell.hidden) return;
        if (!nodes.length) {
          centerCallout();
          return;
        }
        var box = shell.querySelector('.tutorial-shell__callout');
        var bounds = nodes.map(function (node) { return visibleRect(node, 6); }).filter(Boolean);
        if (!bounds.length) {
          centerCallout();
          return;
        }
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
        // 'over' lays the popup on top of its callout target (wide screens
        // only; narrow ones fall back to 'below').
        var side = step.calloutSide === 'over' && global.innerWidth <= 720 ? 'below' : step.calloutSide;
        if (side === 'over') {
          box.style.width = '';
          boxWidth = box.getBoundingClientRect().width || Math.min(432, global.innerWidth - 24);
          left = Math.max(margin, Math.min(leftEdge, global.innerWidth - boxWidth - margin));
          top = Math.max(margin, Math.min(topEdge, global.innerHeight - boxHeight - margin));
        } else if (side === 'below') {
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
        } else if (side === 'right-above-or-left-below' && global.innerWidth > 720) {
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
        } else if (side === 'right' && global.innerWidth > 720) {
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
      // Targets are looked up again on every pass: the page may replace the
      // highlighted nodes (a re-rendered card) while a step stays open.
      function resolveTargets() {
        var step = options.steps[index];
        if (completed) {
          activeTargets = [];
          calloutTargets = targets((message || options.finish || {}).calloutTarget);
          return;
        }
        activeTargets = targets(step.targets);
        calloutTargets = targets(step.calloutTarget || step.targets);
      }
      // A message popup sits in the middle of a fully dimmed page.
      function centerCallout() {
        var box = shell.querySelector('.tutorial-shell__callout');
        box.style.width = '';
        var rect = box.getBoundingClientRect();
        box.style.left = Math.round(Math.max(12, (global.innerWidth - rect.width) / 2)) + 'px';
        box.style.top = Math.round(Math.max(12, (global.innerHeight - rect.height) / 2)) + 'px';
        box.style.bottom = 'auto';
        box.style.transform = 'none';
      }
      function scheduleReposition() {
        if (shell.hidden) return;
        shell.classList.add('tutorial-shell--repositioning');
        if (rafPending) return;
        rafPending = true;
        global.requestAnimationFrame(function () {
          rafPending = false;
          if (shell.hidden) return;
          resolveTargets();
          if (completed && message) {
            renderSpotlight([]);
            centerCallout();
          } else if (completed) {
            positionCallout(calloutTargets);
          } else {
            renderSpotlight(activeTargets);
          }
          shell.classList.remove('tutorial-shell--repositioning');
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
        resolveTargets();
        if (options.onStep) options.onStep(index);
        root.dispatchEvent(new CustomEvent('tutorial:step', {detail: index}));
        var focus = activeTargets[0];
        if (focus && focus.scrollIntoView) focus.scrollIntoView({block: step.calloutSide === 'below' ? 'center' : 'nearest', behavior: 'auto'});
        scheduleReposition();
        if (options.autoFocusInteractive !== false && step.kind === 'action' && !activeTargets.some(function (node) { return node === document.activeElement || (node.contains && node.contains(document.activeElement)); })) {
          global.requestAnimationFrame(function () {
            var candidate = interactiveTargets().reduce(function (found, node) {
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
      function resume() {
        if (!closed) return;
        if (completed && !message) {
          closed = false;
          shell.hidden = false;
          shell.classList.add('is-open');
          var done = options.finish || {};
          title.textContent = done.title || 'Готово!';
          text.textContent = done.text || 'Теперь ты знаешь, как играть.';
          progress.textContent = options.steps.length + ' / ' + options.steps.length;
          next.hidden = false;
          next.textContent = done.label || 'Закрыть';
          if (back) back.hidden = true;
          clearSpotlight();
          scheduleReposition();
          next.focus();
          return;
        }
        // A dismissed onward prompt is not a tutorial step; resume the saved
        // step instead of reopening the stale link action.
        if (message) {
          message = null;
          completed = false;
        }
        open();
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
        var done = options.finish || {};
        title.textContent = done.title || 'Готово!';
        text.textContent = done.text || 'Теперь ты знаешь, как играть.';
        progress.textContent = options.steps.length + ' / ' + options.steps.length;
        next.hidden = false;
        next.textContent = done.label || 'Закрыть';
        if (back) back.hidden = true;
        clearSpotlight();
        if (options.onFinish) options.onFinish();
        scheduleReposition();
        next.focus();
      }
      // A popup after the steps are over, e.g. once the whole board is solved.
      // ``href`` turns its button into a link onward.
      function showMessage(opts) {
        message = opts || {};
        completed = true;
        closed = false;
        shell.hidden = false;
        shell.classList.add('is-open');
        progress.textContent = '';
        title.textContent = message.title || '';
        text.textContent = message.text || '';
        feedback.textContent = '';
        next.hidden = false;
        next.textContent = message.label || 'Закрыть';
        if (back) back.hidden = true;
        clearSpotlight();
        scheduleReposition();
        next.focus();
      }
      next.addEventListener('click', function () {
        if (closed) return;
        if (message && message.href) {
          if (message.onAction) message.onAction();
          global.location.href = message.href;
        }
        else if (completed) close('tutorial_finish');
        else go(1);
      });
      if (back) back.addEventListener('click', function () { if (!closed) go(-1); });
      shell.querySelectorAll('[data-tutorial-close]').forEach(function (node) { node.addEventListener('click', function () { close('tutorial_skip'); }); });
      function targetContains(target, element) {
        return target === element || !!(target.contains && target.contains(element));
      }
      // A page dialog opened by an allowed action (e.g. the hint confirmation
      // after 💡) must stay usable on top of the tutorial.
      function inPageDialog(element) {
        var dialog = element && element.closest && element.closest('.new-rules-modal:not(.tutorial-shell)');
        return !!(dialog && !dialog.hidden);
      }
      // ``interactive`` narrows what an action step lets the player touch;
      // the rest of its targets are only highlighted.
      function interactiveTargets() {
        var step = options.steps[index];
        if (completed || step.kind !== 'action') return [];
        resolveTargets();
        return step.interactive ? targets(step.interactive) : activeTargets;
      }
      function inActionTarget(element) {
        return interactiveTargets().some(function (target) { return targetContains(target, element); });
      }
      function eventAllowed(event) {
        if (shell.hidden || shell.contains(event.target)) return true;
        return inPageDialog(event.target) || inActionTarget(event.target);
      }
      function block(event) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      // Window capture runs before every page handler, including the ones the
      // play page registers on document in the capture phase (likes, Tab nav).
      ['click', 'dblclick', 'auxclick', 'contextmenu', 'pointerdown', 'mousedown', 'touchstart'].forEach(function (type) {
        global.addEventListener(type, function (event) {
          if (!eventAllowed(event)) block(event);
        }, {capture: true, passive: false});
      });
      // Focus never rests on a page control the step does not offer: text
      // from soft keyboards and IME arrives without keydown events.
      global.addEventListener('focusin', function (event) {
        if (shell.hidden || shell.contains(event.target) || inPageDialog(event.target)) return;
        if (inActionTarget(event.target)) return;
        if (event.target.blur) event.target.blur();
        if (!next.hidden) next.focus();
      }, true);
      // A page dialog (the hint confirmation) goes above the tutorial: the
      // popup and its dimming step aside until the dialog closes.
      function syncPageDialog() {
        var open = Array.prototype.some.call(
          document.querySelectorAll('.new-rules-modal:not(.tutorial-shell)'),
          function (dialog) { return !dialog.hidden; }
        );
        var yielding = shell.classList.contains('tutorial-shell--yield');
        if (open === yielding) return;
        shell.classList.toggle('tutorial-shell--yield', open);
        if (!open) scheduleReposition();
      }
      new MutationObserver(syncPageDialog).observe(document.body, {
        attributes: true, attributeFilter: ['hidden'], subtree: true,
      });
      function tabStops() {
        var selector = 'button:not([disabled]):not([hidden]):not([tabindex="-1"]), a[href]:not([tabindex="-1"]), input:not([readonly]):not([disabled]):not([tabindex="-1"]), [tabindex="0"]';
        var nodes = Array.prototype.slice.call(shell.querySelectorAll(selector));
        if (!completed && options.steps[index].kind === 'action') {
          interactiveTargets().forEach(function (target) {
            if (target.matches && target.matches(selector)) nodes.push(target);
            if (target.querySelectorAll) nodes = nodes.concat(Array.prototype.slice.call(target.querySelectorAll(selector)));
          });
        }
        return nodes.filter(function (node, position) { return nodes.indexOf(node) === position && node.getClientRects().length; });
      }
      global.addEventListener('keydown', function (event) {
        if (shell.hidden || inPageDialog(event.target)) return;
        if (event.key === 'Escape') {
          block(event);
          close('tutorial_skip');
          return;
        }
        if (event.key === 'Tab') {
          // Tab cycles only through the popup and the highlighted controls.
          block(event);
          var nodes = tabStops();
          if (!nodes.length) return;
          var position = nodes.indexOf(document.activeElement);
          var nextIndex = position < 0
            ? (event.shiftKey ? nodes.length - 1 : 0)
            : (position + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length;
          nodes[nextIndex].focus();
          return;
        }
        // Keys on the page itself (scrolling) stay; keys aimed at its
        // controls (typing, Enter, Space) only work on highlighted ones.
        // Printable keys are blocked even off the controls: the play page
        // routes stray typing into the current word.
        var control = event.target.closest && event.target.closest('input, textarea, select, button, a[href], [tabindex]');
        var typing = event.key && event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey;
        if ((control || typing) && !eventAllowed(event)) block(event);
      }, true);
      global.addEventListener('resize', scheduleReposition);
      global.addEventListener('scroll', scheduleReposition, true);
      global.addEventListener('load', scheduleReposition, {once: true});
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleReposition);
      track('tutorial_view', root, 1);
      open();
      return {
        setStep: setStep, next: function () { go(1); }, back: function () { go(-1); },
        resume: resume,
        refresh: scheduleReposition,
        showMessage: showMessage,
        finish: finish, close: close, feedback: function (value) { feedback.textContent = value || ''; },
        isOpen: function () { return !shell.hidden; },
      };
    },
  };
}(window));
