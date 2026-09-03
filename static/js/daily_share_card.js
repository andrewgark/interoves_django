'use strict';

(function (root) {
  var WIDTH = 1080;
  var HEIGHT = 1920;
  var VERSION = '1';
  var FONT = "DejaVu Sans, Noto Sans, Segoe UI, Arial, sans-serif";

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
      var cell = 168;
      var gap = 18;
      var grid = cell * 4 + gap * 3;
      var gx = (WIDTH - grid) / 2;
      var gy = 640;
      var r;
      var c;
      for (r = 0; r < 4; r += 1) {
        for (c = 0; c < 4; c += 1) {
          parts.push(roundedRect(
            gx + c * (cell + gap),
            gy + r * (cell + gap),
            cell,
            cell,
            28,
            PALETTE.cell,
            'stroke="' + PALETTE.line + '" stroke-width="3" opacity="0.35"'
          ));
        }
      }
    } else {
      alphabettyDecor(parts, payload, rng, accent);
    }
    return parts.join('');
  }

  function alphabettyDecor(parts, payload, rng, accent) {
    var letters = 'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯABCDEFGHKLMNPRSTUWY';
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
    var titleLines = wrapText(payload.title || '', 22);
    var dateLines = wrapText(payload.date_label || '', 28);
    var headlineLines = wrapText(payload.headline || '', 22);
    var y = 230;
    var parts = [];
    parts.push(textLines(WIDTH / 2, y, titleLines, {
      lh: 64,
      prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="54" font-weight="700" fill="' + PALETTE.ink + '"',
    }));
    y += titleLines.length * 64 + 18;
    if (dateLines[0]) {
      parts.push(textLines(WIDTH / 2, y, dateLines, {
        lh: 42,
        prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="32" fill="' + PALETTE.muted + '"',
      }));
      y += dateLines.length * 42 + 36;
    } else {
      y += 24;
    }
    parts.push(textLines(WIDTH / 2, y, headlineLines, {
      lh: 78,
      prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="68" font-weight="700" fill="' + accent + '"',
    }));
    y += headlineLines.length * 78 + 28;
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
    parts.push(
      '<text x="' + (WIDTH / 2) + '" y="1748" text-anchor="middle" font-family="' + FONT +
      '" font-size="34" font-weight="700" fill="' + PALETTE.ink + '">' +
      esc(payload.brand || 'interoves.com') + '</text>'
    );
    parts.push(
      '<text x="' + (WIDTH / 2) + '" y="1798" text-anchor="middle" font-family="' + FONT +
      '" font-size="24" fill="' + PALETTE.muted + '">Inter Oves</text>'
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
    });
    return parts.join('');
  }

  function saladVisual(payload, top, bottom) {
    var results = payload.word_results || [];
    var count = results.length || Number(payload.word_count) || 0;
    if (!count) return '';
    var areaH = Math.max(240, bottom - top);
    var cols = count <= 4 ? count : (count <= 8 ? Math.ceil(count / 2) : 4);
    var rows = Math.ceil(count / cols);
    var gap = 22;
    var cell = Math.min(132, Math.floor((820 - gap * (cols - 1)) / cols));
    cell = Math.max(72, cell);
    var gridW = cols * cell + (cols - 1) * gap;
    var gridH = rows * cell + (rows - 1) * gap;
    var x0 = (WIDTH - gridW) / 2;
    var y0 = top + Math.max(0, (areaH - gridH) / 2);
    var parts = [];
    var i;
    for (i = 0; i < count; i += 1) {
      var col = i % cols;
      var row = Math.floor(i / cols);
      var item = results[i] || { hint_count: 0 };
      parts.push(roundedRect(
        x0 + col * (cell + gap),
        y0 + row * (cell + gap),
        cell,
        cell,
        cell * 0.28,
        saladFill(item.hint_count),
        ''
      ));
    }
    return parts.join('');
  }

  function alphabettyVisual(payload, top, bottom) {
    var accent = kindAccent(payload.kind || 'alphabetty');
    var cy = (top + bottom) / 2;
    var clock = payload.elapsed_compact || '';
    if (!clock) return '';
    return (
      '<circle cx="' + (WIDTH / 2) + '" cy="' + cy + '" r="168" fill="none" stroke="' +
      accent + '" stroke-width="10" opacity="0.35"/>' +
      '<text x="' + (WIDTH / 2) + '" y="' + (cy + 28) + '" text-anchor="middle" font-family="' +
      FONT + '" font-size="84" font-weight="700" fill="' + PALETTE.ink + '">' +
      esc(clock) + '</text>'
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
