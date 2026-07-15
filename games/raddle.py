# Парсинг и UI-состояние заданий типа «raddle» (лестница слов с подсказками, как raddle.quest).
#
# checker_data (JSON):
#   {
#     "lengths": [5, 9, 7, "5-9", "5 3", ...],   # int или str ("5", "5-9", "5 3")
#     "hints": ["Житель ____а", "____ без двух первых букв", ...],
#     # Ровно len(lengths)-1 подсказок: hints[i] — переход words[i] → words[i+1].
#     # В тексте: ____ / {prev}/{word}/{from} = предыдущее слово;
#     #           ... / … / {next} = следующее слово.
#     # Если слота для следующего нет — при известном next дописывается « → NEXT».
#     "raddle_assist": { "enabled": true, "fractions": [1, 0.4, 0] }
#     # In-game 💡: Hint.desc = raddle_clue:N / raddle_answer:N (points_penalty=0, баллы через fractions)
#   }
#
# Первое и последнее слово показываются сразу; игрок может сдавать только первое и
# последнее из ещё нерешённых. При верном ответе подсказка между словами уходит в «использованные».

import json
import re

from games.util import clean_text

_LENGTH_PARTS_HYPHEN = re.compile(r'^(\d+)\s*-\s*(\d+)$')
_LENGTH_PARTS_SPACE = re.compile(r'^(\d+)\s+(\d+)$')
_RADDLE_ASSIST_CLUE_DESC = re.compile(r'^raddle_clue:(\d+)$', re.I)
_RADDLE_ASSIST_ANSWER_DESC = re.compile(r'^raddle_answer:(\d+)$', re.I)
_RADDLE_LETTER_RE = re.compile(r'[^0-9a-zа-яё]', re.I)
_RADDLE_IS_LETTER_RE = re.compile(r'^[0-9a-zа-яё]$', re.I)
RADDLE_INPUT_FORMAT_SLOT = '#'


def raddle_word_core(word):
    """Буквы и цифры без пробелов, дефисов и прочей пунктуации (для сравнения и длины)."""
    return clean_text(_RADDLE_LETTER_RE.sub('', str(word or '')))


def raddle_input_format(canonical_word=None, mask=None):
    """
    Шаблон ввода для клиента: # — слот буквы, остальное — литералы (дефис, пробел…).
    Структура ответа без раскрытия букв.
    """
    if canonical_word:
        return ''.join(
            RADDLE_INPUT_FORMAT_SLOT
            if _RADDLE_IS_LETTER_RE.match(ch)
            else ch
            for ch in canonical_word
        )
    if mask and mask.get('type') == 'parts':
        a, b = mask['parts']
        sep = mask.get('sep', '-')
        return (
            (RADDLE_INPUT_FORMAT_SLOT * a) + sep
            + (RADDLE_INPUT_FORMAT_SLOT * b)
        )
    if mask and mask.get('type') == 'fixed':
        return RADDLE_INPUT_FORMAT_SLOT * mask['length']
    return RADDLE_INPUT_FORMAT_SLOT * 5

DEFAULT_RADDLE_ASSIST_FRACTIONS = [1, 0.5, 0]


def parse_length_mask(value):
    """Разбор маски длины: число, «a-b» (дефис) или «a b» (пробел)."""
    if isinstance(value, bool):
        return {'type': 'unknown', 'label': str(value)}
    if isinstance(value, int):
        return {'type': 'fixed', 'length': value, 'label': str(value)}
    s = str(value).strip()
    if re.match(r'^\d+$', s):
        n = int(s)
        return {'type': 'fixed', 'length': n, 'label': s}
    m = _LENGTH_PARTS_HYPHEN.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return {'type': 'parts', 'parts': (a, b), 'sep': '-', 'label': '{}-{}'.format(a, b)}
    m = _LENGTH_PARTS_SPACE.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return {'type': 'parts', 'parts': (a, b), 'sep': ' ', 'label': '{} {}'.format(a, b)}
    return {'type': 'unknown', 'label': s}


def length_label_from_word(word):
    """Подпись длины по структуре слова: «РОБИН ГУД» → «5 3», «САНКТ-ПЕТЕРБУРГ» → «5-9»."""
    out = []
    n = 0
    for ch in str(word or ''):
        if _RADDLE_IS_LETTER_RE.match(ch):
            n += 1
        else:
            if n:
                out.append(str(n))
                n = 0
            out.append(ch)
    if n:
        out.append(str(n))
    return ''.join(out) if out else '0'


