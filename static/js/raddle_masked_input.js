/**
 * Маскированный ввод для лесенки (raddle): IMask.js + шаблон с сервера.
 * Шаблон: # — слот буквы, остальные символы — фиксированные литералы (дефис, пробел…).
 * data-raddle-script задаёт допустимые буквы: cyrillic, latin, mixed и их
 * варианты с _digits. Пунктуация приходит из маски отдельно.
 * Локальная проверка: node static/js/raddle_masked_input.test.js
 */
(function (global) {
  'use strict';

  var SLOT = '#';
  var CYRILLIC_LETTER_RE = /[а-яёА-ЯЁ]/;
  var CYRILLIC_EXTRACT_RE = /[а-яёА-ЯЁ]/g;
  var LATIN_LETTER_RE = /[a-zA-Z]/;
  var LATIN_EXTRACT_RE = /[a-zA-Z]/g;
  var MIXED_LETTER_RE = /[a-zA-Zа-яёА-ЯЁ]/;
  var MIXED_EXTRACT_RE = /[a-zA-Zа-яёА-ЯЁ]/g;
  var DIGIT_RE = /[0-9]/;
  var CYRILLIC_DIGIT_RE = /[а-яёА-ЯЁ0-9]/;
  var LATIN_DIGIT_RE = /[a-zA-Z0-9]/;
  var MIXED_DIGIT_RE = /[a-zA-Zа-яёА-ЯЁ0-9]/;
  var DIGIT_EXTRACT_RE = /[0-9]/g;
  var CYRILLIC_DIGIT_EXTRACT_RE = /[а-яёА-ЯЁ0-9]/g;
  var LATIN_DIGIT_EXTRACT_RE = /[a-zA-Z0-9]/g;
  var MIXED_DIGIT_EXTRACT_RE = /[a-zA-Zа-яёА-ЯЁ0-9]/g;
  var BOUND = 'data-raddle-mask-bound';
  var maskByInput = typeof WeakMap !== 'undefined' ? new WeakMap() : null;
  var maskByInputFallback = null;

  function slotCount(fmt) {
    var n = 0;
    for (var i = 0; i < fmt.length; i++) {
      if (fmt.charAt(i) === SLOT) n++;
    }
    return n;
  }

  function inputScript(input) {
    if (!input || typeof input.getAttribute !== 'function') return 'cyrillic';
    var s = input.getAttribute('data-raddle-script');
    if (s === 'latin' || s === 'mixed' || s === 'digits' ||
        s === 'cyrillic_digits' || s === 'latin_digits' || s === 'mixed_digits') return s;
    return 'cyrillic';
  }

  function isLatinScript(script) {
    return script === 'latin';
  }

  function isMixedScript(script) {
    return script === 'mixed';
  }

  function normalizeLetter(ch, script) {
    var s = String(ch || '');
    if (isLatinScript(script)) return s.toUpperCase();
    // Для цифр и смешанных режимов меняем только регистр букв.
    return s.replace(/ё/gi, function (m) {
      return m === 'ё' ? 'е' : 'Е';
    }).toUpperCase();
  }

  function extractRussianLetters(text) {
    var m = String(text || '').match(CYRILLIC_EXTRACT_RE);
    if (!m) return '';
    return m.map(function (ch) { return normalizeLetter(ch, 'cyrillic'); }).join('');
  }

  function extractLatinLetters(text) {
    var m = String(text || '').match(LATIN_EXTRACT_RE);
    if (!m) return '';
    return m.map(function (ch) { return normalizeLetter(ch, 'latin'); }).join('');
  }

  function extractMixedLetters(text) {
    var m = String(text || '').match(MIXED_EXTRACT_RE);
    if (!m) return '';
    return m.map(function (ch) { return normalizeLetter(ch, 'mixed'); }).join('');
  }

  function extractMatching(text, re, script) {
    var m = String(text || '').match(re);
    if (!m) return '';
    return m.map(function (ch) { return /[0-9]/.test(ch) ? ch : normalizeLetter(ch, script); }).join('');
  }

  function extractLetters(text, script) {
    if (isLatinScript(script)) return extractLatinLetters(text);
    if (script === 'digits') return extractMatching(text, DIGIT_EXTRACT_RE, script);
    if (script === 'cyrillic_digits') return extractMatching(text, CYRILLIC_DIGIT_EXTRACT_RE, 'cyrillic');
    if (script === 'latin_digits') return extractMatching(text, LATIN_DIGIT_EXTRACT_RE, 'latin');
    if (script === 'mixed_digits') return extractMatching(text, MIXED_DIGIT_EXTRACT_RE, 'mixed');
    if (isMixedScript(script)) return extractMixedLetters(text);
    return extractRussianLetters(text);
  }

  function lettersToDisplay(fmt, letters) {
    var li = 0;
    var out = '';
    for (var i = 0; i < fmt.length; i++) {
      var ch = fmt.charAt(i);
      if (ch === SLOT) {
        if (li < letters.length) out += letters.charAt(li++);
      } else {
        var need = 0;
        for (var j = 0; j < i; j++) {
          if (fmt.charAt(j) === SLOT) need++;
        }
        if (letters.length > need) out += ch;
      }
    }
    return out;
  }

  function getMaskInstance(input) {
    if (!input) return null;
    if (maskByInput) return maskByInput.get(input) || null;
    if (!maskByInputFallback) maskByInputFallback = new Map();
    return maskByInputFallback.get(input) || null;
  }

  function setMaskInstance(input, mask) {
    if (!input) return;
    if (maskByInput) {
      maskByInput.set(input, mask);
      return;
    }
    if (!maskByInputFallback) maskByInputFallback = new Map();
    maskByInputFallback.set(input, mask);
  }

  function letterReForScript(script) {
    if (isLatinScript(script)) return LATIN_LETTER_RE;
    if (script === 'digits') return DIGIT_RE;
    if (script === 'cyrillic_digits') return CYRILLIC_DIGIT_RE;
    if (script === 'latin_digits') return LATIN_DIGIT_RE;
    if (script === 'mixed_digits') return MIXED_DIGIT_RE;
    if (isMixedScript(script)) return MIXED_LETTER_RE;
    return CYRILLIC_LETTER_RE;
  }

  function buildMaskOptions(fmt, script) {
    var letterRe = letterReForScript(script);
    return {
      mask: fmt,
      definitions: (function () {
        var defs = {};
        defs[SLOT] = letterRe;
        return defs;
      })(),
      prepareChar: function (ch) {
        return normalizeLetter(ch, script);
      },
      lazy: true,
    };
  }

  function getLetters(input) {
    var mask = getMaskInstance(input);
    if (mask) return mask.unmaskedValue || '';
    return input.dataset.raddleLetters || '';
  }

  function setLetters(input, letters, opts) {
    opts = opts || {};
    var fmt = input.getAttribute('data-raddle-format') || '';
    var script = inputScript(input);
    var max = slotCount(fmt);
    var clean = extractLetters(letters, script).slice(0, max);
    var mask = getMaskInstance(input);
    if (mask) {
      mask.unmaskedValue = clean;
      return clean;
    }
    input.dataset.raddleLetters = clean;
    input.value = lettersToDisplay(fmt, clean);
    try {
      input.setSelectionRange(input.value.length, input.value.length);
    } catch (e) {}
    if (typeof opts.onChange === 'function') opts.onChange(input, clean);
    if (clean.length === max && typeof opts.onComplete === 'function') {
      opts.onComplete(input, clean);
    }
    return clean;
  }

  function getSubmitValue(input) {
    if (!input) return '';
    if (input.getAttribute('data-raddle-format')) return getLetters(input);
    return String(input.value || '').trim();
  }

  // Geometry-only native-input calibration. This deliberately never reads
  // individual characters to render them and never creates an editing layer.
  // G is the real font advance; S is the measured step of the empty cells.
  function fontMetrics(input) {
    if (!input || !global.document || !global.document.createElement) return null;
    var canvas = global.document.createElement('canvas');
    var ctx = canvas.getContext && canvas.getContext('2d');
    if (!ctx) return null;
    var style = global.getComputedStyle(input);
    // `getComputedStyle().font` may contain an unresolved var()/shorthand
    // that Canvas silently rejects and replaces with its default serif font.
    // Build a valid Canvas font from the resolved longhands instead.
    // Canvas accepts the core font shorthand, but browser implementations
    // differ on `font-variant` in this setter. Keep it out of the string;
    // ligatures are disabled on the actual input via CSS anyway.
    ctx.font = [style.fontStyle, style.fontWeight,
      style.fontSize + ' ' + style.fontFamily].join(' ');
    var chars = ['A', 'M', 'W', 'И', 'Ж', 'Я', 'Ё', '0', '-', ' '];
    var metrics = {};
    chars.forEach(function (ch) {
      metrics[ch === ' ' ? 'space' : ch] = ctx.measureText(ch).width;
    });
    return {
      font: style.font,
      canvasFont: ctx.font,
      fontFamily: style.fontFamily,
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      letterSpacing: style.letterSpacing,
      values: metrics,
      advance: metrics['0'],
      loaded: global.document.fonts && global.document.fonts.check
        ? global.document.fonts.check(ctx.font, 'И') : null,
    };
  }

  function waitForInputFont(input) {
    if (!input || !global.document || !global.document.fonts) return Promise.resolve();
    var style = global.getComputedStyle(input);
    var family = style.fontFamily;
    var sample = 'AMWИЖЯЁ0- ';
    var ready = global.document.fonts.ready || Promise.resolve();
    return ready.then(function () {
      if (!global.document.fonts.load) return null;
      return global.document.fonts.load(
        style.fontWeight + ' ' + style.fontSize + ' ' + family,
        sample
      );
    });
  }

  function readPx(value) {
    var n = parseFloat(value);
    return isFinite(n) ? n : 0;
  }

  function measureGeometry(input) {
    if (!input || !input.getBoundingClientRect) return null;
    var line = input.closest ? input.closest('.new-raddle-line') : null;
    var mask = line && line.querySelector ? line.querySelector('.new-raddle-mask') : null;
    var inputStyle = global.getComputedStyle(input);
    var maskStyle = mask ? global.getComputedStyle(mask) : null;
    var inputRect = input.getBoundingClientRect();
    var maskRect = mask ? mask.getBoundingClientRect() : null;
    var cells = mask ? Array.prototype.slice.call(mask.children).map(function (cell) {
      var rect = cell.getBoundingClientRect();
      return {
        text: cell.textContent,
        left: rect.left,
        width: rect.width,
        center: rect.left + rect.width / 2,
      };
    }) : [];
    var centers = cells.map(function (cell) { return cell.center; });
    var steps = [];
    for (var i = 1; i < centers.length; i++) steps.push(centers[i] - centers[i - 1]);
    var slotStep = steps.length ? steps.reduce(function (a, b) { return a + b; }, 0) / steps.length : 0;
    var metrics = fontMetrics(input);
    var measuredLetterSpacing = slotStep && metrics ? slotStep - metrics.advance : 0;
    return {
      input: {
        rectWidth: inputRect.width,
        clientWidth: input.clientWidth,
        offsetWidth: input.offsetWidth,
        computedWidth: inputStyle.width,
        paddingLeft: inputStyle.paddingLeft,
        paddingRight: inputStyle.paddingRight,
        borderLeft: inputStyle.borderLeftWidth,
        borderRight: inputStyle.borderRightWidth,
        contentWidth: input.clientWidth - readPx(inputStyle.paddingLeft) - readPx(inputStyle.paddingRight),
      },
      mask: maskRect ? {
        left: maskRect.left,
        width: maskRect.width,
        cellCount: cells.length,
        slotStep: slotStep,
        cells: cells,
      } : null,
      maskHidden: !!(mask && (!maskRect || !maskRect.width)),
      font: metrics,
      measuredLetterSpacing: measuredLetterSpacing,
      currentLetterSpacing: inputStyle.letterSpacing,
    };
  }

  function calibrateGeometry(input) {
    var result = measureGeometry(input);
    if (!result || !result.font || !result.mask || !result.mask.slotStep) return result;
    var line = input.closest ? input.closest('.new-raddle-line') : null;
    if (line) {
      line.style.setProperty('--raddle-glyph-advance', result.font.advance + 'px');
      line.style.setProperty('--raddle-slot-step', result.mask.slotStep + 'px');
      line.style.setProperty('--raddle-char-gap', result.measuredLetterSpacing + 'px');
    }
    return result;
  }

  function debugGeometry(input) {
    var target = input;
    if (!target && global.document) target = global.document.querySelector('input.new-raddle-input');
    if (!target) return null;
    var run = function () {
      var result = measureGeometry(target);
      if (global.console && console.group) {
        console.group('Raddle native geometry');
        console.table(result.input);
        console.table(result.font && result.font.values);
        console.log('Resolved font metrics:', result.font);
        if (result.mask) console.table(result.mask.cells);
        console.log(result);
        console.groupEnd();
      }
      return result;
    };
    return waitForInputFont(target).then(run);
  }

  function scheduleGeometryCalibration(input) {
    var run = function () {
      if (global.requestAnimationFrame) global.requestAnimationFrame(function () { calibrateGeometry(input); });
      else calibrateGeometry(input);
    };
    var line = input && input.closest ? input.closest('.new-raddle-line') : null;
    if (line && global.ResizeObserver && !input.__raddleGeometryObserver) {
      input.__raddleGeometryObserver = new global.ResizeObserver(run);
      input.__raddleGeometryObserver.observe(line);
    }
    waitForInputFont(input).then(run);
  }

  function bindInput(input, hooks) {
    if (!input || input.getAttribute(BOUND) === '1') return;
    var fmt = input.getAttribute('data-raddle-format') || '';
    if (!fmt) return;
    if (typeof IMask === 'undefined') return;

    var script = inputScript(input);
    input.setAttribute(BOUND, '1');
    input.setAttribute('inputmode', 'text');
    input.setAttribute('autocapitalize', 'characters');
    input.removeAttribute('maxlength');

    var initial = input.getAttribute('value') || input.value || '';
    input.removeAttribute('value');

    var mask = IMask(input, buildMaskOptions(fmt, script));
    setMaskInstance(input, mask);

    // Restoring the initial value is programmatic (for example after a live
    // task-card replacement). It must not look like a user completion: IMask
    // may emit `complete` when `unmaskedValue` is assigned.
    var initializing = true;

    function syncDataset() {
      input.dataset.raddleLetters = mask.unmaskedValue || '';
      if (input.classList && input.classList.toggle) {
        input.classList.toggle('new-raddle-input--filled', !!mask.value);
      }
    }

    mask.on('accept', function () {
      syncDataset();
      if (initializing) return;
      if (typeof hooks.onChange === 'function') {
        hooks.onChange(input, mask.unmaskedValue || '');
      }
    });

    mask.on('complete', function () {
      syncDataset();
      if (initializing) return;
      if (typeof hooks.onComplete === 'function') {
        hooks.onComplete(input, mask.unmaskedValue || '');
      }
    });

    if (initial) {
      mask.unmaskedValue = extractLetters(initial, script);
    } else {
      syncDataset();
    }
    initializing = false;
    scheduleGeometryCalibration(input);
  }

  function refresh(input) {
    if (!input) return;
    setLetters(input, getLetters(input));
  }

  function bindAll(root, hooks) {
    var scope = root || document;
    scope.querySelectorAll('input[name="word"][data-raddle-format]').forEach(function (input) {
      bindInput(input, hooks);
    });
  }

  global.RaddleMaskedInput = {
    SLOT: SLOT,
    slotCount: slotCount,
    extractRussianLetters: extractRussianLetters,
    extractLatinLetters: extractLatinLetters,
    extractMixedLetters: extractMixedLetters,
    extractLetters: extractLetters,
    lettersToDisplay: lettersToDisplay,
    buildMaskOptions: buildMaskOptions,
    getLetters: getLetters,
    setLetters: setLetters,
    getSubmitValue: getSubmitValue,
    bindInput: bindInput,
    bindAll: bindAll,
    refresh: refresh,
    fontMetrics: fontMetrics,
    measureGeometry: measureGeometry,
    calibrateGeometry: calibrateGeometry,
    debugGeometry: debugGeometry,
  };
})(typeof window !== 'undefined' ? window : global);
