# Agent instructions (Interoves Django)

## Python virtual environment

Use this virtualenv for **every** Python command in this project (install deps, `manage.py`, tests, one-off scripts):

| Item | Path |
|------|------|
| Root | `../venv/interoves_django` (sibling of the repo: parent directory `venv/`) |
| Python | `../venv/interoves_django/bin/python` |
| pip | `../venv/interoves_django/bin/pip` |

From the repository root (`interoves_django/`):

```bash
source ../venv/interoves_django/bin/activate
python manage.py check
python manage.py migrate
python manage.py test
```

Or without activating:

```bash
../venv/interoves_django/bin/python manage.py test
```

**Do not** use bare `python3` / system `pip` for this project unless the user explicitly wants that. **Do not** create a new venv under this repo by default.

The same requirement is mirrored in `.cursor/rules/python-venv.mdc` (`alwaysApply: true`).

## AWS / prod access (agents)

For Elastic Beanstalk, RDS, Redis (ElastiCache), IAM, and local AWS CLI with the **`ai-bot`** role, read **[agents/aws-eb.md](aws-eb.md)** — especially **Agent playbook** and **RDS / Redis**. Use `required_permissions: ["network", "all"]` when running AWS/SSH scripts from tools.

## Modals / popups (new UI)

For **new** pages under `static/templates/new/`, use **one** pattern: the `new-rules-modal` stack (see `static/css/new.css` and examples in `new/base.html`, `new/team.html`, `new/task_group.html`).

- Outer: `class="new-rules-modal"`, `role="dialog"`, `aria-modal="true"`, `hidden` by default; open with `classList.add('is-open')` and remove `hidden`, close the reverse; lock scroll on `document.body` while open.
- Backdrop: `new-rules-overlay` with a `data-*-close` handler (or equivalent) where needed.
- Content: `new-rules-modal__box pal-card`, close button `new-rules-modal__close`, **Escape** closes.
- Prefer unique `id` and `aria-labelledby` per dialog.

Do **not** add another modal library (e.g. Bootstrap modal, Magnific Popup) for new UI work; legacy templates may still use older stacks—keep new code consistent with `new-rules-modal` only.

## Deploy version banner (hard refresh hint)

**`SITE_DEPLOY_VERSION`** (see `interoves_django/settings.py`) is resolved in order: **`SITE_DEPLOY_VERSION` env** → file **`interoves_django/deploy_version.txt`** → **`git rev-parse --short HEAD`** (local dev when `.git` exists). When it changes, `deploy_version_check.js` hits **`GET /meta/deploy-version/`** (no-store), syncs `localStorage`, then **`location.replace`** with **`_interoves_cb`**.

- **Elastic Beanstalk:** repo **`deploy.sh`** runs **`scripts/write_deploy_version.sh`** before **`eb deploy`**, so each deploy carries the current git short SHA in **`deploy_version.txt`**. You can still set **`SITE_DEPLOY_VERSION`** in EB to override the file. If you deploy without `deploy.sh`, run **`./scripts/write_deploy_version.sh`** yourself first.
- **CI:** generate the same file or set the env to `$CODEBUILD_RESOLVED_SOURCE_VERSION` / `$GITHUB_SHA` / etc.

If the resolved version is empty, the client check is skipped.

## Responsive layout (new UI)

Общие правила для `static/css/new.css` и шаблонов `static/templates/new/`. Источник числовых констант — `:root` в `new.css` (`--new-break-wide`, `--new-wrap-*`, `--raddle-*`).

### Принципы

1. **Брейкпоинты компонента = брейкпоинты оболочки.** До `--new-break-wide` (920px) контент живёт в узком `.new-wrap` (`--new-wrap-narrow`, 36rem). Двухколоночные блоки не должны оставаться в две колонки на этом диапазоне.
2. **Нет «полумёртвых» колонок.** Если места нет на читаемую левую и правую зону — переключай layout целиком (stack / collapse), а не сжимай обе до полоски.
3. **Минимальная ширина зон.** У сайдбаров и вторичных колонок — `minmax(<min>, 1fr)` с осмысленным `<min>` (для raddle: `--raddle-clues-min`).
4. **Контент сжимается, не вылезает.** У grid/flex-потомков `min-width: 0`; у полей ввода `min-width: 0` + `max-width: 100%`; у текста `overflow-wrap: break-word`.
5. **Container queries для компонентов.** Viewport media query недостаточен, если карточка может быть уже страницы (multi-column task grid). Двухпанельные компоненты задают `container-type: inline-size` на корневом элементе.
6. **Одно поведение на диапазон ширины.** На каждом интервале — однозначный режим (stack или columns), без промежуточного «чуть-чуть правой панели».

### Константы (`new.css` `:root`)

| Переменная | Значение | Назначение |
|------------|----------|------------|
| `--new-break-wide` | 920px | `.new-wrap` / nav расширяются от этой ширины viewport |
| `--new-wrap-narrow` | 36rem | max-width страницы до брейкпоинта |
| `--new-wrap-wide` | 60rem | max-width страницы после брейкпоинта |
| `--raddle-ladder-max` | 26rem | левая колонка лестницы |
| `--raddle-clues-min` | 12rem | минимум правой колонки подсказок |
| `--raddle-layout-stack` | 40rem | container query: stack, если карточка уже |

`@media` не принимает `var()` во всех браузерах — в запросах оставляем литералы `920px` / `919px` с комментарием «must match `--new-break-wide`». Линтер проверяет согласованность.

### PR checklist (двухпанельный / responsive UI)

Перед merge изменений в layout `new.css` или шаблоны с двумя панелями:

- [ ] Брейкпоинт stack совпадает с `--new-break-wide` (или обоснован и задокументирован)?
- [ ] Есть `container-type` на корне компонента, если layout зависит от ширины карточки?
- [ ] У колонок grid/flex стоит `min-width: 0`?
- [ ] Ручная проверка на **560px**, **800px**, **1000px** viewport: нет обрезанных подсказок и вылезающих полей?
- [ ] `./scripts/lint_new_ui_responsive.sh` проходит?

### Автопроверка

```bash
./scripts/lint_new_ui_responsive.sh
```

Скрипт также вызывается из `run_tests.sh` перед Django-тестами.

## Raddle (Лесенка)

Auto-submit, контракт `/send_attempt/` и smoke перед деплоем: **[agents/raddle.md](raddle.md)**. Матрица сценариев в `games/raddle_response_contract.py`; тесты: `games.tests.test_raddle_send_attempt`, `games.tests.test_raddle_response_contract`.