def split_word_alternatives(raw):
    parts = [p.strip() for p in str(raw or '').split('|')]
    parts = [p for p in parts if p]
    if not parts:
        return '', ['']
    return parts[0], parts


def word_length_matches(word, mask):
    core = raddle_word_core(word)
    if mask['type'] == 'fixed':
        return len(core) == mask['length']
    if mask['type'] == 'parts':
        a, b = mask['parts']
        return len(core) == a + b
    return True


_MASK_SQUARE = '◼️'


def length_mask_display(mask, canonical_word=None):
    if canonical_word:
        return raddle_input_format(canonical_word).replace(
            RADDLE_INPUT_FORMAT_SLOT, _MASK_SQUARE
        )
    if mask['type'] == 'parts':
        a, b = mask['parts']
        sep = mask.get('sep', '-')
        return (_MASK_SQUARE * a) + sep + (_MASK_SQUARE * b)
    if mask['type'] == 'fixed':
        return _MASK_SQUARE * mask['length']
    return _MASK_SQUARE * 5


def parse_raddle_data(task):
    """
    Возвращает dict с keys: lengths, hints, words, word_accept, masks, n_words
    или None при ошибке разбора.
    """
    if getattr(task, 'task_type', None) != 'raddle':
        return None
    raw = (getattr(task, 'checker_data', None) or '').strip()
    data = {}
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
    lengths_raw = data.get('lengths')
    hints = data.get('hints')
    words_raw = data.get('words')
    if lengths_raw is None or hints is None:
        return None
    if not isinstance(lengths_raw, list) or not isinstance(hints, list):
        return None
    if words_raw is None:
        answer = (getattr(task, 'answer', None) or '').strip()
        if not answer:
            return None
        words_raw = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    if not isinstance(words_raw, list) or not words_raw:
        return None
    n = len(words_raw)
    if len(lengths_raw) != n:
        return None
    if len(hints) != max(0, n - 1):
        return None
    words = []
    word_accept = []
    for w in words_raw:
        canon, opts = split_word_alternatives(w)
        words.append(canon)
        word_accept.append(opts)
    masks = [parse_length_mask(x) for x in lengths_raw]
    assist = _parse_raddle_assist_block(data.get('raddle_assist'))
    return {
        'lengths': lengths_raw,
        'hints': list(hints),
        'words': words,
        'word_accept': word_accept,
        'masks': masks,
        'n_words': n,
        'assist': assist,
    }


def _parse_raddle_assist_block(raw):
    if not isinstance(raw, dict):
        return {'enabled': True, 'fractions': list(DEFAULT_RADDLE_ASSIST_FRACTIONS)}
    fractions = raw.get('fractions', DEFAULT_RADDLE_ASSIST_FRACTIONS)
    if not isinstance(fractions, list) or len(fractions) < 3:
        fractions = list(DEFAULT_RADDLE_ASSIST_FRACTIONS)
    try:
        fractions = [float(x) for x in fractions[:3]]
    except (TypeError, ValueError):
        fractions = list(DEFAULT_RADDLE_ASSIST_FRACTIONS)
    enabled = raw.get('enabled', True)
    return {'enabled': bool(enabled), 'fractions': fractions}


def is_raddle_in_game_assist_hint(hint):
    desc = (getattr(hint, 'desc', None) or '').strip()
    return bool(
        _RADDLE_ASSIST_CLUE_DESC.match(desc) or _RADDLE_ASSIST_ANSWER_DESC.match(desc)
    )


def parse_raddle_assist_desc(desc):
    """(word_index, tier) где tier: 1=clue, 2=answer; или None."""
    desc = (desc or '').strip()
    m = _RADDLE_ASSIST_CLUE_DESC.match(desc)
    if m:
        return int(m.group(1)), 1
    m = _RADDLE_ASSIST_ANSWER_DESC.match(desc)
    if m:
        return int(m.group(1)), 2
    return None


def find_raddle_assist_hint(task, word_index, tier):
    """Hint с desc raddle_clue:N / raddle_answer:N, если автор завёл в админке."""
    want = 'raddle_clue:{}'.format(word_index) if tier == 1 else 'raddle_answer:{}'.format(word_index)
    for hint in task.get_hints():
        if (hint.desc or '').strip().lower() == want.lower():
            return hint
    return None


