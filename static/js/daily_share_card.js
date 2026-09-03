'use strict';

(function (root) {
  var WIDTH = 1080;
  var HEIGHT = 1920;
  var VERSION = '4';
  var FONT = "DejaVu Sans, Noto Sans, Segoe UI, Arial, sans-serif";
  var LOGO_SIZE = 56;
  var LOGO_GAP = 16;
  var LOGO_DATA_URI = 'data:image/png;base64,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';

  var PALETTE = {
    bg: '#F7F4EF',
    ink: '#1A1A1A',
    muted: '#5C6560',
    accent: '#2F6F4E',
    accentSoft: '#1F6F5E',
    cell: '#FFFFFF',
    line: '#D7D0C6',
    green: '#2F9E6B',
    yellow: '#C9A227',
    red: '#C45C4A',
    given: '#1F6F5E',
    givenFill: '#E4EFE8',
    empty: '#E7E1D8',
  };

  var KIND_COLORS = {
    ladder: '#2F6F4E',
    salad: '#2F6F4E',
    alphabetty: '#3B6EA5',
  };

  var cache = { key: '', blob: null };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0;
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function wrapText(text, maxChars) {
    var words = String(text || '').split(/\s+/).filter(Boolean);
    var lines = [];
    var current = '';
    words.forEach(function (word) {
      var trial = current ? current + ' ' + word : word;
      if (trial.length <= maxChars) {
        current = trial;
      } else {
        if (current) lines.push(current);
        current = word;
      }
    });
    if (current) lines.push(current);
    return lines.length ? lines : [''];
  }

  function textLines(x, y, lines, attrs) {
    return lines.map(function (line, i) {
      return '<text x="' + x + '" y="' + (y + i * (attrs.lh || 48)) + '" ' + attrs.prop + '>' +
        esc(line) + '</text>';
    }).join('');
  }

  function roundedRect(x, y, w, h, r, fill, extra) {
    return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h +
      '" rx="' + r + '" ry="' + r + '" fill="' + fill + '" ' + (extra || '') + '/>';
  }

  function stateFill(state) {
    if (state === 'green') return PALETTE.green;
    if (state === 'yellow') return PALETTE.yellow;
    if (state === 'red') return PALETTE.red;
    if (state === 'given') return PALETTE.givenFill;
    return PALETTE.empty;
  }

  function stateStroke(state) {
    if (state === 'given') return PALETTE.given;
    if (state === 'empty') return PALETTE.line;
    return 'none';
  }

  function saladFill(hintCount) {
    var n = Math.max(0, Number(hintCount) || 0);
    if (n <= 0) return PALETTE.green;
    if (n === 1) return PALETTE.yellow;
    if (n === 2) return '#D0893B';
    return PALETTE.red;
  }

  function kindAccent(kind) {
    return KIND_COLORS[kind] || PALETTE.accent;
  }

  function decoBackground(payload) {
    var kind = payload.kind || payload.game_kind;
    var accent = kindAccent(kind);
    var rng = mulberry32(Number(payload.seed) || 0);
    var parts = [
      '<rect width="' + WIDTH + '" height="' + HEIGHT + '" fill="' + PALETTE.bg + '"/>',
      '<rect width="' + WIDTH + '" height="10" fill="' + accent + '"/>',
    ];
    if (kind === 'ladder') {
      var i;
      for (i = 0; i < 7; i += 1) {
        var y = 280 + i * 210;
        var w = 420 + rng() * 280;
        var x = rng() < 0.5 ? -80 : WIDTH - w + 80;
        parts.push(roundedRect(x, y, w, 96, 18, accent, 'opacity="0.06"'));
      }
    } else if (kind === 'salad') {
      var i;
      for (i = 0; i < 6; i += 1) {
        var size = 90 + rng() * 140;
        var sx = rng() < 0.5 ? -40 : WIDTH - size + 40;
        var sy = 300 + rng() * 1180;
        parts.push(roundedRect(sx, sy, size, size, 22, accent, 'opacity="0.06"'));
      }
    } else {
      alphabettyDecor(parts, payload, rng, accent);
    }
    return parts.join('');
  }

  function alphabettyDecor(parts, payload, rng, accent) {
    var letters = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ';
    var variant = Number(payload.variant);
    if (!isFinite(variant)) variant = (Number(payload.seed) || 0) % 3;
    variant = ((variant % 3) + 3) % 3;
    var count = variant === 1 ? 12 : variant === 2 ? 20 : 9;
    var i;
    if (variant === 1) {
      var cx = WIDTH / 2;
      var cy = 980;
      var radius = 340;
      for (i = 0; i < count; i += 1) {
        var ang = (Math.PI * 2 * i) / count - Math.PI / 2;
        var ch = letters.charAt((Number(payload.seed) + i * 7) % letters.length);
        parts.push(
          '<text x="' + (cx + Math.cos(ang) * radius) + '" y="' + (cy + Math.sin(ang) * radius) +
          '" text-anchor="middle" font-family="' + FONT + '" font-size="92" font-weight="700" fill="' +
          accent + '" opacity="0.14">' + esc(ch) + '</text>'
        );
      }
      return;
    }
    if (variant === 2) {
      var cols = 5;
      var rows = 4;
      var startX = 140;
      var startY = 620;
      for (i = 0; i < cols * rows; i += 1) {
        var col = i % cols;
        var row = Math.floor(i / cols);
        var letter = letters.charAt((Number(payload.seed) + i * 3) % letters.length);
        parts.push(
          '<text x="' + (startX + col * 180) + '" y="' + (startY + row * 210) +
          '" font-family="' + FONT + '" font-size="120" font-weight="700" fill="' +
          accent + '" opacity="' + (0.07 + (i % 3) * 0.03) + '">' + esc(letter) + '</text>'
        );
      }
      return;
    }
    for (i = 0; i < count; i += 1) {
      var ch2 = letters.charAt((Number(payload.seed) + i * 11) % letters.length);
      var x = 80 + rng() * 880;
      var y = 520 + rng() * 980;
      var size = 140 + rng() * 180;
      parts.push(
        '<text x="' + x + '" y="' + y + '" font-family="' + FONT + '" font-size="' + size +
        '" font-weight="700" fill="' + accent + '" opacity="' + (0.06 + rng() * 0.08) +
        '" transform="rotate(' + (rng() * 36 - 18) + ' ' + x + ' ' + y + ')">' +
        esc(ch2) + '</text>'
      );
    }
  }

  function headerBlock(payload, accent) {
    var dateLines = wrapText(payload.date_label || '', 28);
    var headlineLines = wrapText(payload.headline || '', 32);
    var y = 210;
    var parts = [];
    if (dateLines[0]) {
      parts.push(textLines(WIDTH / 2, y, dateLines, {
        lh: 42,
        prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="32" fill="' + PALETTE.muted + '"',
      }));
      y += dateLines.length * 42 + 40;
    } else {
      y += 24;
    }
    parts.push(textLines(WIDTH / 2, y, headlineLines, {
      lh: 70,
      prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="54" font-weight="700" fill="' + accent + '"',
    }));
    y += headlineLines.length * 70 + 28;
    return { svg: parts.join(''), bottom: y };
  }

  function footerBlock(payload) {
    var statsLines = wrapText(payload.stats_line || '', 34);
    var y = 1588;
    var parts = [];
    if (statsLines[0]) {
      parts.push(textLines(WIDTH / 2, y, statsLines, {
        lh: 40,
        prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="30" fill="' + PALETTE.muted + '"',
      }));
      y += statsLines.length * 40 + 36;
    }
    var brand = payload.brand || 'interoves.com';
    var textW = Math.max(200, brand.length * 21);
    var groupW = LOGO_SIZE + LOGO_GAP + textW;
    var x0 = (WIDTH - groupW) / 2;
    var logoY = 1720;
    if (LOGO_DATA_URI) {
      parts.push(
        '<image href="' + LOGO_DATA_URI + '" x="' + x0 + '" y="' + logoY +
        '" width="' + LOGO_SIZE + '" height="' + LOGO_SIZE + '"/>'
      );
    }
    parts.push(
      '<text x="' + (x0 + LOGO_SIZE + LOGO_GAP) + '" y="' + (logoY + 38) +
      '" text-anchor="start" font-family="' + FONT + '" font-size="34" font-weight="700" fill="' +
      PALETTE.ink + '">' + esc(brand) + '</text>'
    );
    return parts.join('');
  }

  function ladderVisual(payload, top, bottom) {
    var steps = payload.steps || [];
    if (!steps.length) return '';
    var areaH = Math.max(240, bottom - top);
    var gap = steps.length > 10 ? 8 : 12;
    var stepH = Math.min(78, Math.floor((areaH - gap * (steps.length - 1)) / steps.length));
    stepH = Math.max(36, stepH);
    var maxLen = 1;
    steps.forEach(function (step) {
      maxLen = Math.max(maxLen, Number(step.length) || 1);
    });
    var minW = 280;
    var maxW = 820;
    var totalH = steps.length * stepH + (steps.length - 1) * gap;
    var y0 = top + Math.max(0, (areaH - totalH) / 2);
    var parts = [];
    steps.forEach(function (step, i) {
      var t = (Number(step.length) || 1) / maxLen;
      var w = Math.round(minW + (maxW - minW) * t);
      var x = (WIDTH - w) / 2;
      var y = y0 + i * (stepH + gap);
      var fill = stateFill(step.state);
      var stroke = stateStroke(step.state);
      var extra = stroke === 'none' ? '' : 'stroke="' + stroke + '" stroke-width="5"';
      parts.push(roundedRect(x, y, w, stepH, Math.min(16, stepH / 3), fill, extra));
      if (step.label) {
        var label = String(step.label);
        var fontSize = Math.min(stepH * 0.52, w / Math.max(1, label.length * 0.62));
        fontSize = Math.max(16, fontSize);
        var textFill = step.state === 'given' ? PALETTE.given : PALETTE.ink;
        parts.push(
          '<text x="' + (x + w / 2) + '" y="' + (y + stepH * 0.68) +
          '" text-anchor="middle" font-family="' + FONT + '" font-size="' + fontSize.toFixed(1) +
          '" font-weight="700" fill="' + textFill + '">' + esc(label) + '</text>'
        );
      }
    });
    return parts.join('');
  }

  function saladVisual(payload, top, bottom) {
    var letters = payload.grid || [];
    var results = payload.word_results || [];
    var count = results.length || Number(payload.word_count) || 0;
    if (!letters.length && !count) return '';
    var areaH = Math.max(240, bottom - top);
    var cell = 148;
    var gap = 16;
    var gridSize = cell * 4 + gap * 3;
    var tileGap = 12;
    var tile = count
      ? Math.min(64, Math.floor((820 - tileGap * Math.max(0, count - 1)) / Math.max(1, count)))
      : 0;
    tile = count ? Math.max(24, tile) : 0;
    var between = letters.length && count ? 40 : 0;
    var totalH = (letters.length ? gridSize : 0) + between + (count ? tile : 0);
    var y0 = top + Math.max(0, (areaH - totalH) / 2);
    var parts = [];
    var r;
    var c;
    if (letters.length) {
      var gx = (WIDTH - gridSize) / 2;
      for (r = 0; r < 4; r += 1) {
        for (c = 0; c < 4; c += 1) {
          var cx = gx + c * (cell + gap);
          var cy = y0 + r * (cell + gap);
          var letter = String(letters[r * 4 + c] || '');
          parts.push(roundedRect(
            cx,
            cy,
            cell,
            cell,
            24,
            PALETTE.cell,
            'stroke="' + PALETTE.line + '" stroke-width="4"'
          ));
          if (letter) {
            parts.push(
              '<text x="' + (cx + cell / 2) + '" y="' + (cy + cell * 0.68) +
              '" text-anchor="middle" font-family="' + FONT + '" font-size="' +
              Math.round(cell * 0.48) + '" font-weight="700" fill="' + PALETTE.ink + '">' +
              esc(letter) + '</text>'
            );
          }
        }
      }
    }
    if (count) {
      var rowW = count * tile + (count - 1) * tileGap;
      var tx = (WIDTH - rowW) / 2;
      var ty = y0 + (letters.length ? gridSize + between : 0);
      var i;
      for (i = 0; i < count; i += 1) {
        var item = results[i] || { hint_count: 0 };
        parts.push(roundedRect(
          tx + i * (tile + tileGap),
          ty,
          tile,
          tile,
          tile * 0.28,
          saladFill(item.hint_count),
          ''
        ));
      }
    }
    return parts.join('');
  }

  function attemptsWord(payload) {
    if (payload.attempts_word) return String(payload.attempts_word);
    var n = Math.max(0, Number(payload.attempts) || 0);
    if ((payload.locale || 'ru') === 'en') return n === 1 ? 'try' : 'tries';
    var n10 = n % 10;
    var n100 = n % 100;
    if (n10 === 1 && n100 !== 11) return 'попытка';
    if (n10 >= 2 && n10 <= 4 && n100 !== 12 && n100 !== 13 && n100 !== 14) return 'попытки';
    return 'попыток';
  }

  function alphabettyVisual(payload, top, bottom) {
    var accent = kindAccent(payload.kind || 'alphabetty');
    var cy = (top + bottom) / 2;
    if (payload.attempts == null || payload.attempts === '') return '';
    var count = String(payload.attempts);
    var word = attemptsWord(payload);
    var fontSize = count.length > 3 ? 56 : 84;
    var wordSize = 32;
    return (
      '<circle cx="' + (WIDTH / 2) + '" cy="' + cy + '" r="176" fill="none" stroke="' +
      accent + '" stroke-width="10" opacity="0.35"/>' +
      '<text x="' + (WIDTH / 2) + '" y="' + (cy - 6) +
      '" text-anchor="middle" font-family="' + FONT + '" font-size="' + fontSize +
      '" font-weight="700" fill="' + PALETTE.ink + '">' +
      esc(count) + '</text>' +
      '<text x="' + (WIDTH / 2) + '" y="' + (cy + 52) +
      '" text-anchor="middle" font-family="' + FONT + '" font-size="' + wordSize +
      '" font-weight="600" fill="' + PALETTE.muted + '">' +
      esc(word) + '</text>'
    );
  }

  function buildShareCardSvg(payload) {
    payload = payload || {};
    var kind = payload.kind || payload.game_kind || 'ladder';
    var accent = kindAccent(kind);
    var header = headerBlock(payload, accent);
    var visualTop = header.bottom + 24;
    var visualBottom = 1540;
    var visual = '';
    if (kind === 'ladder') visual = ladderVisual(payload, visualTop, visualBottom);
    else if (kind === 'salad') visual = saladVisual(payload, visualTop, visualBottom);
    else visual = alphabettyVisual(payload, visualTop, visualBottom);
    return (
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + WIDTH + '" height="' + HEIGHT +
      '" viewBox="0 0 ' + WIDTH + ' ' + HEIGHT + '" role="img">' +
      decoBackground(payload) +
      header.svg +
      visual +
      footerBlock(payload) +
      '</svg>'
    );
  }

  function rasterizeSvgToPngBlob(svgText) {
    return new Promise(function (resolve, reject) {
      if (typeof Image === 'undefined' || typeof document === 'undefined') {
        reject(new Error('canvas-unavailable'));
        return;
      }
      var blob = new Blob([svgText], { type: 'image/svg+xml;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function () {
        try {
          var canvas = document.createElement('canvas');
          canvas.width = WIDTH;
          canvas.height = HEIGHT;
          var ctx = canvas.getContext('2d');
          if (!ctx) throw new Error('canvas-context');
          ctx.fillStyle = PALETTE.bg;
          ctx.fillRect(0, 0, WIDTH, HEIGHT);
          ctx.drawImage(img, 0, 0, WIDTH, HEIGHT);
          if (typeof canvas.toBlob === 'function') {
            canvas.toBlob(function (png) {
              URL.revokeObjectURL(url);
              if (!png) reject(new Error('png-failed'));
              else resolve(png);
            }, 'image/png');
            return;
          }
          var dataUrl = canvas.toDataURL('image/png');
          URL.revokeObjectURL(url);
          var comma = dataUrl.indexOf(',');
          var binary = atob(dataUrl.slice(comma + 1));
          var bytes = new Uint8Array(binary.length);
          var i;
          for (i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
          resolve(new Blob([bytes], { type: 'image/png' }));
        } catch (err) {
          URL.revokeObjectURL(url);
          reject(err);
        }
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('svg-image-failed'));
      };
      img.src = url;
    });
  }

  function payloadKey(payload) {
    try {
      return JSON.stringify(payload || {});
    } catch (err) {
      return String(Date.now());
    }
  }

  function renderShareCardPng(payload) {
    var key = payloadKey(payload);
    if (cache.blob && cache.key === key) {
      return Promise.resolve(cache.blob);
    }
    return rasterizeSvgToPngBlob(buildShareCardSvg(payload)).then(function (blob) {
      cache = { key: key, blob: blob };
      return blob;
    });
  }

  function resetCache() {
    cache = { key: '', blob: null };
  }

  var api = {
    WIDTH: WIDTH,
    HEIGHT: HEIGHT,
    VERSION: VERSION,
    PALETTE: PALETTE,
    buildShareCardSvg: buildShareCardSvg,
    rasterizeSvgToPngBlob: rasterizeSvgToPngBlob,
    renderShareCardPng: renderShareCardPng,
    resetCache: resetCache,
  };

  root.DailyShareCard = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
