/**
 * Разбор ввода для заданий «Замены» (строка / вставка из документа).
 * Зеркало логики: games/replacements_input_parse.py (тесты Django).
 * Локальная проверка: node static/js/replacements_input_parse.test.js
 */
(function (global) {
  'use strict';

  function normPasteText(s) {
    return (s || '').replace(/\u00a0/g, ' ').replace(/\r\n/g, '\n');
  }

  function normalizeQuotesLine(s) {
    return normPasteText(s)
      .replace(/\u201C|\u201D|\u201E|\u2033/g, '"')
      .replace(/\u00AB/g, '«')
      .replace(/\u00BB/g, '»');
  }

  function firstNonEmptyLineInRaw(raw) {
    var lines = normPasteText(String(raw)).split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].replace(/\s/g, '')) return lines[i];
    }
    return '';
  }

  /** Буквы и цифры (латиница + кириллица); пунктуация не входит в токен. */
  var LETTER_RUN_RE = /[A-Za-z0-9\u0400-\u04FF\u0500-\u052F]+/g;

  function letterTokenSpans(s) {
    var text = normPasteText(String(s));
    var out = [];
    var m;
    LETTER_RUN_RE.lastIndex = 0;
    while ((m = LETTER_RUN_RE.exec(text)) !== null) {
      out.push({ orig: m[0], low: m[0].toLowerCase() });
    }
    return out;
  }

  function templateTokenPattern(L, nSlots) {
    if (!L || L.length !== nSlots + 1) return null;
    var pattern = [];
    var i;
    for (i = 0; i <= nSlots; i++) {
      var spans = letterTokenSpans(L[i]);
      for (var t = 0; t < spans.length; t++) pattern.push(spans[t].low);
      if (i < nSlots) pattern.push(null);
    }
    return pattern;
  }

  function alignAnswersToTemplate(pattern, userSpans) {
    if (!pattern || !userSpans || !userSpans.length) return null;
    var nSlots = 0;
    var si;
    for (si = 0; si < pattern.length; si++) {
      if (pattern[si] === null) nSlots++;
    }
    if (userSpans.length === nSlots) {
      var only = [];
      for (si = 0; si < userSpans.length; si++) only.push(userSpans[si].orig);
      return only;
    }
    var out = [];
    var j = 0;
    for (si = 0; si < pattern.length; si++) {
      if (pattern[si] === null) {
        if (j >= userSpans.length) return null;
        out.push(userSpans[j].orig);
        j++;
      } else {
        while (j < userSpans.length && userSpans[j].low !== pattern[si]) j++;
        if (j >= userSpans.length || userSpans[j].low !== pattern[si]) return null;
        j++;
      }
    }
    return out.length === nSlots ? out : null;
  }

  function parseReplTokenLine(raw, L, nSlots) {
    if (nSlots <= 0) return [];
    var first = firstNonEmptyLineInRaw(raw);
    if (!String(first).replace(/\s/g, '')) return null;
    var user = letterTokenSpans(first);
    if (!user.length) return null;
    var pattern = templateTokenPattern(L, nSlots);
    if (!pattern) return null;
    return alignAnswersToTemplate(pattern, user);
  }

  function parseReplTabLine(raw, nSlots) {
    var n = parseInt(nSlots, 10) || 0;
    raw = (raw || '').trim();
    if (n <= 0) return [];
    if (!raw) return null;
    if (n === 1) return [raw];
    var tab = raw.split('\t');
    if (tab.length === n) return tab.map(function (s) { return s.trim(); });
    var semi = raw.split(/\s*;\s*/);
    if (semi.length === n) return semi.map(function (s) { return s.trim(); });
    return null;
  }

  /**
   * Несколько кавычек с группами слов ("А Б", "В") → плоский список слотов по пробелам.
   * Нужно для строки «как в ответе»: 4 кавычки при 11 слотах в условии (капс внутри групп).
   */
  function expandQuotedChunksByWhitespace(chunks, nSlots) {
    var n = parseInt(nSlots, 10) || 0;
    if (!chunks || !chunks.length || n <= 0) return null;
    var flat = [];
    for (var c = 0; c < chunks.length; c++) {
      var seg = (chunks[c] || '').trim();
      if (!seg) continue;
      var parts = seg.split(/\s+/);
      for (var p = 0; p < parts.length; p++) {
        var t = parts[p].trim();
        if (!t) continue;
        t = t.replace(/\?+$/g, '').replace(/!+$/g, '');
        flat.push(t);
      }
    }
    if (flat.length === n) return flat;
    if (flat.length > n) return flat.slice(0, n);
    return null;
  }

  /**
   * Подряд идущие фрагменты в "..." или «...» (как в документах с « и » между группами).
   */
  function parseReplMixedQuotedLine(line, nSlots) {
    var n = parseInt(nSlots, 10) || 0;
    if (n <= 0) return [];
    line = normalizeQuotesLine(String(line));
    if (!line.replace(/\s/g, '')) return null;
    var out = [];
    var i = 0;
    var len = line.length;
    while (i < len && out.length < n + 24) {
      while (i < len && /\s/.test(line.charAt(i))) i++;
      if (i >= len) break;
      var c = line.charAt(i);
      if (c === '"') {
        i++;
        var buf = '';
        while (i < len) {
          if (line.charAt(i) === '\\' && i + 1 < len) {
            buf += line.charAt(i + 1);
            i += 2;
            continue;
          }
          if (line.charAt(i) === '"') break;
          buf += line.charAt(i);
          i++;
        }
        if (i < len && line.charAt(i) === '"') i++;
        out.push(buf.trim());
      } else if (c === '«') {
        i++;
        var buf2 = '';
        while (i < len && line.charAt(i) !== '»') {
          buf2 += line.charAt(i);
          i++;
        }
        if (i < len && line.charAt(i) === '»') i++;
        out.push(buf2.trim());
      } else {
        i++;
      }
    }
    if (out.length >= n) return out.slice(0, n);
    var expanded = expandQuotedChunksByWhitespace(out, n);
    if (expanded) return expanded;
    return null;
  }

  function parseReplQuotedDoubleLine(raw, nSlots) {
    var n = parseInt(nSlots, 10) || 0;
    if (n <= 0) return [];
    var line = normalizeQuotesLine(String(raw)).split(/\r?\n/)[0];
    if (!line.replace(/\s/g, '')) return null;
    if (n === 1) {
      var t = line.trim();
      if (t.length >= 2 && t.charAt(0) === '"' && t.charAt(t.length - 1) === '"') {
        return [t.slice(1, -1).trim()];
      }
      return null;
    }
    var re = /"((?:[^"\\]|\\.)*)"/g;
    var out = [];
    var m;
    while ((m = re.exec(line)) !== null) {
      out.push(m[1].replace(/\\(.)/g, '$1').trim());
    }
    if (out.length === n) return out;
    if (out.length > n) return out.slice(0, n);
    var exp = expandQuotedChunksByWhitespace(out, n);
    if (exp) return exp;
    return null;
  }

  function parseReplGuillemetLine(raw, nSlots) {
    var n = parseInt(nSlots, 10) || 0;
    if (n <= 0) return [];
    var line = normalizeQuotesLine(String(raw)).split(/\r?\n/)[0];
    if (!line.replace(/\s/g, '')) return null;
    if (n === 1) {
      var mm = line.match(/^\s*«([^»]*)»\s*$/);
      if (mm) return [mm[1].trim()];
      return null;
    }
    var re = /«([^»]*)»/g;
    var out = [];
    var m;
    while ((m = re.exec(line)) !== null) {
      out.push(m[1].trim());
    }
    if (out.length === n) return out;
    if (out.length > n) return out.slice(0, n);
    var exp2 = expandQuotedChunksByWhitespace(out, n);
    if (exp2) return exp2;
    return null;
  }

  function extractReplWordsLiterals(container) {
    var L = [];
    var buf = '';
    var nSlots = 0;
    if (!container || !container.children) return { literals: [], nSlots: 0 };
    var kids = container.children;
    for (var i = 0; i < kids.length; i++) {
      var el = kids[i];
      if (el.classList && el.classList.contains('new-replacements-static')) {
        buf += normPasteText(el.textContent);
      } else if (el.classList && el.classList.contains('new-replacements-partial-answer')) {
        buf += normPasteText(el.textContent);
      } else if (el.tagName === 'INPUT' && el.name === 'answers[]') {
        L.push(buf);
        buf = '';
        nSlots++;
      }
    }
    L.push(buf);
    return { literals: L, nSlots: nSlots };
  }

  function normLiteralMatch(s) {
    return String(s || '').replace(/\u2013/g, '-').replace(/\u2014/g, '-');
  }

  function findLiteral(rem, lit) {
    if (!lit) return 0;
    var pos = rem.indexOf(lit);
    if (pos >= 0) return pos;
    return normLiteralMatch(rem).indexOf(normLiteralMatch(lit));
  }

  function skipLiteral(rem, lit) {
    if (!lit) return rem;
    if (rem.indexOf(lit) === 0) return rem.slice(lit.length);
    var nr = normLiteralMatch(rem);
    var nl = normLiteralMatch(lit);
    if (nl && nr.indexOf(nl) === 0) return rem.slice(nl.length);
    return rem;
  }

  function consumeLiteralPrefix(rem, lit) {
    if (!lit || !lit.replace(/\s/g, '')) return rem;
    if (rem.indexOf(lit) === 0) return rem.slice(lit.length);
    var r0 = rem.replace(/^\s+/, '');
    var l0 = lit.replace(/^\s+/, '');
    if (l0 && r0.indexOf(l0) === 0) {
      return rem.slice(rem.length - r0.length + l0.length);
    }
    var nr = normLiteralMatch(rem.replace(/^\s+/, ''));
    var nl = normLiteralMatch(lit.replace(/^\s+/, ''));
    if (nl && nr.indexOf(nl) === 0) {
      return rem.slice(rem.length - rem.replace(/^\s+/, '').length + nl.length);
    }
    return null;
  }

  var OPTIONAL_SUFFIX_RE = /^[\s.,;:!?)»"']+$/;
  var HYPHEN_LITERALS = { '-': true, '\u2013': true, '\u2014': true };
  var LONG_OPTIONAL_SUFFIX_LEN = 20;

  function sliceBeforeSuffix(rem, after) {
    if (!after) return rem.trim();
    if (rem.endsWith(after)) return rem.slice(0, rem.length - after.length).trim();
    var na = normLiteralMatch(after);
    var nr = normLiteralMatch(rem);
    if (na && nr.endsWith(na)) {
      return rem.slice(0, rem.length - (rem.endsWith(after) ? after.length : na.length)).trim();
    }
    if (OPTIONAL_SUFFIX_RE.test(after)) {
      var r = rem.replace(/\s+$/, '');
      var suf = after.replace(/^\s+|\s+$/g, '');
      while (suf.length) {
        if (r.endsWith(suf)) return r.slice(0, r.length - suf.length).trim();
        suf = suf.slice(0, -1);
      }
      return r.trim();
    }
    var tr = rem.replace(/\s+$/, '');
    var ta = after.replace(/\s+$/, '');
    if (tr.endsWith(ta)) return tr.slice(0, tr.length - ta.length).trim();
    return null;
  }

  function nextSignificantLiteral(L, startIdx) {
    for (var j = startIdx; j < L.length; j++) {
      if (L[j] && !HYPHEN_LITERALS[L[j]]) return L[j];
    }
    return '';
  }

  function parseReplCompactFallbackLine(firstLine, nSlots) {
    var user = letterTokenSpans(firstLine || '');
    if (user.length === nSlots) {
      var out = [];
      for (var i = 0; i < user.length; i++) out.push(user[i].orig);
      return out;
    }
    return null;
  }

  function tryHyphenPairWithoutChar(rem, nextLit) {
    var pos = findLiteral(rem, nextLit);
    if (pos < 0) return null;
    var words = rem.slice(0, pos).trim().split(/\s+/);
    if (words.length !== 2) return null;
    return { w0: words[0], w1: words[1], rest: rem.slice(pos) };
  }

  function parseFullLineByLiterals(fullRaw, L, nSlots) {
    if (!nSlots) return [];
    if (!L || L.length !== nSlots + 1) return null;
    var first = String(fullRaw).split(/\r?\n/)[0].replace(/\s+$/, '');
    var rem = consumeLiteralPrefix(first, L[0]);
    if (rem === null) return null;
    var out = [];
    var i = 0;
    while (i < nSlots) {
      if (i === nSlots - 1) {
        var afterLast = L[nSlots] || '';
        var val = sliceBeforeSuffix(rem, afterLast);
        if (val === null && afterLast.replace(/^\s+|\s+$/g, '').length >= LONG_OPTIONAL_SUFFIX_LEN) {
          val = rem.replace(/^\s+|\s+$/g, '');
        }
        if (val === null) return null;
        out.push(val);
        break;
      }
      var nextLit = L[i + 1];
      if (HYPHEN_LITERALS[nextLit]) {
        var hpos = findLiteral(rem, nextLit);
        if (hpos >= 0) {
          out.push(rem.slice(0, hpos).trim());
          rem = skipLiteral(rem.slice(hpos), nextLit);
          i += 1;
          continue;
        }
        var sigLit = nextSignificantLiteral(L, i + 2);
        if (!sigLit) return null;
        var pair = tryHyphenPairWithoutChar(rem, sigLit);
        if (!pair) return null;
        out.push(pair.w0);
        out.push(pair.w1);
        rem = pair.rest;
        i += 2;
        continue;
      }
      if (nextLit === '') return null;
      var pos = findLiteral(rem, nextLit);
      if (pos < 0) return null;
      out.push(rem.slice(0, pos).trim());
      rem = skipLiteral(rem.slice(pos), nextLit);
      i += 1;
    }
    if (out.length !== nSlots) return null;
    return out;
  }

  function splitWholeLines(text) {
    if (text == null || text === '') return [];
    return text.split(/\r?\n/);
  }

  function parseReplLineAnswersSmart(container, raw, nSlots) {
    if (nSlots <= 0) return [];
    raw = normPasteText(raw);
    if (!String(raw).replace(/\s/g, '')) return null;
    var firstLine = firstNonEmptyLineInRaw(raw).replace(/\s+$/, '');
    var tabbed = parseReplTabLine(firstLine, nSlots);
    if (tabbed) return tabbed;
    var mixed = parseReplMixedQuotedLine(firstLine, nSlots);
    if (mixed) return mixed;
    var qd = parseReplQuotedDoubleLine(firstLine, nSlots);
    if (qd) return qd;
    var qg = parseReplGuillemetLine(firstLine, nSlots);
    if (qg) return qg;
    if (container) {
      var ex = extractReplWordsLiterals(container);
      if (ex.nSlots === nSlots) {
        var tok = parseReplTokenLine(firstLine, ex.literals, nSlots);
        if (tok) return tok;
      }
    }
    return parseReplCompactFallbackLine(firstLine, nSlots);
  }

  var api = {
    splitWholeLines: splitWholeLines,
    expandQuotedChunksByWhitespace: expandQuotedChunksByWhitespace,
    normPasteText: normPasteText,
    normalizeQuotesLine: normalizeQuotesLine,
    firstNonEmptyLineInRaw: firstNonEmptyLineInRaw,
    parseReplTabLine: parseReplTabLine,
    parseReplMixedQuotedLine: parseReplMixedQuotedLine,
    parseReplQuotedDoubleLine: parseReplQuotedDoubleLine,
    parseReplGuillemetLine: parseReplGuillemetLine,
    extractReplWordsLiterals: extractReplWordsLiterals,
    letterTokenSpans: letterTokenSpans,
    templateTokenPattern: templateTokenPattern,
    alignAnswersToTemplate: alignAnswersToTemplate,
    parseReplTokenLine: parseReplTokenLine,
    parseFullLineByLiterals: parseFullLineByLiterals,
    sliceBeforeSuffix: sliceBeforeSuffix,
    parseReplLineAnswersSmart: parseReplLineAnswersSmart,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.ReplacementsInputParse = api;
})(typeof window !== 'undefined' ? window : global);
