(function (global) {
  'use strict';
  function createTutorialShell(options) {
    var root = options.root, shell = root.querySelector('[data-tutorial-shell]');
    var storageKey = 'interoves_tutorial_' + options.game + '_' + options.version;
    var spotlightLayer = shell.querySelector('[data-tutorial-spotlights]');
    var index = 0, started = false, completed = false;
    function emit(name, step) {
      try { if (global.interovesAnalytics && global.interovesAnalytics.trackYandexGoal) global.interovesAnalytics.trackYandexGoal(name, { game: options.game, tutorial_version: options.version, step: step }); } catch (e) {}
    }
    function target() { return options.target ? options.target(index) : null; }
    function render() {
      var step = options.steps[index];
      root.querySelector('[data-tutorial-progress]').textContent = (index + 1) + ' / ' + options.steps.length;
      root.querySelector('[data-tutorial-title]').textContent = completed ? 'Готово!' : (step.title || 'Лесенка');
      root.querySelector('[data-tutorial-text]').textContent = completed ? 'Чтобы решить Лесенку, продолжай связывать соседние слова подсказками, пока две части цепочки не встретятся.' : (step.text || '');
      root.querySelector('[data-tutorial-next]').hidden = !step.next;
      root.querySelector('[data-tutorial-back]').hidden = index === 0 || completed || (options.canGoBack && !options.canGoBack(index));
      shell.querySelector('.tutorial-shell__finish').hidden = !completed;
      shell.querySelector('.new-rules-modal__actions').hidden = completed || !step.next;
      root.querySelectorAll('[data-tutorial-target]').forEach(function (node) { node.classList.remove('tutorial-target--active'); });
      if (options.render) options.render(index, step);
      var el = completed ? null : target();
      var targets = Array.isArray(el) ? el.filter(Boolean) : (el ? [el] : []);
      targets.forEach(function (node) { node.classList.add('tutorial-target--active'); });
      if (targets.length) {
        var first = targets[0];
        if (options.shouldScroll !== false) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
        window.requestAnimationFrame(function () { positionSpotlights(targets); });
      } else {
        spotlightLayer.innerHTML = '';
      }
    }
    function positionSpotlights(targets) {
      spotlightLayer.innerHTML = '';
      targets.forEach(function (target) {
        var rect = target.getBoundingClientRect();
        var spotlight = document.createElement('div');
        spotlight.className = 'tutorial-shell__spotlight';
        spotlight.style.top = Math.max(8, rect.top - 6) + 'px';
        spotlight.style.left = Math.max(8, rect.left - 6) + 'px';
        spotlight.style.width = Math.max(0, rect.width + 12) + 'px';
        spotlight.style.height = Math.max(0, rect.height + 12) + 'px';
        spotlightLayer.appendChild(spotlight);
      });
    }
    function save(status) { try { localStorage.setItem(storageKey, JSON.stringify({ status: status, step: index + 1 })); } catch (e) {} }
    function open() { started = true; completed = false; index = 0; save('started'); shell.hidden = false; shell.classList.add('is-open'); document.body.classList.add('tutorial-is-open'); emit('tutorial_start', index + 1); render(); }
    function close(kind) { shell.hidden = true; shell.classList.remove('is-open'); document.body.classList.remove('tutorial-is-open'); if (kind) { save(kind === 'tutorial_skip' ? 'skipped' : 'closed'); emit(kind, index + 1); } }
    function next() { if (!started || completed || !options.canAdvance(index)) return; goTo(index + 1); }
    function goTo(nextIndex) { if (!started || completed || nextIndex < 0 || nextIndex >= options.steps.length) return; emit('tutorial_step_complete', index + 1); index = nextIndex; save('started'); render(); }
    function complete() { if (!started || completed) return; emit('tutorial_step_complete', index + 1); completed = true; save('completed'); emit('tutorial_complete', options.steps.length); render(); }
    function back() { if (index > 0 && !completed && (!options.canGoBack || options.canGoBack(index))) { index -= 1; render(); } }
    root.querySelector('[data-tutorial-start]').addEventListener('click', open);
    shell.querySelector('[data-tutorial-next]').addEventListener('click', next);
    shell.querySelector('[data-tutorial-back]').addEventListener('click', back);
    shell.querySelectorAll('[data-tutorial-close]').forEach(function (el) { el.addEventListener('click', function () { close('tutorial_skip'); }); });
    shell.querySelector('[data-tutorial-skip]').addEventListener('click', function () { close('tutorial_skip'); });
    shell.querySelector('[data-tutorial-restart]').addEventListener('click', function () { if (options.restart) options.restart(); open(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !shell.hidden) close('tutorial_skip'); });
    emit('tutorial_view', 0);
    window.addEventListener('resize', function () { if (!shell.hidden) render(); });
    window.addEventListener('scroll', function () { if (!shell.hidden && !completed) { var el = target(); var targets = Array.isArray(el) ? el.filter(Boolean) : (el ? [el] : []); positionSpotlights(targets); } }, { passive: true });
    return { open: open, next: next, goTo: goTo, complete: complete, render: render, close: close, getStep: function () { return index; } };
  }
  global.InterovesTutorial = { create: createTutorialShell };
})(window);
