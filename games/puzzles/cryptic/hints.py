"""Две подсказки криптика до решения, как 💡 и 💡💡 в Лесенке.

Криптик стоит ``CRYPTIC_POINTS`` баллов, каждая подсказка снимает
``HINT_PENALTY``. Первая показывает черту и определение, вторая (только после
первой) — всю разметку подсказки без шагов, где собирается ответ. Строки
``Hint`` (штраф и порядок) пересобирает консоль при каждом сохранении дня;
игроку тексты рисуются из разбора при показе (тег ``cryptic_hints``).
Без сошедшегося разбора подсказок нет.
"""

from __future__ import annotations

from games.puzzles.cryptic.explain import hint_htmls

CRYPTIC_POINTS = 3
HINT_PENALTY = 1
HINT_DESCS = ('где определение', 'увидеть разбор')
# Кнопка 💡 / 💡💡 и заголовок окна с подсказкой (страница криптика игрока).
HINT_BUTTONS = ('Узнать, где определение', 'Увидеть разбор')
HINT_TITLES = ('Подсказка: где определение', 'Подсказка: разбор')


def sync_cryptic_hints(task) -> None:
    from games.models import Hint

    tags = task.tags or {}
    htmls = hint_htmls(
        clue=str(tags.get('cryptic_clue') or ''),
        answer=task.answer or '',
        markup=str(tags.get('cryptic_markup') or ''),
        formula=str(tags.get('cryptic_formula') or ''),
    )
    existing = {h.number: h for h in Hint.objects.filter(task=task)}
    if htmls is None:
        for hint in existing.values():
            hint.delete()
        return
    saved = []
    for index, (desc, html) in enumerate(zip(HINT_DESCS, htmls), start=1):
        number = str(index)
        hint = existing.pop(number, None) or Hint(task=task, number=number)
        if (hint.pk is None or hint.desc != desc or hint.text != html
                or hint.points_penalty is None or hint.points_penalty != HINT_PENALTY):
            hint.desc, hint.text, hint.points_penalty = desc, html, HINT_PENALTY
            hint.save()
        saved.append(hint)
    for hint in existing.values():
        hint.delete()
    second_requires = set(saved[1].required_hints.values_list('pk', flat=True))
    if second_requires != {saved[0].pk}:
        saved[1].required_hints.set([saved[0]])
