# Interoves Design System (new UI)

Рабочий документ для `static/css/new.css` и шаблонов `static/templates/new/`.  
Legacy Bootstrap UI, microsites и Django admin — вне скоупа, кроме явных пересечений с `.new-btn`.

Источник правды по токенам и компонентам — этот файл + `:root` / `html.new-ui-dark` в `new.css`.  
Промпт рефакторинга: [`design-refresh-prompt.md`](design-refresh-prompt.md).

---

## 1. Дизайн-принципы

1. **Игра важнее chrome.** Навигация, кнопки и карточки не спорят с условием, полем ответа и фидбеком «верно / неверно».
2. **Один акцент на блок.** В смысловом блоке — одна primary-кнопка; остальное outline / text / secondary.
3. **Токены вместо магии.** Цвета, радиусы, высоты кнопок и брейкпоинты задаются CSS-переменными; хардкод `#6aaa64` / случайный `border-radius` в компонентах — долг.
4. **Характер через типографику и ритм, не через декор.** Без градиентных «hero», glow, glass и oversized radius ради красоты.
5. **Разные типы контента — разная структура.** Карточка игры ≠ карточка правил ≠ таблица результатов ≠ модалка подтверждения.
6. **Светлая и тёмная тема — одна система.** Любой новый цвет получает пару в `html.new-ui-dark`.
7. **Состояния объясняют действие.** Hover / focus-visible / disabled / loading / wrong-answer меняют понятный параметр (контраст, рамка, спиннер), а не украшают.
8. **Расширяемость без UI-kit.** Новые экраны собираются из токенов и существующих классов; React/Bootstrap-компоненты для new UI не вводим.

---

## 2. Цветовая система

### Chrome UI (`:root` / `html.new-ui-dark`)

| Роль | Переменная | Light (текущее) | Назначение |
|------|------------|-----------------|------------|
| Фон страницы | `--bg` | `#f9f9f9` | Оболочка |
| Поверхность | `--surface` | `#ffffff` | Карточки, инпуты |
| Плитка / secondary fill | `--tile` | `#e8e8e8` | Secondary btn, chips |
| Текст | `--text` | `#1a1a1a` | Основной |
| Вторичный текст | `--muted` | `#6b6b6b` | Подписи, meta |
| Рамка | `--border` | `#d3d6da` | Разделители, outline |
| Акцент | `--accent` / `--accent-hover` | `#1f6f5e` / `#185849` (dark: `#3fb89a` / `#5ec9ad`) | Primary, chrome |
| Опасность | `--danger` / `--danger-soft` | `#b61f1f` / rgba… | Destructive |
| Предупреждение | `--warning` / `--warning-hover` | `#b8953d` / `#a18232` | Билеты, донат CTA |
| Успех (семантика) | `--success` + soft bg/border | = accent family | Сообщения, solved |
| Фокус | `--focus-ring` | mix accent | Кольцо focus-visible |

Тёмная тема переопределяет те же имена в `html.new-ui-dark`.  
Фон light: `#f5f7f6` (лёгкий холодный оттенок, не Wordle `#f9f9f9`).

### Игровые палитры (не смешивать с chrome)

- **Results cells:** `--results-cell-*-bg/fg`, `--results-row-me-*` (своя легенда; не обязана совпадать с accent).
- **Raddle:** `--raddle-before`, `--raddle-focus` (= accent), `--raddle-after`.

### Чего не делать

- Не возвращать Wordle `#6aaa64` и legacy SaaS-синий `#6372ff` в new UI.
- Не использовать градиенты как основной фон страницы.
- Не плодить accent-цвета «для каждой игры» в chrome; игровой цвет — локально в игровом блоке.

### Accent (зафиксировано)

Глубокий teal `#1f6f5e` — интеллектуальный, не Wordle-clone и не purple SaaS. Менять только через переменные в `:root` / `html.new-ui-dark`.

---

## 3. Типографика

