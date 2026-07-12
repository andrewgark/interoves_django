# Raddle (Лесенка) — контракт API и чеклисты

Код: `games/raddle.py`, `games/views/attempt_views.py`, `static/templates/new/task_group.html`, шаблон `task-content/task-raddle.html`.

Матрица сценариев в коде: [`games/raddle_response_contract.py`](../games/raddle_response_contract.py) (источник правды для тестов).

## Главный принцип

> UI может перерисовывать задание и переносить фокус **только** когда сервер явно сигнализирует продвижение состояния.

Косвенные признаки (`update_task_html_new` в ответе, `status: ok`, наличие слова в `solved_indices`) **не** являются сигналом к перерисовке.

## Контракт `/send_attempt/<task_id>/` (raddle)

| Ситуация | `status` | Флаги | Клиент (`applyRaddleAttemptResponse`) |
|----------|----------|-------|-------------------------------------|
| Верно в этой попытке | `ok` | `raddle_correct: true` | `applyNewUiTaskHtml` + `focusRaddleNext` |
| Неверно | `ok` | `raddle_correct: false` | красная строка, ввод **не** трогать |
| Уже решено (лаг) | `ok` | `raddle_needs_sync: true` | синхронизация HTML + фокус |
| Дубликат неверного | `duplicate` | `raddle_duplicate_solved: false` | красная строка + сообщение, **без** HTML |
| Дубликат после успеха | `duplicate` | `raddle_duplicate_solved: true` | синхронизация HTML + фокус |

`raddle_word_index` — индекс слова из посылки (0-based), всегда при raddle-ответах.

### Сервер

- `raddle_correct` = слово из посылки совпало с эталоном **и** попало в `solved_indices` (не путать с `Partial` — это статус прогресса по всему заданию).
- `raddle_needs_sync` — попытка не зачла, но слово уже в `solved_indices` (повтор после лага).
- При `DuplicateAttemptException` для raddle: `raddle_duplicate_solved` по chain state; `update_task_html_new` **только** если solved.

### Клиент (авто-режим, не турнир)

- `raddleLast` — блокировка двойной отправки **того же** значения; ставится только если fetch реально ушёл; сбрасывается при ошибке сети, при `length !== maxlength`, при sync.
- `paste` — fallback через `setTimeout(0)` только если busy / уже отправлено.
- Разделение: `syncRaddleUiAfterAdvance` (перерисовка) vs `showRaddleWrongFeedback` (локальная обратная связь).

## Тесты

```bash
../venv/interoves_django/bin/python manage.py test games.tests.test_raddle games.tests.test_raddle_send_attempt games.tests.test_raddle_response_contract
```

- `test_raddle.py` — checker, UI context, парсинг.
- `test_raddle_send_attempt.py` — интеграция view: ok / wrong / duplicate / needs_sync.
- `test_raddle_response_contract.py` — матрица контракта непротиворечива.

## PR checklist (raddle / task_group.html)

Перед merge изменений в авто-submit или `send_attempt` для raddle:

- [ ] Есть ли вызов `applyNewUiTaskHtml` без `raddle_correct` / `raddle_needs_sync` / `raddle_duplicate_solved`?
- [ ] Сбрасывается ли `raddleLast` при `catch` и когда `postRaddleAutoForm` вернул `false`?
- [ ] Может ли один жест (paste + input) отправить два одинаковых запроса?
- [ ] При `duplicate` без `raddle_duplicate_solved` фокус остаётся на текущей строке?
- [ ] Обновлены ли тесты в `test_raddle_send_attempt.py` при новых флагах ответа?

## PR checklist (responsive / new.css)

Перед merge изменений в `task-raddle.html` или raddle-секцию `new.css` — см. также **[agents/AGENTS.md](AGENTS.md) § Responsive layout**:

- [ ] `@media (max-width: …)` для stack использует `919px` (на единицу меньше `--new-break-wide`), не другой magic number?
- [ ] `.new-raddle-task` с `container-type: inline-size` и `@container raddle-task` на `--raddle-layout-stack`?
- [ ] Правая колонка: `minmax(var(--raddle-clues-min), 1fr)`, не `minmax(0, 1fr)`?
- [ ] Поля ввода: `min-width: 0`, `max-width: 100%`?
- [ ] Ручная проверка viewport **560px** (stack, длинные слова), **800px** (stack, без полоски подсказок), **1000px** (две колонки, подсказки читаемы)?
- [ ] `./scripts/lint_new_ui_responsive.sh` проходит?

## Smoke перед деплоем (~5 мин, Firefox incognito)

Игра «Лесенка», анонимный режим, DevTools → Network (фильтр `send_attempt`).

1. **Неверное с клавиатуры** — красная строка, текст в поле остаётся, фокус на месте.
2. **То же неверное + Enter** — `duplicate`, сообщение, поле **не** стирается, фокус **не** прыгает.
3. **Другое неверное** — снова красная строка на **этой** строке.
4. **Paste полного слова** — в Network ровно **один** POST на слово.
5. **Верное слово** — строка закрывается, фокус на следующей playable.
6. **Throttle Slow 3G** — верный ответ, повтор того же → `duplicate` + `raddle_duplicate_solved: true`, UI синхронизируется **без** лишнего шага.

При багрепорте приложить из Network JSON ответа: `status`, `raddle_correct`, `raddle_needs_sync`, `raddle_duplicate_solved`, `raddle_word_index`.
