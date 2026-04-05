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

  function consumeLiteralPrefix(rem, lit) {
    if (!lit || !lit.replace(/\s/g, '')) return rem;
    if (rem.indexOf(lit) === 0) return rem.slice(lit.length);
    var r0 = rem.replace(/^\s+/, '');
    var l0 = lit.replace(/^\s+/, '');
    if (l0 && r0.indexOf(l0) === 0) {
      return rem.slice(rem.length - r0.length + l0.length);
    }
    return null;
  }

  function parseFullLineByLiterals(fullRaw, L, nSlots) {
    if (!nSlots) return [];
    if (!L || L.length !== nSlots + 1) return null;
    var first = String(fullRaw).split(/\r?\n/)[0].replace(/\s+$/, '');
    var rem = consumeLiteralPrefix(first, L[0]);
    if (rem === null) return null;
    var out = [];
    for (var i = 0; i < nSlots; i++) {
      if (i === nSlots - 1) {
        var after = L[nSlots] || '';
        if (after) {
          var val;
          if (rem.endsWith(after)) {
            val = rem.slice(0, rem.length - after.length);
          } else {
            var tr = rem.trimEnd();
            var ta = after.trimEnd();
            if (!tr.endsWith(ta)) return null;
            val = tr.slice(0, tr.length - ta.length);
          }
          out.push(val.trim());
        } else {
          out.push(rem.trim());
        }
        break;
      }
      var nextLit = L[i + 1];
      if (nextLit === '') return null;
      var pos = rem.indexOf(nextLit);
      if (pos < 0) return null;
      out.push(rem.slice(0, pos).trim());
      rem = rem.slice(pos + nextLit.length);
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
    if (!container) return null;
    var ex = extractReplWordsLiterals(container);
    if (ex.nSlots !== nSlots) return null;
    return parseFullLineByLiterals(firstLine, ex.literals, nSlots);
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
    parseFullLineByLiterals: parseFullLineByLiterals,
    parseReplLineAnswersSmart: parseReplLineAnswersSmart,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.ReplacementsInputParse = api;
})(typeof window !== 'undefined' ? window : global);