def ensure_raddle_assist_hints(task):
    """
    Создаёт Hint для in-game 💡 / 💡💡 (desc=raddle_clue:N / raddle_answer:N).
    Нужны для HintAttempt и штрафов; в панели игрока не показываются (get_player_hints).
    """
    from games.models import Hint

    if getattr(task, 'task_type', None) != 'raddle':
        return 0
    parsed = parse_raddle_data(task)
    if not parsed:
        return 0
    n = parsed['n_words']
    if n < 3:
        return 0
    existing = {
        (h.desc or '').strip().lower(): h
        for h in Hint.objects.filter(task=task)
        if (h.desc or '').strip()
    }
    created = 0
    for wi in range(1, n - 1):
        for tier, prefix in (
            (1, 'raddle_clue'),
            (2, 'raddle_answer'),
        ):
            desc = '{}:{}'.format(prefix, wi)
            key = desc.lower()
            if key in existing:
                continue
            Hint.objects.create(
                task=task,
                number='{}.{}'.format(tier, wi),
                desc=desc,
                text='',
                points_penalty=0,
            )
            created += 1
    return created


def word_solve_credit(tier, assist_config):
    from decimal import Decimal
    fractions = (assist_config or {}).get('fractions', DEFAULT_RADDLE_ASSIST_FRACTIONS)
    idx = max(0, min(int(tier or 0), len(fractions) - 1))
    try:
        return Decimal(str(fractions[idx]))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal(str(DEFAULT_RADDLE_ASSIST_FRACTIONS[idx]))


def resolve_assist_tiers(state, hint_attempts=None):
    """word_index → tier (0..2) из chain state и HintAttempt."""
    tiers = {}
    raw = state.get('assist_tier') or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                tiers[int(k)] = max(0, min(2, int(v)))
            except (TypeError, ValueError):
                pass
    for ha in hint_attempts or []:
        if not getattr(ha, 'is_real_request', True):
            continue
        hint = getattr(ha, 'hint', None)
        if hint is None:
            continue
        parsed = parse_raddle_assist_desc(hint.desc)
        if parsed is None:
            continue
        wi, tier = parsed
        tiers[wi] = max(tiers.get(wi, 0), tier)
    return tiers


def apply_assist_tier(state, word_index, tier):
    state = dict(state)
    assist = dict(state.get('assist_tier') or {})
    key = str(int(word_index))
    assist[key] = max(int(assist.get(key, 0)), int(tier))
    state['assist_tier'] = assist
    return state


def validate_raddle_checker_data(raw_text, answer_text=None):
    """Список строк-ошибок для админки (пустой = ок)."""
    errors = []
    if not (raw_text or '').strip():
        errors.append('checker_data пустой')
        return errors
    try:
        data = json.loads(raw_text)
    except (ValueError, TypeError) as e:
        errors.append('checker_data не JSON: {}'.format(e))
        return errors
    if not isinstance(data, dict):
        errors.append('checker_data должен быть JSON-объектом')
        return errors
    lengths = data.get('lengths')
    hints = data.get('hints')
    words = data.get('words')
    n_words = None
    if isinstance(words, list) and words:
        n_words = len(words)
    elif isinstance(lengths, list) and lengths:
        n_words = len(lengths)
    elif (answer_text or '').strip():
        n_words = len([ln for ln in answer_text.splitlines() if ln.strip()])

    if not isinstance(lengths, list) or not lengths:
        errors.append('нет lengths (непустой список)')
    if not isinstance(hints, list):
        errors.append(
            'нет hints (список): подсказок должно быть на 1 меньше, чем слов '
            '(каждая — переход от слова к следующему)'
        )
    elif n_words is not None and len(hints) != max(0, n_words - 1):
        errors.append(
            'число подсказок (hints) должно быть на 1 меньше числа слов '
            '(сейчас подсказок {}, слов {}; каждая подсказка — переход '
            'от слова к следующему)'.format(len(hints), n_words)
        )
    if isinstance(hints, list):
        for i, h in enumerate(hints):
            if not str(h or '').strip():
                errors.append(
                    'подсказка #{} пустая (hints[{}] — переход '
                    'words[{}] → words[{}])'.format(i + 1, i, i, i + 1)
                )
    if isinstance(lengths, list) and isinstance(words, list) and words and len(lengths) != len(words):
        errors.append('lengths и words разной длины ({} и {})'.format(len(lengths), len(words)))
    if words is None and not (answer_text or '').strip():
        errors.append('нет words в JSON и пустой answer')
    assist = data.get('raddle_assist')
    if assist is not None and not isinstance(assist, dict):
        errors.append('raddle_assist должен быть объектом')
    elif isinstance(assist, dict) and 'fractions' in assist:
        fr = assist.get('fractions')
        if not isinstance(fr, list) or len(fr) < 3:
            errors.append('raddle_assist.fractions — список из 3 чисел')
    return errors


