# Дублирует логику static/js/replacements_input_parse.js для тестов и (при необходимости) сервера.
# При изменении правил разбора обновляйте оба файла.
# Тесты: games/tests/test_replacements_input_parse.py

import re


def norm_paste_text(s):
    return (s or '').replace('\u00a0', ' ').replace('\r\n', '\n')


def normalize_quotes_line(s):
    t = norm_paste_text(s)
    for a, b in (
        ('\u201c', '"'),
        ('\u201d', '"'),
        ('\u201e', '"'),
        ('\u2033', '"'),
        ('\u00ab', '«'),
        ('\u00bb', '»'),
    ):
        t = t.replace(a, b)
    return t


def first_non_empty_line_in_raw(raw):
    for ln in norm_paste_text(str(raw)).split('\n'):
        if ln.strip():
            return ln
    return ''


def parse_repl_tab_line(raw, n_slots):
    n = int(n_slots) if n_slots else 0
    raw = (raw or '').strip()
    if n <= 0:
        return []
    if not raw:
        return None
    if n == 1:
        return [raw]
    tab = raw.split('\t')
    if len(tab) == n:
        return [x.strip() for x in tab]
    semi = re.split(r'\s*;\s*', raw)
    if len(semi) == n:
        return [x.strip() for x in semi]
    return None


def expand_quoted_chunks_by_whitespace(chunks, n_slots):
    """Несколько кавычечных групп → плоский список по пробелам (4 группы → 11 слотов)."""
    n = int(n_slots) if n_slots else 0
    if not chunks or n <= 0:
        return None
    flat = []
    for seg in chunks:
        seg = (seg or '').strip()
        if not seg:
            continue
        for t in seg.split():
            t = t.strip()
            if not t:
                continue
            t = re.sub(r'\?+$', '', t)
            t = re.sub(r'!+$', '', t)
            flat.append(t)
    if len(flat) == n:
        return flat
    if len(flat) > n:
        return flat[:n]
    return None


def parse_repl_mixed_quoted_line(line, n_slots):
    n = int(n_slots) if n_slots else 0
    line = normalize_quotes_line(str(line))
    if not line.strip():
        return None
    out = []
    i = 0
    ln = len(line)
    while i < ln and len(out) < n + 24:
        while i < ln and line[i].isspace():
            i += 1
        if i >= ln:
            break
        c = line[i]
        if c == '"':
            i += 1
            buf = []
            while i < ln:
                if line[i] == '\\' and i + 1 < ln:
                    buf.append(line[i + 1])
                    i += 2
                    continue
                if line[i] == '"':
                    break
                buf.append(line[i])
                i += 1
            if i < ln and line[i] == '"':
                i += 1
            out.append(''.join(buf).strip())
        elif c == '«':
            i += 1
            buf = []
            while i < ln and line[i] != '»':
                buf.append(line[i])
                i += 1
            if i < ln and line[i] == '»':
                i += 1
            out.append(''.join(buf).strip())
        else:
            i += 1
    if len(out) >= n:
        return out[:n]
    expanded = expand_quoted_chunks_by_whitespace(out, n)
    if expanded is not None:
        return expanded
    return None


def parse_repl_quoted_double_line(raw, n_slots):
    n = int(n_slots) if n_slots else 0
    line = normalize_quotes_line(str(raw)).split('\n')[0]
    if not line.strip():
        return None
    if n == 1:
        t = line.strip()
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            return [t[1:-1].strip()]
        return None
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', line):
        inner = re.sub(r'\\(.)', r'\1', m.group(1)).strip()
        out.append(inner)
    if len(out) == n:
        return out
    if len(out) > n:
        return out[:n]
    exp = expand_quoted_chunks_by_whitespace(out, n)
    if exp is not None:
        return exp
    return None


def parse_repl_guillemet_line(raw, n_slots):
    n = int(n_slots) if n_slots else 0
    line = normalize_quotes_line(str(raw)).split('\n')[0]
    if not line.strip():
        return None
    if n == 1:
        m = re.match(r'^\s*«([^»]*)»\s*$', line)
        if m:
            return [m.group(1).strip()]
        return None
    out = re.findall(r'«([^»]*)»', line)
    out = [x.strip() for x in out]
    if len(out) == n:
        return out
    if len(out) > n:
        return out[:n]
    exp2 = expand_quoted_chunks_by_whitespace(out, n)
    if exp2 is not None:
        return exp2
    return None


def parse_repl_line_answers_smart_no_dom(raw, n_slots):
    """Без разбора по литералам из DOM (только таб/кавычки/«»)."""
    if n_slots <= 0:
        return []
    raw = norm_paste_text(raw)
    if not re.sub(r'\s', '', raw):
        return None
    first_line = first_non_empty_line_in_raw(raw).rstrip()
    tabbed = parse_repl_tab_line(first_line, n_slots)
    if tabbed is not None:
        return tabbed
    mixed = parse_repl_mixed_quoted_line(first_line, n_slots)
    if mixed is not None:
        return mixed
    qd = parse_repl_quoted_double_line(first_line, n_slots)
    if qd is not None:
        return qd
    qg = parse_repl_guillemet_line(first_line, n_slots)
    if qg is not None:
        return qg
    return None