| Роль | Значение |
|------|----------|
| UI stack | `"Clear Sans", "Helvetica Neue", Arial, sans-serif` (`body.new-ui`) |
| Моноширинный (лестница, коды) | `ui-monospace, SF Mono, Menlo, Consolas, monospace` |
| Акцентный | Пока не вводим; `.pal-*` тянет Inter — это **не** системный шрифт, подлежит выравниванию |

### Шкала (ориентир)

| Стиль | Размер | Weight | Применение |
|-------|--------|--------|------------|
| Page title (`.new-heading`) | ~1.5–1.75rem | 700 | Заголовок экрана |
| Section title | ~1.1rem | 700 | Hub section, team blocks |
| Body | 1rem / 16px root | 400 | Основной текст |
| Button | `var(--btn-font)` 0.9rem | 600 | `.new-btn` |
| Meta / hint | 0.8–0.9rem | 400 | `.new-login-hint`, muted |
| Uppercase label | 0.8rem | 400–700 | `.new-form label` |
| Score / game number | mono или 700 | — | Результаты, raddle |

Line-height: UI ~1.5; заголовки ~1.15–1.3; плотные meta ~1.35.

**Не подключать** новые веб-шрифты без явной причины и проверки лицензии/веса бандла.

---

## 4. Геометрия

### Контейнер и брейкпоинты

| Токен | Значение | Смысл |
|-------|----------|-------|
| `--new-break-wide` | 920px | Nav / wrap расширяются |
| `--new-wrap-narrow` | 36rem | До брейкпоинта |
| `--new-wrap-wide` | 60rem | После брейкпоинта |
| `--raddle-layout-stack` | 40rem | Container query stack |

Правила responsive: `agents/AGENTS.md`. Проверка: `./scripts/lint_new_ui_responsive.sh`.

### Spacing (рекомендуемая шкала)

`0.25 / 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2 / 3 rem` — предпочитать эти шаги в новых правилах.

### Radius

| Токен / правило | Значение | Где |
|-----------------|----------|-----|
| `--btn-radius` | 6px | Кнопки, компактные контролы |
| Control | 4–6px | Инпуты, hub section border |
| Card | 8–10px | Team hub sections, task cards |
| Pill | 999px | Clock, theme switch, status pills |
| **Избегать** | 18–24px повсюду | Сейчас в `.pal-card` — долг |

### Тени

Допустимы: лёгкий lift `0 2px 10px` на крупных блоках; focus ring; inset marker у raddle.  
Недопустимы: многослойный glow, «SaaS card stack», тень ради декора на каждой плитке.

### Hit targets

Минимум ~32–36px (`--btn-height-mini` / `--btn-height`). Icon-only: квадрат той же высоты.

---

## 5. Компоненты

### 5.1 Кнопки (реализовано)

**Классы:** `.new-btn` + модификаторы.

| Вариант | Класс | Когда |
|---------|-------|-------|
| Primary | `.new-btn` | Главное действие блока |
| Secondary | `.new-btn--secondary` | Альтернатива без акцента |
| Outline | `.new-btn--outline` или `.new-btn--ghost` (alias) | Вторичные действия в ряду |
| Text / quiet | `.new-btn--text` | Отмена, «назад», низкий приоритет |
| Destructive | `.new-btn--danger` | Выход из команды, необратимое |
| Warning CTA | `.new-btn--yellow` | Билеты / донат (осознанный exception) |
| Mini | `.new-btn--mini` | Плотные ряды (game card actions) |
| Icon-only | `.new-btn--icon` | Квадратная кнопка |
| Loading | `.new-btn--loading` / `.is-loading` | Ожидание ответа |

Состояния: `:hover`, `:active`, `:focus-visible`, `:disabled`, `a[aria-disabled]`.  
Spacing: у `.new-btn` нет дефолтного `margin-top`; у `.new-form > .new-btn` — `1rem`.