def default_raddle_state(n_words):
    if n_words < 2:
        solved = list(range(n_words))
    else:
        solved = [0, n_words - 1]
    return {'solved_indices': sorted(solved), 'used_hints': [], 'assist_tier': {}, 'total': 0.0}


def load_raddle_state(raw_state, n_words):
    if not raw_state:
        return default_raddle_state(n_words)
    try:
        st = json.loads(raw_state)
    except (ValueError, TypeError):
        return default_raddle_state(n_words)
    solved = sorted(set(int(i) for i in (st.get('solved_indices') or [])))
    if n_words >= 2:
        base = {0, n_words - 1}
        solved = sorted(set(solved) | base)
    used = sorted(set(int(i) for i in (st.get('used_hints') or [])))
    try:
        total = float(st.get('total', 0) or 0)
    except (TypeError, ValueError):
        total = 0.0
    assist_tier = st.get('assist_tier') or {}
    if not isinstance(assist_tier, dict):
        assist_tier = {}
    return {
        'solved_indices': solved,
        'used_hints': used,
        'assist_tier': assist_tier,
        'total': total,
    }


def unsolved_indices(state, n_words):
    solved = set(state.get('solved_indices') or [])
    return [i for i in range(n_words) if i not in solved]


def playable_word_indices(state, n_words):
    open_idx = unsolved_indices(state, n_words)
    if not open_idx:
        return set()
    return {open_idx[0], open_idx[-1]}


def hint_index_for_word(word_index):
    """Подсказка между word_index-1 и word_index (переход сверху вниз)."""
    if word_index <= 0:
        return None
    return word_index - 1


def clue_index_for_playable_word(word_index, solved, n_words):
    """
    Подсказка, открывающая playable-слово:
      верхний край (известно предыдущее) → hints[word_index - 1] (prev → слово);
      нижний край (известно следующее) → hints[word_index] (слово → next).
    """
    if word_index is None or word_index <= 0 or word_index >= n_words - 1:
        return None
    solved = set(solved or [])
    if (word_index - 1) in solved:
        return word_index - 1
    if (word_index + 1) in solved:
        return word_index
    return None


def word_matches(user_word, accept_list):
    u = raddle_word_core(user_word)
    return any(raddle_word_core(opt) == u for opt in accept_list)


def mask_slot_count(mask, canonical_word=None):
    """Сколько букв нужно ввести (для ширины инпута / maxlength)."""
    if canonical_word:
        return len(raddle_word_core(canonical_word))
    if mask['type'] == 'fixed':
        return mask['length']
    if mask['type'] == 'parts':
        a, b = mask['parts']
        return a + b
    return 12


def input_size_for_mask(mask, canonical_word=None):
    """Атрибут size для поля ввода (как у replacements_lines)."""
    length = mask_slot_count(mask, canonical_word)
    # size считают в «средних» буквах; плейсхолдер ◼️ шире, поэтому чуть с запасом
    if length <= 4:
        return max(4, length * 2)
    if length <= 8:
        return max(8, length * 2)
    if length <= 12:
        return max(12, length * 2)
    return max(16, length * 2)


def raddle_word_solved_list(parsed, attempts=None):
    """Какие слова лестницы считаются решёнными (по chain state из попыток)."""
    n = parsed['n_words']
    state = load_raddle_state(None, n)
    for a in reversed(attempts or []):
        raw = getattr(a, 'state', None)
        if raw:
            state = load_raddle_state(raw, n)
            break
    solved = set(state.get('solved_indices') or [])
    return [i in solved for i in range(n)]


_CLUE_PREV_PLACEHOLDER_RE = re.compile(r'\{(prev|word|from)\}', re.IGNORECASE)
_CLUE_NEXT_PLACEHOLDER_RE = re.compile(r'\{next\}', re.IGNORECASE)
_CLUE_BLANK_RE = re.compile(r'_{2,}')
_CLUE_BLANK_TOKEN = '____'
_CLUE_NEXT_TOKEN = '...'
_CLUE_REPEATED_BLANK_RE = re.compile(r'____(?:\s*____)+')
_CLUE_ELLIPSIS_RE = re.compile(r'\.{3}|…')


def clue_has_next_slot(hint_text):
    """
    Есть ли слот следующего слова (... / … / {next}).
    Любое вхождение «...» считается слотом (подставляем следующее слово во все).
    """
    text = str(hint_text or '')
    if _CLUE_NEXT_PLACEHOLDER_RE.search(text):
        return True
    return bool(_CLUE_ELLIPSIS_RE.search(text))


