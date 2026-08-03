# Промпт: дизайн-система Interoves (new UI)

Сейчас new UI сайта визуально выглядит как типичный AI/Wordle-шаблон: зелёный accent в духе Wordle (`#6aaa64`), случайные скругления (4px / 8px / 999px), дубли вариантов кнопок, местами «пластиковые» CTA и несогласованные локальные стили. Интерфейс ещё не ощущается как самостоятельный продукт Interoves с узнаваемым характером.

Нужно постепенно исправить дизайн, не ломая существующую функциональность.

## Контекст продукта

**Interoves** — платформа с интеллектуальными играми (командные/личные сессии, билеты, результаты, хаб игр, «Лесенка»/Raddle, «Десяточки», Alphabetty, proportions и др.).

Дизайн должен передавать:
- интеллектуальность, но не академическую скуку;
- игровой характер, но не детскость;
- современность, но не типичный SaaS/Wordle-клон;
- ясность, структурность и внимание к деталям;
- ощущение авторского, самостоятельного проекта Interoves.

Избегать AI-slop:
- беспричинных градиентов;
- чрезмерного glassmorphism;
- огромных скруглений у всех элементов;
- одинаковых карточек для любого типа контента;
- случайных теней и свечения;
- декоративных элементов без функции;
- шаблонных hero-блоков;
- чрезмерного количества цветов;
- дизайна, похожего на лендинг AI-стартапа или Wordle-клон «из коробки»;
- эмодзи и иконок вместо полноценной визуальной системы (в UI уже есть эмодзи в theme switch — не размножать паттерн).

## Стек и границы работ (важно)

Проект — Django-монолит со **двумя UI-слоями**:

| Слой | Стили | Шаблоны | Статус для этой работы |
|------|--------|---------|------------------------|
| **New UI** (целевой) | `static/css/new.css` | `static/templates/new/` (+ support частично на `.new-btn`) | **Основная зона изменений** |
| Legacy | Bootstrap + `static/css/style.css` (`.btn-custom`, `.btn-*`) | `static/templates/` вне `new/` | **Не переписывать**, только зафиксировать в аудите |
| Support | `static/css/support.css` (переиспользует `.new-btn`) | `static/templates/support/` | Учитывать совместимость с `.new-btn` |
| Microsites / admin | отдельные CSS | eurovision booklet, Django admin | **Вне скоупа** |

Конвенции проекта:
- `agents/AGENTS.md` — venv, модалки, responsive new UI;
- модалки new UI: только паттерн `new-rules-modal` (не Bootstrap modal / Magnific Popup);
- responsive: токены `--new-break-wide` и др. в `:root` `new.css`; проверка `./scripts/lint_new_ui_responsive.sh`;
- тёмная тема: класс `html.new-ui-dark` + переменные в `new.css`;
- шрифт new UI: `"Clear Sans", "Helvetica Neue", Arial, sans-serif` — не подключать новые шрифты без необходимости;
- UI — server-rendered Django templates + vanilla JS в шаблонах, **не React**; не ставить UI-библиотеки ради визуала.

Документ дизайн-системы класть в `agents/DESIGN_SYSTEM.md` (рядом с остальными agent-доками), не в корень без нужды.

## Стартовые точки для аудита (уже известны — уточни и дополни)

Глобальные токены и оболочка:
- `static/css/new.css` — `:root`, `html.new-ui-dark`, `body.new-ui`, `.new-wrap`, `.new-nav`, палитра `--bg/--surface/--text/--muted/--accent/--border/--danger/...`;
- `static/templates/new/base.html` — подключение `new.css`, theme switch, оболочка.

Компоненты new UI (искать классы, не React-компоненты):
- кнопки: `.new-btn`, `.new-btn--ghost`, `.new-btn--yellow`, `.new-btn--danger`, `.new-btn--mini`, CTA хаба `.new-hub-section__cta--*`;
- карточки: `.new-game-card`, `.pal-card`, task cards в `partials/task_card.html`;
- формы: `.new-form`, attempt rows, pay forms;
- навигация: `.new-nav`, team subnav;
- модалки: `.new-rules-modal` / overlay / box;
- игровые UI: raddle, proportions, alphabetty, results tables.

Дубли / legacy-кнопки (зафиксировать, не мигрировать массово на первом этапе):
- Bootstrap `.btn`, `.btn-primary`, `.btn-custom` в legacy-шаблонах (`index.html`, `games_grid.html`, task-content и т.д.);
- спецкнопки вне системы: `.new-theme-switch__btn`, `.new-eye-toggle`, `.new-rules-trigger`, `.new-raddle-retry-btn`, submit в игровых формах.

Текущие проблемы кнопок (гипотезы для проверки):
- нет единой шкалы вариантов (нет secondary/outline/loading/icon-only как системы);
- у `.new-btn` дефолтный `margin-top: 1.25rem` — ломает layout в плотных рядах;
- ghost по смыслу ближе к outline;
- hub CTA захардкожены на `#6aaa64` в обход токенов;
- слабые/непоследовательные `:focus-visible`, `disabled`, `active`, `transition`;
- support.css частично переопределяет `.new-btn`.

## Порядок работы

Работай итерациями. **Не начинай массовую переработку до завершения аудита.**

### Итерация 0 — аудит

Изучи структуру и выдай краткий аудит с конкретными файлами и классами:

1. Где токены, цвета, типографика, CSS-переменные.
2. Какие реализации кнопок/карточек/форм/навигации/модалок есть в new UI и где legacy-дубли.
3. Где стили дублируются или конфликтуют (`new.css` vs `support.css` vs inline в шаблонах).
4. Что сильнее всего даёт ощущение AI/Wordle-шаблона.
5. Какие изменения можно безопасно внести централизованно (токены + `.new-btn*`), не переписывая все страницы.

Затем предложи последовательный план. Пока не переделывай весь интерфейс.

### Итерация 1 (минимум) — система кнопок new UI

Переработай **только** систему кнопок new UI (классы в `new.css` + правки шаблонов `new/` и при необходимости `support/`).

Нужна единая система вариантов (маппинг на существующие классы предпочтителен, ломать имена массово не надо — можно ввести семантические модификаторы и постепенно свести aliases):

| Вариант | Предлагаемый класс / alias |
|---------|----------------------------|
| primary | `.new-btn` |
| secondary | новый или переосмысленный вариант (не путать с Bootstrap) |
| outline | сейчас фактически `.new-btn--ghost` — уточни naming |
| ghost | настоящий ghost без сильной рамки (если нужен) |
| destructive | `.new-btn--danger` |
| warning/accent-alt | `.new-btn--yellow` — решить, оставлять ли как игровой/семантический |
| size mini | `.new-btn--mini` |
| disabled / loading / icon-only | добавить согласованно |

Для каждого варианта согласуй: высоту, padding, font-size/weight, border-radius, цвета, рамки, hover/active/focus-visible/disabled, transition, иконки.

Принципы:
- кнопки точные и компактные, не «пластиковые»;
- без сильных градиентов, массивных теней, oversized radius;
- одна заметная primary на смысловой блок;
- secondary/ghost не конкурируют с primary;
- destructive только для опасных действий;
- focus-visible заметен; клавиатурная доступность; контраст;
- сохранить работу `<a class="new-btn">` и `<button class="new-btn">`;
- учесть переопределения в `static/css/support.css`;
- не ломать data-атрибуты и JS-обработчики в `task_group.html`, pay, team, support.

После изменений перечисли:
- изменённые файлы;
- какие старые стили заменены;
- где остались нестандартные кнопки (raddle retry, theme switch, rules trigger, legacy Bootstrap);
- что проверить вручную (хаб, список игр, task group / raddle / proportions, pay, team, support week_tasks/ladders, light/dark, 560/800/1000px).

### Итерация 2 (максимум) — DESIGN_SYSTEM.md

После кнопок подготовь `agents/DESIGN_SYSTEM.md` — основу авторского дизайн-языка. Не переделывай все страницы сразу.

Документ должен содержать:

1. **Дизайн-принципы** — 5–8 конкретных, применимых к Interoves (не абстракции).
2. **Цветовая система** — на базе существующих `--bg/--surface/--text/--muted/--accent/--border/--danger` и dark-вариантов; предложи недостающие (warning, focus, success) как систему; не плодить акценты. Отдельно: results-cell tokens и raddle (`--raddle-before/focus/after`) — игровые, не путать с chrome UI.
3. **Типографика** — Clear Sans / текущий стек; размеры заголовков, body, meta, вопросов/ответов, счёта; line-height/weight. Новые шрифты — только если без них нельзя добиться характера.
4. **Геометрия** — spacing scale, `.new-wrap` widths, grid rules, radius scale (не один radius на всё), borders, допустимые тени, размеры hit targets. Согласовать с `--new-break-wide` / wrap tokens.
5. **Компоненты** — описать (и где уместно слегка унифицировать) шаблоны для: кнопок; инпутов; карточек игр; карточек сессий; вопросов/вариантов ответа; таблиц результатов; рейтингов; таймеров/часов (`.new-clock`); тегов/статусов; навигации; `new-rules-modal`; уведомлений (`.new-messages`); empty/error/loading. Для каждого: назначение, варианты, состояния, пример, anti-patterns.
6. **Шаблоны страниц** — структура (не одинаковые карточки): hub (`hub.html`); каталог/folder games; страница игры (`game_page.html` / announce); игровая сессия (`task_group.html` + task cards); results; profile/team; support/admin-lite.
7. **Motion** — длительности, easing, hover, modal open/close, состояние ответа (correct/wrong), loading, этапы игры; уважать `prefers-reduced-motion`.
8. **Accessibility** — контраст light/dark, keyboard, focus-visible, hit targets, aria у модалок (уже есть паттерн), ошибки не только цветом.

После документа предложи следующие **3–5 компонентов** для переработки (например: inputs, game cards, modals, messages, results cells).

## Технические ограничения

- Не переписывать проект целиком; не трогать бизнес-логику без необходимости.
- Не ставить большие UI-библиотеки; не добавлять зависимости, если хватает CSS/существующего стека.
- Не плодить per-page CSS — токены и общие классы в `new.css`.
- Не удалять работающие возможности; сохранять responsive.
- Legacy Bootstrap UI и microsites не «причёсывать» в рамках этого промпта, кроме явных пересечений с `.new-btn`.
- Не делать массовые автозамены классов, если влияние неочевидно.
- После правок layout/responsive — прогон `./scripts/lint_new_ui_responsive.sh` при затрагивании соответствующих правил.
- Python/тесты — через `../venv/interoves_django/bin/python` (см. `agents/AGENTS.md`), если понадобятся проверки.

## Формат отчёта после каждой итерации

- что изменено;
- почему;
- какие файлы затронуты;
- что проверить вручную;
- риски / незавершённые места.

## Старт

Начни с аудита текущего дизайна и реализации кнопок new UI.  
Пока не переделывай весь интерфейс.