**Не следует:** градиенты, толстая «пластиковая» тень, `border-radius: 999px` на обычных CTA, две primary рядом.

### 5.2 Поля ввода — частично сделано

- База: `.new-form input/select/textarea` — `min-height: var(--btn-height)`, `border-radius: var(--btn-radius)`, focus ring `--focus-ring`, disabled-состояние.
- Игровые: attempt row подтянут к тому же focus ring; raddle / alphabetty / proportions — ещё локально.
- **Anti-pattern:** inline `style` на инпутах; разные толщины рамки в одном ряду без причины.

### 5.3 Карточки игр — сделано (CSS)

- `.new-game-card` + `__actions` / `__rowactions`: radius 8px, title 700, actions в ряд с wrap.
- Hover — лёгкая смена border к accent mix, без тени.
- Одна primary («Играть» / «Зарегистрироваться»), остальное `--ghost --mini`.
- **Anti-pattern:** одинаковый визуальный вес у всех ссылок; колонка кнопок на всю высоту без нужды.

### 5.4 Карточки сессий / hub — сделано (CSS)

- `.new-hub-section`: border 1px, radius 8px; today = inset accent bar (не double-ring glow).
- CTA модификаторы `--today` / `--latest` / `--solved` через токены.
- Алиасы `.new-daily-ladder*` выровнены с hub.

### 5.5 Вопросы и ответы — частично сделано

- Task card: radius `--card-radius`, meta bar на control tokens.
- Attempt / alphabetty / raddle inputs + proportions slots: общий focus ring, `--control-radius` / `--control-height`.
- Compact controls: `.new-rules-trigger`, `.new-eye-toggle`, like/text buttons с `:focus-visible`.
- Фидбек — цвет + текст/иконка состояния, не только цвет.

### 5.6 Таблицы результатов

- Токены `--results-cell-*`; контраст fg/bg обязателен в light и dark.

### 5.7 Рейтинги / таймеры

- `.new-clock` — pill; не дублировать как кнопку.
- Таймеры игры — локально; не анимировать непрерывно без пользы (`prefers-reduced-motion`).

### 5.8 Теги и статусы

- `.new-pill`, `.new-pill--ok` и т.п. — компактные; не превратить в кнопки.

### 5.9 Навигация

- `.new-nav` sticky; бренд текстом; ссылки muted → text на hover.
- Секции (Десяточки / Лесенка / …) — **только иконки** Phosphor; название в `title` + `aria-label` (`.new-nav__section-label` visually hidden). «Игры», «Профиль», «Команда» — текстом.
- Theme switch / mode toggle / team subnav — один язык segmented/control (`--control-radius`, surface fill).
- Иконки chrome — только Phosphor; FA в new UI не подключать.
- Rules / eye / likes / raddle tip — `ph-question`, `ph-eye`, `ph-thumbs-*`, `ph-lightbulb`.
- Back / forward — `.new-back` / `.new-fwd` + `ph-arrow-left` / `ph-arrow-right` (не unicode `←`/`→`).
- Segmented — `.new-segmented` (+ aliases team/replacements).
- Цвета иконок: `danger` = wrong/dislike; `warning` = lightbulb tip; `accent` = ok/like/hub (вкл. star); `muted` = idle chrome → accent на hover. Утилиты: `.new-ph--*`.

### 5.10 Модалки

- Только `new-rules-modal` + overlay + `__box` (+ Escape, `aria-modal`, scroll lock). См. `agents/AGENTS.md`.
- Действия внизу: flex + gap; primary + outline/text.
- **Anti-pattern:** Bootstrap modal, второй стек попапов.

### 5.11 Уведомления — сделано (CSS)

- `.new-messages` `.success` / `.error` / `.warning` — soft backgrounds из токенов, radius `--btn-radius`.

### 5.12 Empty / error / loading

- Empty: `.new-empty` + `__title` / `__text` / `__actions` — короткий текст + опционально одна CTA, без иллюстраций.
- Loading кнопки: `.new-btn--loading`; страничный loading — существующие паттерны progress, не skeleton-карточный шум без нужды.