def clue_blank_template(hint_text):
    """
    Нормализует плейсхолдеры:
      {prev}/{word}/{from} и подряд идущие _ → ____
      {next} → ...
    """
    text = str(hint_text or '')
    text = _CLUE_PREV_PLACEHOLDER_RE.sub(_CLUE_BLANK_TOKEN, text)
    text = _CLUE_NEXT_PLACEHOLDER_RE.sub(_CLUE_NEXT_TOKEN, text)
    text = _CLUE_BLANK_RE.sub(_CLUE_BLANK_TOKEN, text)
    text = _CLUE_REPEATED_BLANK_RE.sub(_CLUE_BLANK_TOKEN, text)
    return text


_CLUE_KIND_CLASS = {
    'ref': 'new-raddle-clue-ref',
    'next': 'new-raddle-clue-next',
    'before': 'new-raddle-clue-before',
    'focus': 'new-raddle-clue-focus',
    'after': 'new-raddle-clue-after',
}


def _clue_ref_html(word, *, kind='ref'):
    """
    kind=ref — жёлтый; kind=next — зелёный (legacy / used).
    Финал лесенки: before=зелёный (А), focus=жёлтый (Б), after=оранжевый (В).
    """
    from django.utils.html import escape
    cls = _CLUE_KIND_CLASS.get(kind, 'new-raddle-clue-ref')
    return '<strong class="{}">{}</strong>'.format(cls, escape(word))


def render_transition_clue(
    hint_text,
    prev_word='',
    next_word='',
    *,
    prev_known=False,
    next_known=False,
    html=False,
    next_as_solved=False,
    prev_kind='ref',
    next_kind=None,
):
    """
    Подсказка перехода prev → next.
    ____ = предыдущее; ... / … / {next} = следующее.
    Если слота следующего нет, а next известен — дописываем « → NEXT».
    next_as_solved: подсветить следующее слово зелёным (для блока «Использованные»).
    """
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    template = clue_blank_template(hint_text)
    has_next_slot = clue_has_next_slot(hint_text)

    if html:
        out = escape(template)
    else:
        out = template

    if prev_known and prev_word:
        pk = prev_kind if prev_kind != 'ref' else 'ref'
        repl = _clue_ref_html(prev_word, kind=pk) if html else str(prev_word)
        out = _CLUE_BLANK_RE.sub(repl, out)

    if next_known and next_word:
        if next_kind is None:
            next_kind = 'next' if next_as_solved else 'ref'
        repl = _clue_ref_html(next_word, kind=next_kind) if html else str(next_word)
        if has_next_slot:
            out = _CLUE_ELLIPSIS_RE.sub(repl, out)
        else:
            out = out.rstrip()
            if out.endswith('.'):
                out = out[:-1].rstrip()
            if html:
                out = '{} → {}'.format(out, _clue_ref_html(next_word, kind=next_kind))
            else:
                out = '{} → {}'.format(out, next_word)

    return mark_safe(out) if html else out


def substitute_clue_word(hint_text, word, *, html=False):
    """Обратная совместимость: подставляет word только в ____ (prev)."""
    return render_transition_clue(
        hint_text, prev_word=word or '', prev_known=bool(word), html=html,
    )


def render_raddle_clue(hint_text, prev_word, prev_solved=True):
    """Подстановка предыдущего слова в ____ / {prev}."""
    return render_transition_clue(
        hint_text,
        prev_word=prev_word or '',
        prev_known=bool(prev_solved and prev_word),
    )


def clue_display_for_hint(
    hint_text, hint_index, words, solved, *, focus_ref_word=None, focus_ref_role=None, html=False,
):
    """
    Неиспользованная подсказка: не подставляем реально отгаданные слова перехода
    (это спойлер). При фокусе на playable подставляем известного соседа во все подсказки:
      role=prev → в ____ (жёлтый);
      role=next → в ... / «→» (зелёный).
    """
    if focus_ref_word and focus_ref_role == 'next':
        return render_transition_clue(
            hint_text,
            next_word=focus_ref_word,
            next_known=True,
            html=html,
            next_as_solved=True,
        )
    if focus_ref_word:
        return render_transition_clue(
            hint_text,
            prev_word=focus_ref_word,
            prev_known=True,
            html=html,
        )
    return render_transition_clue(hint_text, html=html)


def used_clue_display(hint_text, hint_index, words, *, html=False):
    """
    Использованная подсказка: оба слова известны.
    Слово после «→» (и в слоте следующего) — зелёным.
    """
    if hint_index < 0 or hint_index + 1 >= len(words):
        return render_transition_clue(hint_text, html=html)
    return render_transition_clue(
        hint_text,
        prev_word=words[hint_index],
        next_word=words[hint_index + 1],
        prev_known=True,
        next_known=True,
        html=html,
        next_as_solved=True,
    )



