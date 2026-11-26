"""Разбор криптика: разметка подсказки и формула → проверка и HTML.

Разметка — подсказка слово в слово со служебными знаками::

    *Пьяный* (мужчина же) | {сокровище}

``|`` делит фразу на две части (ровно одна черта), ``{…}`` — определение
(хотя бы одно; внутри можно размечать блоки и индикаторы), ``*…*`` —
индикатор, ``(…)`` — буквенный блок, остальное — связки.

Формула — как из слов блоков получается ответ::

    анаг[Пьяный](МУЖЧИНА ЖЕ)
    звук[говорю](ЧТО)=ШТО + без_кон[Бесконечно](ПОРА)
    вокруг[взял](без_сер[Бессердечный](ХОДОК), МЯЧ)

Операция пишется как ``op[индикатор](аргументы)``; ``A + B`` и ``X − Y``
без индикатора, с индикатором — ``склеить[…](A, B)`` и ``убрать[…](X, Y)``. Каждый индикатор
разметки привязан ровно к одной операции, каждое слово блоков используется
ровно один раз. ``=СЛОВО`` после шага задаёт или проверяет его результат;
у созвучия и синонима (``РЕКА=ПО``) результат берётся на веру. Двойное
определение: две ``{…}``, блоков нет, формула — сам ответ.

Словарик для авторов — ``site/_cryptic_explain_help.html``.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

from django.utils.html import escape


@dataclass(frozen=True)
class Op:
    name: str  # подпись в разборе
    arity: int = 1
    trusted: bool = False  # результат не вычисляется, задаётся через =


OPS: dict[str, Op] = {
    'анаг': Op('анаграмма'),
    'скрыт': Op('спрятано'),
    'обр': Op('наоборот'),
    'нач': Op('первые буквы'),
    'кон': Op('последние буквы'),
    'чёт': Op('чётные буквы'),
    'нечёт': Op('нечётные буквы'),
    'сер': Op('середина'),
    'без_нач': Op('без первой'),
    'без_кон': Op('без последней'),
    'без_сер': Op('без середины'),
    'вокруг': Op('вставить внутрь', arity=2),
    'замена': Op('замена', arity=2),
    'звук': Op('звучит как', trusted=True),
    # То же, что ``X − Y`` и ``A + B``, но с индикатором.
    'убрать': Op('убрать', arity=2),
    'склеить': Op('склеить', arity=0),  # 0 — сколько угодно, от двух
}
_OP_ALIASES = {'чет': 'чёт', 'нечет': 'нечёт'}

PALETTE_SIZE = 4
_VARIANT_LIMIT = 400
# Сколько вариантов и проб можно перебрать за один разбор: вложенные вставки
# множат варианты, а предпросмотр считается на каждое нажатие клавиши.
WORK_BUDGET = 50_000
_budget: ContextVar[list[int]] = ContextVar('cryptic_explain_budget')


class ExplainError(Exception):
    """Разбор не сходится: сообщение показывается автору как есть."""


def up(text) -> str:
    return str(text or '').upper().replace('Ё', 'Е')


def shown_upper(text) -> str:
    """Заглавные для показа: Ё остаётся Ё (сравнение — через ``letters``)."""
    return str(text or '').upper()


def _spend(amount: int = 1) -> None:
    left = _budget.get(None)
    if left is None:
        return
    left[0] -= amount
    if left[0] < 0:
        raise ExplainError('Формула слишком ветвится: уточните промежуточные шаги через «=»')


def letters(text) -> str:
    return re.sub(r'[^А-ЯA-Z0-9]', '', up(text))


def _words(text) -> list[str]:
    return [w for w in (letters(part) for part in re.split(r'[\s,.;:!?«»"()—–]+', str(text or ''))) if w]


def _squash(text) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip()


# --- разметка ------------------------------------------------------------


@dataclass
class SNode:
    kind: str  # root | def | text | ind | fod | split
    text: str = ''
    children: list['SNode'] = field(default_factory=list)
    color: int = 0
    op_name: str = ''
    ids: str = ''  # шаги разбора, с которыми связан кусок (для подсветки при наведении)

    def plain(self) -> str:
        if self.kind == 'split':
            return ''
        if self.kind in ('text', 'ind', 'fod'):
            return self.text
        return ''.join(child.plain() for child in self.children)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def parse_markup(src: str) -> SNode:
    root = SNode('root')
    stack = [root]
    buf: list[str] = []
    splits = 0
    i = 0
    src = str(src or '')

    def flush():
        if buf:
            stack[-1].children.append(SNode('text', ''.join(buf)))
            buf.clear()

    while i < len(src):
        c = src[i]
        if c == '{':
            if len(stack) > 1:
                raise ExplainError('Смысловые части {…} не вкладываются друг в друга')
            flush()
            node = SNode('def')
            stack[-1].children.append(node)
            stack.append(node)
            i += 1
        elif c == '}':
            if len(stack) < 2:
                raise ExplainError('Лишняя «}»')
            flush()
            stack.pop()
            i += 1
        elif c == '*':
            flush()
            end = src.find('*', i + 1)
            if end < 0:
                raise ExplainError('Не закрыт индикатор *…*')
            stack[-1].children.append(SNode('ind', src[i + 1:end]))
            i = end + 1
        elif c == '(':
            flush()
            depth = 0
            j = i
            while j < len(src):
                if src[j] == '(':
                    depth += 1
                elif src[j] == ')':
                    depth -= 1
                    if not depth:
                        break
                j += 1
            if j >= len(src):
                raise ExplainError('Не закрыт буквенный блок (…)')
            stack[-1].children.append(SNode('fod', src[i + 1:j]))
            i = j + 1
        elif c == ')':
            raise ExplainError('Лишняя «)»')
        elif c == '|':
            if len(stack) > 1:
                raise ExplainError('Черта | внутри смысловой части')
            flush()
            stack[-1].children.append(SNode('split'))
            splits += 1
            i += 1
        else:
            buf.append(c)
            i += 1
    flush()
    if len(stack) > 1:
        raise ExplainError('Не закрыта смысловая часть {…}')
    if splits != 1:
        raise ExplainError('Черта | должна быть одна' if splits else 'Нет черты |, делящей подсказку на две части')
    return root


# --- формула -------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'\s[-–—]\s|\s+|−|→|->|[()\[\],=+]|[0-9A-Za-zА-Яа-яЁё_«»]+(?:-[0-9A-Za-zА-Яа-яЁё«»]+)*'
)


def _tokenize(src: str) -> list[tuple[str, str]]:
    src = ' ' + str(src or '') + ' '
    tokens = []
    i = 0
    while i < len(src):
        m = _TOKEN_RE.match(src, i)
        if not m:
            raise ExplainError('Непонятный символ в формуле: «{}»'.format(src[i]))
        s = m.group(0)
        i = m.end()
        if s.isspace():
            continue
        if s == '−' or re.fullmatch(r'\s[-–—]\s', s):
            tokens.append(('-', s))
        elif s in ('→', '->'):
            tokens.append(('→', s))
        elif s in '()[],=+':
            tokens.append((s, s))
        else:
            tokens.append(('w', s))
    return tokens


@dataclass
class FNode:
    kind: str  # lit | op | cat | minus
    id: int
    words: list[str] = field(default_factory=list)
    op: str = ''
    indicator: str = ''
    args: list['FNode'] = field(default_factory=list)
    swap: tuple[str, str] = ('', '')
    result: str = ''
    count: int = 1  # нач2 / кон2: сколько букв с краю каждого слова


class _FormulaParser:
    def __init__(self, src: str):
        self.tokens = _tokenize(src)
        self.p = 0
        self.ids = 0

    def _new(self, kind, **kw) -> FNode:
        self.ids += 1
        return FNode(kind, self.ids, **kw)

    def peek(self, kind) -> bool:
        return self.p < len(self.tokens) and self.tokens[self.p][0] == kind

    def eat(self, kind) -> str:
        if not self.peek(kind):
            tail = '' if self.p < len(self.tokens) else ', а она закончилась'
            raise ExplainError('В формуле ожидалось «{}»{}'.format('слово' if kind == 'w' else kind, tail))
        value = self.tokens[self.p][1]
        self.p += 1
        return value

    def parse(self) -> FNode:
        if not self.tokens:
            raise ExplainError('Пустая формула')
        node = self.expr()
        if self.p < len(self.tokens):
            raise ExplainError('Лишнее в конце формулы: «{}»'.format(self.tokens[self.p][1]))
        return node

    def expr(self) -> FNode:
        items = [self.term()]
        while self.peek('+') or self.peek('-'):
            kind = self.tokens[self.p][0]
            self.p += 1
            right = self.term()
            if kind == '-':
                left = self._new('cat', args=items) if len(items) > 1 else items[0]
                items = [self._new('minus', args=[left, right])]
            else:
                items.append(right)
        return self._new('cat', args=items) if len(items) > 1 else items[0]

    def term(self) -> FNode:
        node = self.atom()
        if self.peek('='):
            self.p += 1
            words = []
            while self.peek('w'):
                words.append(self.eat('w'))
            if not words:
                raise ExplainError('После «=» нужно слово')
            node.result = letters(''.join(words))
        return node

    def atom(self) -> FNode:
        if self.peek('('):
            self.p += 1
            node = self.expr()
            self.eat(')')
            return node
        if not self.peek('w'):
            raise ExplainError('В формуле ожидалось слово или операция')
        name = self.tokens[self.p][1].lower()
        name = _OP_ALIASES.get(name, name)
        nxt = self.tokens[self.p + 1][0] if self.p + 1 < len(self.tokens) else ''
        count = 1
        edge = re.fullmatch(r'(нач|кон)(\d{1,2})', name)
        if edge and nxt in ('(', '['):
            name, count = edge.group(1), int(edge.group(2))
            if count < 1:
                raise ExplainError('У «{}» число букв — от 1'.format(name))
        if name in OPS and nxt in ('(', '['):
            self.p += 1
            node = self._new('op', op=name, count=count)
            if self.peek('['):
                self.p += 1
                parts = []
                while self.p < len(self.tokens) and not self.peek(']'):
                    parts.append(self.tokens[self.p][1])
                    self.p += 1
                self.eat(']')
                node.indicator = ' '.join(parts)
            self.eat('(')
            node.args.append(self.expr())
            if name == 'замена':
                self.eat(',')
                src = self.eat('w')
                self.eat('→')
                dst = self.eat('w')
                node.swap = (letters(src), letters(dst))
            else:
                while self.peek(','):
                    self.p += 1
                    node.args.append(self.expr())
            self.eat(')')
            expected = OPS[name].arity
            got = len(node.args) + (1 if name == 'замена' else 0)
            if expected and got != expected:
                raise ExplainError('У «{}» должно быть аргументов: {}'.format(name, expected))
            if not expected and got < 2:
                raise ExplainError('У «{}» нужно хотя бы два аргумента'.format(name))
            return node
        words = []
        while self.peek('w'):
            words.append(self.eat('w'))
        return self._new('lit', words=words)


def parse_formula(src: str) -> FNode:
    return _FormulaParser(src).parse()


# --- вычисление ----------------------------------------------------------
# Значение шага — список вариантов (вставка может встать в разные места).
# Вариант — части ответа и выбранные результаты шагов (``picks``).
# Часть: ('fix', буквы, слова) | ('anag', буквы, id) | ('hid', источник, id).


@dataclass
class _Variant:
    parts: list[tuple]
    picks: dict[int, str]


def _fixed(variant: _Variant, where: str) -> tuple[str, list[str]]:
    text, words = '', []
    for part in variant.parts:
        if part[0] != 'fix':
            raise ExplainError('Допишите результат через «=» после {}'.format(where))
        text += part[1]
        words += part[2]
    return text, words


def _match(parts, target: str, pos: int, picks: dict) -> Optional[dict]:
    if not parts:
        return picks if pos == len(target) else None
    part, rest = parts[0], parts[1:]
    if part[0] == 'fix':
        if target[pos:pos + len(part[1])] != part[1]:
            return None
        return _match(rest, target, pos + len(part[1]), picks)
    if part[0] == 'anag':
        piece = target[pos:pos + len(part[1])]
        if len(piece) != len(part[1]) or sorted(piece) != sorted(part[1]):
            return None
        return _match(rest, target, pos + len(piece), {**picks, part[2]: piece})
    for length in range(len(target) - pos, 0, -1):
        _spend()
        piece = target[pos:pos + length]
        if piece in part[1]:
            found = _match(rest, target, pos + length, {**picks, part[2]: piece})
            if found is not None:
                return found
    return None


def _label(node: FNode) -> str:
    if node.kind == 'op':
        return '«{}(…)»'.format(node.op)
    if node.kind == 'lit':
        return '«{}»'.format(' '.join(node.words))
    return 'шага'


def _evaluate(node: FNode) -> list[_Variant]:
    values = _evaluate_raw(node)
    if not node.result:
        if node.kind == 'op' and OPS[node.op].trusted:
            raise ExplainError('У «{}» допишите результат: {}(…)=СЛОВО'.format(node.op, node.op))
        return values
    trusted = node.kind == 'lit' or (node.kind == 'op' and OPS[node.op].trusted)
    for variant in values:
        picks = variant.picks if trusted else _match(variant.parts, node.result, 0, variant.picks)
        if picks is not None:
            return [_Variant([('fix', node.result, [node.result])], {**picks, node.id: node.result})]
    raise ExplainError('Шаг {} не даёт {}'.format(_label(node), node.result))


def _fix(text: str, base: dict, node_id: int) -> _Variant:
    _spend()
    return _Variant([('fix', text, [text])], {**base, node_id: text})


def _concat(items: list[FNode]) -> list[_Variant]:
    acc = [_Variant([], {})]
    for item in items:
        values = _evaluate(item)
        _spend(len(acc) * len(values))
        acc = [
            _Variant(a.parts + b.parts, {**a.picks, **b.picks})
            for a in acc for b in values
        ][:_VARIANT_LIMIT]
    return acc


def _remove(node: FNode) -> list[_Variant]:
    out = []
    for a in _evaluate(node.args[0]):
        for b in _evaluate(node.args[1]):
            x, _ = _fixed(a, 'уменьшаемого')
            y, _ = _fixed(b, 'вычитаемого')
            at = x.find(y)
            if at >= 0 and y:
                out.append(_fix(x[:at] + x[at + len(y):], {**a.picks, **b.picks}, node.id))
    if not out:
        raise ExplainError('Нельзя убрать: таких букв подряд нет')
    return out


def _middle_options(n: int) -> list[range]:
    """Середина слова из n букв: у нечётного одна буква; у чётного обе средние
    или одна из них (так тоже пишут; какая — решает ответ или «=»)."""
    m = n // 2
    if n % 2:
        return [range(m, m + 1)]
    if not n:
        return [range(0)]
    return [range(m - 1, m + 1), range(m - 1, m), range(m, m + 1)]


def _evaluate_raw(node: FNode) -> list[_Variant]:
    if node.kind == 'op' and node.op == 'склеить':
        return _concat(node.args)
    if node.kind == 'op' and node.op == 'убрать':
        return _remove(node)
    if node.kind == 'lit':
        return [_Variant([('fix', letters(''.join(node.words)), [letters(w) for w in node.words])], {})]
    if node.kind == 'cat':
        return _concat(node.args)
    if node.kind == 'minus':
        return _remove(node)

    op = node.op
    args = [_evaluate(arg) for arg in node.args]
    out: list[_Variant] = []
    for variant in args[0]:
        if op in ('анаг', 'скрыт'):
            text, _ = _fixed(variant, 'вложенного шага')
            out.append(_Variant([('anag' if op == 'анаг' else 'hid', text, node.id)], variant.picks))
            continue
        if op == 'звук':
            out.append(_Variant([('fix', '', [])], variant.picks))
            continue
        text, words = _fixed(variant, '«{}»'.format(op))
        picks = variant.picks
        if op == 'обр':
            out.append(_fix(text[::-1], picks, node.id))
        elif op == 'нач':
            out.append(_fix(''.join(w[:node.count] for w in words), picks, node.id))
        elif op == 'кон':
            out.append(_fix(''.join(w[-node.count:] for w in words), picks, node.id))
        elif op == 'чёт':
            out.append(_fix(text[1::2], picks, node.id))
        elif op == 'нечёт':
            out.append(_fix(text[0::2], picks, node.id))
        elif op == 'без_нач':
            out.append(_fix(text[1:], picks, node.id))
        elif op == 'без_кон':
            out.append(_fix(text[:-1], picks, node.id))
        elif op == 'сер':
            for mid in _middle_options(len(text)):
                out.append(_fix(''.join(text[i] for i in mid), picks, node.id))
        elif op == 'без_сер':
            for mid in _middle_options(len(text)):
                out.append(_fix(''.join(ch for i, ch in enumerate(text) if i not in mid), picks, node.id))
        elif op == 'замена':
            src, dst = node.swap
            at = text.find(src)
            if at < 0 or not src:
                raise ExplainError('В {} нет {} для замены'.format(text, src))
            while at >= 0:
                out.append(_fix(text[:at] + dst + text[at + len(src):], picks, node.id))
                at = text.find(src, at + 1)
        elif op == 'вокруг':
            for inner in args[1]:
                inner_text, _ = _fixed(inner, 'вставляемого')
                for k in range(1, len(text)):
                    if len(out) >= _VARIANT_LIMIT:
                        break
                    out.append(_fix(text[:k] + inner_text + text[k:], {**picks, **inner.picks}, node.id))
    return out[:_VARIANT_LIMIT]


# --- шаги для показа -----------------------------------------------------


@dataclass(frozen=True)
class Step:
    inputs: tuple[str, ...]
    name: str
    output: str
    color: int = 0
    joiner: str = ''  # '+', '−' или 'в' (вставка: что «в» куда)
    # Номера букв (без пробелов): что из первого входа пошло в результат (hit),
    # что из него выброшено (cut) и что в результате появилось новое (out_hit).
    hit: tuple[int, ...] = ()
    cut: tuple[int, ...] = ()
    out_hit: tuple[int, ...] = ()
    node_id: int = 0
    final: bool = False


def _op_name(node: FNode) -> str:
    if node.op == 'замена':
        return 'замена {}→{}'.format(*node.swap)
    if node.op in ('нач', 'кон') and node.count > 1:
        n = node.count
        noun = 'буквы' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'букв'
        return '{} {} {}'.format('первые' if node.op == 'нач' else 'последние', n, noun)
    return OPS[node.op].name


def _shown(node: FNode, picks: dict) -> str:
    if node.kind == 'lit':
        return node.result or shown_upper(' '.join(node.words))
    if node.kind == 'cat' or (node.kind == 'op' and node.op == 'склеить' and not node.result):
        return ''.join(letters(_shown(arg, picks)) for arg in node.args)
    return picks.get(node.id) or node.result or '?'


def _shown_in(node: FNode, picks: dict) -> str:
    """Вход шага. У склейки без своего шага видны части: «МИРА + И РАЯ»."""
    if node.kind == 'cat':
        return ' + '.join(_shown_in(arg, picks) for arg in node.args)
    return _shown(node, picks)


def _arg_words(node: FNode, picks: dict) -> list[str]:
    """Слова входа шага, как их видит вычисление (для первых/последних букв)."""
    if node.kind == 'lit' and not node.result:
        return [letters(w) for w in node.words]
    if node.kind == 'cat' or (node.kind == 'op' and node.op == 'склеить' and not node.result):
        return [w for arg in node.args for w in _arg_words(arg, picks)]
    return [letters(_shown(node, picks))]


def _span(at: int, length: int) -> tuple[int, ...]:
    return tuple(range(at, at + length)) if at >= 0 else ()


_NO_MARKS = ((), (), ())


def _marks(node: FNode, picks: dict) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """(hit, cut, out_hit) шага — см. ``Step``."""
    result = letters(picks.get(node.id, ''))
    if node.kind == 'minus' or node.op == 'убрать':
        x, y = (letters(_shown(arg, picks)) for arg in node.args)
        return (), _span(x.find(y) if y else -1, len(y)), ()
    op = node.op
    if op == 'вокруг':
        outer, inner = (letters(_shown(arg, picks)) for arg in node.args)
        for k in range(1, len(outer)):
            if outer[:k] + inner + outer[k:] == result:
                return (), (), _span(k, len(inner))
        return _NO_MARKS
    if op == 'замена':
        text = letters(_shown(node.args[0], picks))
        src, dst = node.swap
        at = text.find(src) if src else -1
        while at >= 0:
            if text[:at] + dst + text[at + len(src):] == result:
                return (), _span(at, len(src)), _span(at, len(dst))
            at = text.find(src, at + 1)
        return _NO_MARKS
    words = _arg_words(node.args[0], picks)
    text = ''.join(words)
    n = len(text)
    if op == 'скрыт':
        return _span(text.find(result) if result else -1, len(result)), (), ()
    if op in ('нач', 'кон'):
        hit, pos = [], 0
        for w in words:
            take = min(node.count, len(w))
            hit.extend(range(pos, pos + take) if op == 'нач' else range(pos + len(w) - take, pos + len(w)))
            pos += len(w)
        return tuple(hit), (), ()
    if op == 'чёт':
        return tuple(range(1, n, 2)), (), ()
    if op == 'нечёт':
        return tuple(range(0, n, 2)), (), ()
    if op == 'без_нач':
        return (), _span(0, min(n, 1)), ()
    if op == 'без_кон':
        return (), _span(n - 1, min(n, 1)), ()
    if op in ('сер', 'без_сер'):
        for mid in _middle_options(n):
            if op == 'сер' and ''.join(text[i] for i in mid) == result:
                return tuple(mid), (), ()
            if op == 'без_сер' and ''.join(ch for i, ch in enumerate(text) if i not in mid) == result:
                return (), tuple(mid), ()
    return _NO_MARKS


def _steps(node: FNode, picks: dict, colors: dict, answer: str, out: list, top: bool) -> None:
    """``answer`` — ответ для показа (с пробелами и Ё): им подписан последний шаг."""
    if node.kind == 'lit':
        if node.result:
            out.append(Step(
                (shown_upper(' '.join(node.words)),), 'значит', answer if top else node.result,
                node_id=node.id, final=top,
            ))
        return
    if node.kind == 'cat':
        for arg in node.args:
            _steps(arg, picks, colors, answer, out, False)
        if top:
            out.append(Step(
                tuple(_shown_in(a, picks) for a in node.args), 'склеить', answer, joiner='+',
                node_id=node.id, final=True,
            ))
        return
    if node.kind == 'minus':
        for arg in node.args:
            _steps(arg, picks, colors, answer, out, False)
        _hit, cut, _out = _marks(node, picks)
        out.append(Step(
            tuple(_shown_in(a, picks) for a in node.args), 'убрать',
            answer if top else picks.get(node.id, '?'), joiner='−', cut=cut,
            node_id=node.id, final=top,
        ))
        return
    for arg in node.args:
        _steps(arg, picks, colors, answer, out, False)
    inputs = tuple(_shown_in(a, picks) for a in node.args)
    joiner = ''
    if node.op == 'склеить':
        joiner = '+'
    elif node.op == 'убрать':
        joiner = '−'
    elif node.op == 'вокруг':
        inputs = (inputs[1], inputs[0])
        joiner = 'в'
    elif node.op == 'скрыт':
        inputs = (shown_upper(' '.join(node.args[0].words)) if node.args[0].kind == 'lit' else inputs[0],)
    hit, cut, out_hit = _marks(node, picks)
    out.append(Step(
        inputs, _op_name(node), answer if top else _shown(node, picks),
        color=colors.get(node.id, 0), joiner=joiner, hit=hit, cut=cut, out_hit=out_hit,
        node_id=node.id, final=top,
    ))


# --- проверка целиком ----------------------------------------------------


@dataclass
class Explanation:
    errors: list[str] = field(default_factory=list)
    surface: Optional[SNode] = None
    steps: list[Step] = field(default_factory=list)
    fod_colors: dict[int, int] = field(default_factory=dict)
    fod_ids: dict[int, set] = field(default_factory=dict)
    double_definition: bool = False
    answer: str = ''
    # Не мешают сохранить, но скорее всего это недосмотр автора.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def analyze(*, clue: str, answer: str, markup: str, formula: str) -> Explanation:
    token = _budget.set([WORK_BUDGET])
    try:
        return _analyze(clue=clue, answer=answer, markup=markup, formula=formula)
    finally:
        _budget.reset(token)


def _latin_hint(word: str) -> str:
    return ' (в нём латинские буквы)' if re.search(r'[A-Z]', word) and re.search(r'[А-Я]', word) else ''


def _analyze(*, clue: str, answer: str, markup: str, formula: str) -> Explanation:
    res = Explanation(answer=letters(answer))
    answer_shown = re.sub(r'\s+', ' ', shown_upper(answer)).strip()
    try:
        surface = parse_markup(markup)
    except ExplainError as exc:
        res.errors.append(str(exc))
        return res
    res.surface = surface
    if clue and _squash(surface.plain()) != _squash(clue):
        res.errors.append('Текст без служебных знаков не совпадает с подсказкой')
    nodes = list(surface.walk())
    defs = [n for n in nodes if n.kind == 'def']
    inds = [n for n in nodes if n.kind == 'ind']
    fods = [n for n in nodes if n.kind == 'fod']
    if not defs:
        res.errors.append('Нет смысловой части {…}')

    try:
        tree = parse_formula(formula)
    except ExplainError as exc:
        res.errors.append(str(exc))
        return res

    if tree.kind == 'lit' and not tree.result and not fods and letters(''.join(tree.words)) == res.answer:
        res.double_definition = True
        if len(defs) < 2:
            res.errors.append('Формула — сам ответ только у двойного определения: нужны две {…}')
        if inds:
            res.errors.append('У двойного определения не бывает индикаторов')
        return res

    lit_words: list[tuple[str, Optional[FNode], FNode]] = []
    bound: list[FNode] = []
    colors: dict[int, int] = {}

    def scan(node: FNode, parent: Optional[FNode]):
        if node.kind == 'lit':
            lit_words.extend((letters(w), parent, node) for w in node.words)
            return
        if node.kind == 'op':
            if node.indicator:
                colors[node.id] = len(bound) % PALETTE_SIZE + 1
                bound.append(node)
            elif node.op not in ('склеить', 'убрать'):
                res.warnings.append('У «{}» нет индикатора: обычно его подсказывает слово в подсказке'.format(node.op))
            for arg in node.args:
                scan(arg, node)
            return
        # Слова прямо под «X − Y» или верхним «A + B» относятся к их шагу.
        owner = node if node.kind == 'minus' or parent is None else parent
        for arg in node.args:
            scan(arg, owner)

    scan(tree, None)

    pool = [(w, index) for index, fod in enumerate(fods) for w in _words(fod.text)]
    for word, parent, lit in lit_words:
        at = next((i for i, (w, _b) in enumerate(pool) if w == word), None)
        if at is None:
            res.errors.append('В формуле «{}», а в блоках (…) такого слова нет{}'.format(word, _latin_hint(word)))
            continue
        block = pool.pop(at)[1]
        linked = res.fod_ids.setdefault(block, set())
        if parent is not None:
            linked.add(parent.id)
        if lit.result:
            linked.add(lit.id)
        if block not in res.fod_colors and parent is not None and parent.id in colors:
            res.fod_colors[block] = colors[parent.id]
    for word, _block in pool:
        res.errors.append('Слово «{}» из блока (…) не использовано в формуле'.format(word))

    free = list(inds)
    for node in bound:
        key = letters(node.indicator)
        target = next((n for n in free if letters(n.text) == key), None)
        if target is None:
            res.errors.append('Индикатора «{}» нет в разметке *…*{}'.format(
                node.indicator, _latin_hint(up(node.indicator)),
            ))
            continue
        free.remove(target)
        target.color = colors[node.id]
        target.op_name = _op_name(node)
        target.ids = str(node.id)
    for node in free:
        res.errors.append(
            'Индикатор «{}» не привязан к операции: напишите op[{}](…)'.format(node.text, node.text)
        )

    try:
        variants = _evaluate(tree)
    except ExplainError as exc:
        res.errors.append(str(exc))
        return res
    picks = None
    for variant in variants:
        picks = _match(variant.parts, res.answer, 0, variant.picks)
        if picks is not None:
            break
    if picks is None:
        shown = []
        for variant in variants[:3]:
            shown.append(' + '.join(
                part[1] if part[0] == 'fix' else '{}({})'.format('анаг' if part[0] == 'anag' else 'скрыт', part[1])
                for part in variant.parts
            ))
        res.errors.append('Формула даёт {}, а не {}'.format(' или '.join(shown) or 'пустоту', res.answer))
        return res
    _steps(tree, picks, colors, answer_shown, res.steps, True)
    return res


def validate(*, clue: str, answer: str, markup: str, formula: str) -> None:
    """Пустой разбор допустим; заполненный должен сходиться целиком."""
    if not str(markup or '').strip() and not str(formula or '').strip():
        return
    if not str(markup or '').strip():
        raise ExplainError('Заполните разметку разбора')
    if not str(formula or '').strip():
        raise ExplainError('Заполните формулу разбора')
    res = analyze(clue=clue, answer=answer, markup=markup, formula=formula)
    if res.errors:
        raise ExplainError('Разбор не сходится: ' + '; '.join(res.errors))


# --- HTML ----------------------------------------------------------------


def _color_cls(color: int) -> str:
    return ' cx-c{}'.format(color) if color else ''


def _ids_attr(ids: str) -> str:
    return ' data-cx-ids="{}"'.format(escape(ids)) if ids else ''


def _segment(cls: str, color: int, inner: str, label: str, ids: str = '') -> str:
    return (
        '<span class="cx-seg {}{}"{}><span class="cx-seg__text">{}</span>'
        '<span class="cx-seg__label">{}</span></span>'
    ).format(cls, _color_cls(color), _ids_attr(ids), inner, escape(label))


# Уровни подсказок до решения: 1 — только черта и определение, 2 — вся
# разметка подсказки без шагов, где собирается ответ.
HINT_SPLIT = 1
HINT_SURFACE = 2


def _render_surface(res: Explanation, hint_level: int = 0) -> str:
    """Подсказка с размеченными частями. ``hint_level`` — уровень подсказки
    (без связей с шагами разбора: их на карточке ещё нет)."""
    counter = {'fod': 0, 'def': 0}
    pieces: list[str] = []
    linked = not hint_level

    def plain(text: str) -> str:
        return '<span class="cx-plain">{}</span>'.format(escape(text))

    def render(node: SNode) -> str:
        if node.kind == 'text':
            return plain(node.text)
        if node.kind == 'split':
            return '<span class="cx-split" role="separator"></span>'
        if node.kind == 'ind':
            if hint_level == HINT_SPLIT:
                return plain(node.text)
            ids = node.ids if linked else ''
            return _segment('cx-seg--ind', node.color, escape(node.text), node.op_name or 'индикатор', ids)
        if node.kind == 'fod':
            color = res.fod_colors.get(counter['fod'], 0)
            ids = ' '.join(str(i) for i in sorted(res.fod_ids.get(counter['fod'], ()))) if linked else ''
            counter['fod'] += 1
            if hint_level == HINT_SPLIT:
                return plain(node.text)
            return _segment('cx-seg--fod', color, escape(node.text), 'буквы', ids)
        if node.kind == 'def':
            counter['def'] += 1
            inner = _glue(node.children, render)
            # Первая подсказка у двойного определения выдаёт только одно из двух.
            if hint_level == HINT_SPLIT and res.double_definition and counter['def'] > 1:
                return inner
            ids = 'def' if res.steps and linked else ''
            return _segment('cx-seg--def', 0, inner, 'определение', ids)
        return _glue(node.children, render)

    pieces.append(render(res.surface))
    return '<p class="cx-line">{}</p>'.format(''.join(pieces))


def _glue(children: list[SNode], render) -> str:
    """Знак препинания сразу после блока не уезжает на новую строку один."""
    out = []
    i = 0
    while i < len(children):
        child = children[i]
        html = render(child)
        nxt = children[i + 1] if i + 1 < len(children) else None
        if child.kind in ('ind', 'fod', 'def') and nxt is not None and nxt.kind == 'text':
            m = re.match(r'^[,.;:!?…»)]+', nxt.text)
            if m:
                html = '<span class="cx-nowrap">{}<span class="cx-plain">{}</span></span>'.format(
                    html, escape(m.group(0)),
                )
                rest = nxt.text[m.end():]
                out.append(html)
                if rest:
                    out.append('<span class="cx-plain">{}</span>'.format(escape(rest)))
                i += 2
                continue
        out.append(html)
        i += 1
    return ''.join(out)


def _mark(text: str, hit: tuple[int, ...] = (), cut: tuple[int, ...] = ()) -> str:
    """Выделяет буквы с номерами ``hit`` и зачёркивает ``cut``. Если буквы идут
    подряд (спрятанное слово), пробел внутри куска выделяется вместе с ними;
    первые буквы слов — каждая отдельно."""
    chars = list(shown_upper(text))
    groups = {'cx-hit': set(hit), 'cx-cut': set(cut)}
    solid = {cls: bool(ix) and max(ix) - min(ix) + 1 == len(ix) for cls, ix in groups.items()}
    index, marks = -1, []
    for ch in chars:
        if letters(ch):
            index += 1
            marks.append(next((cls for cls, ix in groups.items() if index in ix), ''))
        else:
            marks.append(None)
    for i, m in enumerate(marks):
        if m is None:
            before = next((x for x in reversed(marks[:i]) if x is not None), '')
            after = next((x for x in marks[i + 1:] if x is not None), '')
            marks[i] = before if before and before == after and solid[before] else ''
    html, run, run_cls = [], [], ''
    for ch, m in zip(chars + [''], marks + ['']):
        if m and m == run_cls:
            run.append(ch)
            continue
        if run:
            html.append('<span class="{}">{}</span>'.format(run_cls, escape(''.join(run))))
        run, run_cls = ([ch], m) if m else ([], '')
        if not m:
            html.append(escape(ch))
    return ''.join(html)


def _word(text: str, extra: str = '') -> str:
    return '<span class="cx-word{}">{}</span>'.format(extra, text)


def _render_steps(steps: list[Step]) -> str:
    rows = []
    for step in steps:
        words = [
            _word(_mark(s, step.hit, step.cut) if i == 0 and (step.hit or step.cut) else escape(s))
            for i, s in enumerate(step.inputs)
        ]
        if step.joiner:
            inputs = '<span class="cx-join">{}</span>'.format(escape(step.joiner)).join(words)
        else:
            inputs = ' '.join(words)
        output = _mark(step.output, step.out_hit) if step.out_hit else escape(step.output)
        ids = '{}{}'.format(step.node_id or '', ' def' if step.final else '').strip()
        rows.append(
            '<li class="cx-step{}"{}><span class="cx-step__in">{}</span>'
            '<span class="cx-step__op"><span class="cx-step__name">{}</span>'
            '<i class="ph ph-arrow-right" aria-hidden="true"></i></span>'
            '<span class="cx-step__out">{}</span></li>'.format(
                _color_cls(step.color), _ids_attr(ids), inputs, escape(step.name),
                _word(output, ' cx-word--out'),
            )
        )
    return '<ol class="cx-steps">{}</ol>'.format(''.join(rows))


def render_html(res: Explanation) -> str:
    """HTML разбора; пусто, если разметка не разобралась."""
    if res.surface is None:
        return ''
    html = _render_surface(res)
    if res.ok and res.steps:
        html += _render_steps(res.steps)
    elif res.ok and res.double_definition:
        html += '<p class="cx-note">Двойное определение: обе части по-своему определяют {}.</p>'.format(
            escape(res.answer),
        )
    return '<div class="cx-explain">{}</div>'.format(html)


def editor_preview(*, clue: str, answer: str, markup: str, formula: str) -> dict:
    """Живое превью разбора для редакторов и авторов: HTML, ошибки, предупреждения."""
    clue, markup, formula = (str(v or '').strip() for v in (clue, markup, formula))
    if not markup and not formula:
        return {'html': '', 'errors': [], 'warnings': [], 'empty': True}
    res = analyze(clue=clue, answer=str(answer or ''), markup=markup, formula=formula)
    if not formula:
        res.errors.append('Заполните формулу')
    return {'html': render_html(res), 'errors': res.errors, 'warnings': res.warnings, 'answer': res.answer}


def explanation_html(*, clue: str, answer: str, markup: str, formula: str) -> str:
    """HTML для игрока: только сошедшийся разбор."""
    if not str(markup or '').strip():
        return ''
    res = analyze(clue=clue, answer=answer, markup=markup, formula=formula)
    return render_html(res) if res.ok else ''


def hint_htmls(*, clue: str, answer: str, markup: str, formula: str) -> tuple[str, str] | None:
    """HTML двух подсказок до решения или None, если разбора нет или он не сходится.

    Первая — деление на две части и определение (у двойного определения —
    первое из двух), вторая — вся разметка подсказки без шагов и ответа.
    """
    if not str(markup or '').strip():
        return None
    res = analyze(clue=clue, answer=answer, markup=markup, formula=formula)
    if not res.ok or res.surface is None:
        return None
    return tuple(
        '<div class="cx-explain cx-explain--hint">{}</div>'.format(_render_surface(res, level))
        for level in (HINT_SPLIT, HINT_SURFACE)
    )