### 5.13 Rules / content cards (`.pal-*`) — сделано

Переведены на chrome-токены: `inherit` font, radius 10px, accent rule numbers без glow, flat example-box без градиента и синей SaaS-палитры.  
Модальный close: квадратный `--btn-radius`, `:focus-visible`.  
Тёмная тема почти полностью через переменные (остался только чуть более сильный shadow у `.pal-card`).

---

## 6. Шаблоны страниц

| Экран | Шаблон | Структура |
|-------|--------|-----------|
| Хаб | `hub.html` + `hub_section_card.html` | Секции по продуктам, не сетка одинаковых SaaS-карточек |
| Каталог игр | `folder_games.html` + `games_list_items.html` | Список/карточки игр, плотные действия |
| Страница игры | `game_page.html`, `game_announce_page.html` | Анонс / вход в сессию |
| Сессия | `task_group.html` + `task_card.html` | Meta bar, задачи, модалки правил/подтверждений |
| Результаты | `results.html` | Таблица + фильтры, не карточки |
| Профиль / команда | `profile.html`, `team.html` | Формы и статусы |
| Оплата / донат | `pay.html`, `donate.html` | Методы как крупные secondary/primary controls |
| Support | `support/*` + `support.css` | Console на тех же `.new-btn` |

---

## 7. Motion

| Тип | Длительность | Easing |
|-----|--------------|--------|
| Hover цвет/бордер | ~120–160ms | `ease` |
| Modal open/close | коротко; без bounce | — |
| Wrong answer / shake | ≤400ms, только на поле | существующие keyframes |
| Button spinner | 0.65s linear | `new-btn-spin` |

`prefers-reduced-motion: reduce` — отключать transition/animation у `.new-btn` loading и подобных.

Анимация должна подтверждать смену состояния (отправка, ошибка, смена темы), не «оживлять» статичную страницу.

---

## 8. Accessibility

- Контраст текста/кнопок в light и dark.
- `:focus-visible` обязателен для кнопок и ключевых контролов; не снимать outline без замены.
- Клавиатура: модалки Escape + focus trap по возможности; не ломать Tab в task forms.
- Hit target ≥ ~32px.
- Модалки: `role="dialog"`, `aria-modal`, `aria-labelledby`.
- Ошибки: текст + цвет (`.new-field-error`, messages).
- Не передавать смысл только цветом ячейки results без легенды/подписи.

---

## 9. Следующие компоненты для переработки

Сделано: Phosphor + семантика; quiet back; focus-visible на nav/mode/back/pager/assist/prefix/action-link/link-muted; share copy = btn + flash «Скопировано»; pay chips squared + pay page без inline; ticket pager = `.new-results-pager`; modal `.pal-title` / `.pal-lead` без inline margin; audio progress (`.new-audio-*`); team password details (`.new-team-details` / `.new-team-pass`); Instagram = прямая ссылка на профиль (не зеркало ленты); Pigeon VPN (`.new-vpn-*`); spacing utilities (`.new-theme-line`, `.new-stack-gap*`, `.new-meta-*`). FA 4 только в legacy. Пикер: `/static/design-icon-picker.html`.

Далее (по желанию):

1. **Commit + deploy** когда визуал устроит.
2. Точечный polish order-game landing radii (10/12px → tokens), если захочется выровнять.

Недавние слайсы: support (токены радиусов, Phosphor pager/back, без layout-inline); pills squared; order-game check без ✓; team/donate/profile/results/task modals.

---

## 10. Технические якоря

| Что | Где |
|-----|-----|
| Токены + кнопки + new UI | `static/css/new.css` |
| Support overrides | `static/css/support.css` |
| Shell | `static/templates/new/base.html` |
| Agent conventions | `agents/AGENTS.md` |
| Этот документ | `agents/DESIGN_SYSTEM.md` |