def reference_word_for_playable(word_index, solved, n_words):
    """Соседнее уже решённое слово для playable index. Возвращает index или None."""
    if word_index > 0 and (word_index - 1) in solved:
        return word_index - 1
    if word_index + 1 < n_words and (word_index + 1) in solved:
        return word_index + 1
    return None


def reference_role_for_playable(word_index, solved, n_words):
    """
    Роль известного соседа в паре перехода к playable-слову:
      'prev' — сосед сверху (переход prev → слово);
      'next' — сосед снизу (переход слово → next).
    """
    if word_index > 0 and (word_index - 1) in solved:
        return 'prev'
    if word_index + 1 < n_words and (word_index + 1) in solved:
        return 'next'
    return None


def both_neighbors_solved(word_index, solved, n_words):
    """У playable-слова известны и верхний, и нижний сосед (осталось одно слово)."""
    if word_index is None or word_index <= 0 or word_index >= n_words - 1:
        return False
    solved = set(solved or [])
    return (word_index - 1) in solved and (word_index + 1) in solved


def last_word_role_for_index(word_index, focus_index, *, dual_clues):
    """
    Роль строки в финале (А — зелёный, Б — жёлтый, В — оранжевый).
    """
    if not dual_clues or focus_index is None:
        return ''
    if word_index == focus_index - 1:
        return 'before'
    if word_index == focus_index:
        return 'focus'
    if word_index == focus_index + 1:
        return 'after'
    return ''


def render_last_word_transition_clue(hint_text, before_word, focus_word, after_word, *, pair):
    """
    Подсказка с подстановкой А+Б или Б+В и цветами before/focus/after.
    pair='ab' → А (зелёный) + Б (жёлтый); pair='bc' → Б (жёлтый) + В (оранжевый).
    """
    if pair == 'ab':
        return render_transition_clue(
            hint_text,
            prev_word=before_word,
            next_word=focus_word,
            prev_known=True,
            next_known=True,
            html=True,
            prev_kind='before',
            next_kind='focus',
        )
    return render_transition_clue(
        hint_text,
        prev_word=focus_word,
        next_word=after_word,
        prev_known=True,
        next_known=True,
        html=True,
        prev_kind='focus',
        next_kind='after',
    )


def build_last_word_clue_options(parsed, focus_index, *, revealed_clue_indices):
    """
    Два варианта расстановки двух оставшихся подсказок:
      1) А+Б, затем Б+В
      2) Б+В, затем А+Б
    """
    words = parsed['words']
    hints = parsed['hints']
    before_word = words[focus_index - 1]
    focus_word = words[focus_index]
    after_word = words[focus_index + 1]
    hint_ab_idx = focus_index - 1
    hint_bc_idx = focus_index
    hint_ab = hints[hint_ab_idx]
    hint_bc = hints[hint_bc_idx]

    def _item(hint_index, hint_text, pair):
        return {
            'index': hint_index,
            'text': hint_text,
            'pair': pair,
            'is_revealed': hint_index in revealed_clue_indices,
            'display_html': render_last_word_transition_clue(
                hint_text, before_word, focus_word, after_word, pair=pair,
            ),
        }

    return [
        {
            'id': 'ab-bc',
            'hints': [
                _item(hint_ab_idx, hint_ab, 'ab'),
                _item(hint_bc_idx, hint_bc, 'bc'),
            ],
        },
        {
            'id': 'bc-ab',
            'hints': [
                _item(hint_bc_idx, hint_bc, 'bc'),
                _item(hint_ab_idx, hint_ab, 'ab'),
            ],
        },
    ]


def raddle_result_squares(parsed, state, *, hint_attempts=None):
    """
    Wordle-style строка для завершённой лесенки: по квадрату на среднее слово.
    1 балл → 🟩, 0.5 → 🟨, 0 → 🟥.
    """
    n = parsed['n_words']
    middle_total = max(0, n - 2)
    if middle_total == 0:
        return ''
    solved = set(state.get('solved_indices') or [])
    if len(solved) < n:
        return ''
    assist_tiers = resolve_assist_tiers(state, hint_attempts)
    squares = []
    for i in range(1, n - 1):
        tier = assist_tiers.get(i, 0)
        if tier >= 2:
            squares.append('🟥')
        elif tier == 1:
            squares.append('🟨')
        else:
            squares.append('🟩')
    return ''.join(squares)


def raddle_result_squares_for_actor(
    task,
    *,
    team=None,
    user=None,
    anon_key=None,
    mode='general',
    game=None,
    include_other_games=False,
):
    """Строка квадратов для решённого raddle-задания текущего актора."""
    from games.models import Attempt

    parsed = parse_raddle_data(task)
    if not parsed:
        return ''
    game_arg = None if include_other_games else game
    ai = Attempt.manager.get_attempts_info(
        team=team,
        task=task,
        mode=mode,
        user=user,
        anon_key=anon_key,
        game=game_arg,
    )
    if not ai.is_solved():
        return ''
    state = load_raddle_state(None, parsed['n_words'])
    if ai.attempts:
        for a in reversed(ai.attempts):
            if a.state:
                state = load_raddle_state(a.state, parsed['n_words'])
                break
    return raddle_result_squares(parsed, state, hint_attempts=ai.hint_attempts)


def build_raddle_ui_context(parsed, state, attempts=None, max_attempts=None, mode=None, hint_attempts=None):
    """Контекст для шаблона: строки лестницы, подсказки, попытки по словам."""
    n = parsed['n_words']
    assist_cfg = parsed.get('assist') or _parse_raddle_assist_block(None)
    assist_enabled = assist_cfg.get('enabled', True)
    solved = set(state.get('solved_indices') or [])
    playable = playable_word_indices(state, n)
    used_hints = set(state.get('used_hints') or [])
    assist_tiers = resolve_assist_tiers(state, hint_attempts)
    revealed_clue_indices = set()
    for wi, tier in assist_tiers.items():
        if tier >= 1:
            hi = clue_index_for_playable_word(wi, solved, n)
            if hi is not None:
                revealed_clue_indices.add(hi)
    tournament = mode == 'tournament'
    word_attempts = [0] * n
    for a in attempts or []:
        try:
            p = json.loads(a.text)
            idx = int(p.get('word_index', -1))
            if 0 <= idx < n:
                word_attempts[idx] += 1
        except (ValueError, TypeError):
            pass

    playable_sorted = sorted(i for i in range(n) if (i in playable) and (i not in solved))
    # По умолчанию фокус на верхнем playable (как на raddle.quest).
    default_focus = playable_sorted[0] if playable_sorted else None
    default_ref_idx = (
        reference_word_for_playable(default_focus, solved, n)
        if default_focus is not None else None
    )
    default_ref_word = (
        parsed['words'][default_ref_idx]
        if default_ref_idx is not None else ''
    )
    default_ref_role = (
        reference_role_for_playable(default_focus, solved, n)
        if default_focus is not None else None
    )
    # Одно оставшееся слово: оба соседа известны → каждую unused-подсказку
    # показываем в вариантах «до» (prev) и «после» (next).
    last_word_dual_clues = (
        default_focus is not None
        and len(playable_sorted) == 1
        and both_neighbors_solved(default_focus, solved, n)
    )

    rows = []
    for i in range(n):
        mask = parsed['masks'][i]
        is_solved = i in solved
        is_open = (not is_solved) and (i in playable)
        attempts_exhausted = (
            tournament
            and max_attempts is not None
            and word_attempts[i] >= max_attempts
        )
        is_playable = is_open and not attempts_exhausted
        tier = assist_tiers.get(i, 0)
        clue_hi = (
            clue_index_for_playable_word(i, solved, n) if is_playable else None
        )
        canon = parsed['words'][i]
        # Подпись и квадраты — по структуре ответа (пробел ≠ дефис), не по строке lengths.
        mask_html = length_mask_display(mask, canon).strip()
        length_label = length_label_from_word(canon) if canon else mask['label']
        ref_idx = reference_word_for_playable(i, solved, n) if is_playable else None
        ref_role = reference_role_for_playable(i, solved, n) if is_playable else None
        dual_neighbors = is_playable and both_neighbors_solved(i, solved, n)
        last_word_role = last_word_role_for_index(
            i, default_focus, dual_clues=last_word_dual_clues,
        )
        # Цвета пары: prev=жёлтый, next=зелёный (обычный режим)
        focus_pair_role = None
        ref_pair_role = None
        if not last_word_role:
            if is_playable and ref_role == 'prev':
                focus_pair_role = 'next'  # вводим следующее
                ref_pair_role = 'prev'
            elif is_playable and ref_role == 'next':
                focus_pair_role = 'prev'  # вводим предыдущее
                ref_pair_role = 'next'
        inp_fmt = raddle_input_format(canon, mask)
        rows.append({
            'index': i,
            'word': canon if is_solved else '',
            'mask_html': mask_html,
            'mask_placeholder': mask_html,
            'length_label': length_label,
            'mask_slots': mask_slot_count(mask, canon),
            'max_length': mask_slot_count(mask, canon),
            'input_format': inp_fmt,
            'input_format_len': len(inp_fmt),
            'input_size': input_size_for_mask(mask, canon),
            'is_solved': is_solved,
            'is_playable': is_playable,
            'attempts_exhausted': is_open and attempts_exhausted,
            'is_given': i == 0 or i == n - 1,
            'compact_hidden': False,
            'is_default_focus': default_focus is not None and i == default_focus,
            'is_default_ref': default_ref_idx is not None and i == default_ref_idx,
            'ref_word_index': ref_idx,
            'ref_word': parsed['words'][ref_idx] if ref_idx is not None else '',
            'ref_role': ref_role,
            'focus_pair_role': focus_pair_role,
            'ref_pair_role': ref_pair_role,
            'dual_neighbors': dual_neighbors,
            'last_word_role': last_word_role,
            'prev_neighbor_word': (
                parsed['words'][i - 1] if dual_neighbors else ''
            ),
            'next_neighbor_word': (
                parsed['words'][i + 1] if dual_neighbors else ''
            ),
            'attempts': word_attempts[i],
            'assist_tier': tier,
            'revealed_answer': canon if tier >= 2 and not is_solved else '',
            'clue_hint_index': clue_hi,
            # 💡 всегда видна на playable: первый раз берёт подсказку, далее только подсвечивает
            'show_clue_btn': assist_enabled and is_playable and clue_hi is not None,
            'can_clue_assist': (
                assist_enabled and is_playable and tier < 1 and clue_hi is not None
            ),
            'clue_already_taken': assist_enabled and is_playable and tier >= 1,
            'can_answer_assist': assist_enabled and is_playable and tier == 1,
        })

    unused = []
    used = []
    last_word_clue_options = []
    for hi, hint in enumerate(parsed['hints']):
        blank_tpl = clue_blank_template(hint)
        prev_w = parsed['words'][hi] if hi < n else ''
        next_w = parsed['words'][hi + 1] if hi + 1 < n else ''
        base = {
            'index': hi,
            'text': hint,
            'blank_template': blank_tpl,
            'prev_word': prev_w,
            'next_word': next_w,
            'prev_solved': hi in solved,
            'next_solved': (hi + 1) in solved,
            'has_next_slot': clue_has_next_slot(hint),
            'is_revealed': hi in revealed_clue_indices,
            'clue_variant': '',
        }
        if hi in used_hints:
            item = {
                **base,
                'display': used_clue_display(hint, hi, parsed['words']),
                'display_html': used_clue_display(hint, hi, parsed['words'], html=True),
            }
            used.append(item)
        elif not last_word_dual_clues:
            item = {
                **base,
                'display': clue_display_for_hint(
                    hint, hi, parsed['words'], solved,
                    focus_ref_word=default_ref_word or None,
                    focus_ref_role=default_ref_role,
                ),
                'display_html': clue_display_for_hint(
                    hint, hi, parsed['words'], solved,
                    focus_ref_word=default_ref_word or None,
                    focus_ref_role=default_ref_role,
                    html=True,
                ),
            }
            unused.append(item)
    if last_word_dual_clues:
        last_word_clue_options = build_last_word_clue_options(
            parsed, default_focus, revealed_clue_indices=revealed_clue_indices,
        )
    else:
        unused.sort(key=lambda x: x['display'].lower())

    middle_total = max(0, n - 2)
    is_complete = middle_total == 0 or len(solved) >= n
    result_squares = raddle_result_squares(parsed, state, hint_attempts=hint_attempts) if is_complete else ''
    return {
        'rows': rows,
        'unused_hints': unused,
        'used_hints': used,
        'title_from': parsed['words'][0] if n else '',
        'title_to': parsed['words'][-1] if n else '',
        'n_words': n,
        'middle_total': middle_total,
        'solved_count': len(solved),
        'collapsed_solved_count': 0,
        'default_focus_index': default_focus,
        'default_ref_index': default_ref_idx,
        'default_ref_word': default_ref_word,
        'default_ref_role': default_ref_role,
        'last_word_dual_clues': last_word_dual_clues,
        'last_word_clue_options': last_word_clue_options,
        'is_complete': is_complete,
        'result_squares': result_squares,
        'assist_enabled': assist_enabled,
        'assist_fractions': assist_cfg.get('fractions', DEFAULT_RADDLE_ASSIST_FRACTIONS),
        'is_tournament': tournament,
    }
