# Agent instructions (Interoves Django)

Общие правила читаемости, границ слоёв и безопасного рефакторинга находятся в
[docs/development.md](../docs/development.md). План постепенного улучшения — в
[docs/code-health-roadmap.md](../docs/code-health-roadmap.md). При изменении
существующего кода сначала следуй этим документам, затем применяй предметные
правила ниже.

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

### Django tests

Run Django tests through the project wrapper from the repository root:

```bash
./run_tests.sh
./run_tests.sh games.tests.test_censorly
```

The wrapper uses `interoves_django.test_settings` and `--keepdb`, which keeps a
file-backed SQLite test database in `/tmp/interoves-django-test.sqlite3` and
avoids rebuilding hundreds of migrations on every run. Do not invoke
`manage.py test` directly for ordinary local test runs: the default SQLite
test database is in-memory and can make startup look hung while migrations
are being recreated. A separate `INTEROVES_TEST_DB_PATH` can be supplied when
running tests concurrently.

The same requirement is mirrored in `.cursor/rules/python-venv.mdc` (`alwaysApply: true`).

## Docker in the developer environment

Do not conclude that Docker is unavailable from a failed `docker compose`
probe in an agent shell. The `docker` command may resolve to
`/home/andre/bin/docker`, a wrapper around the Windows Docker CLI, while
`/usr/bin/docker` uses the native WSL socket. Verify `docker ps` in the user's
normal terminal/context before stopping Docker-related work. In the Codex
agent shell these paths may fail independently because of WSL vsock or
`/var/run/docker.sock` permissions; report the exact path/error rather than
claiming that Docker itself is not running.

### Disposable MySQL integration-test container

For MySQL-only Django tests, use a separate disposable container with known
test credentials. Do not extract credentials from an existing container's
environment. First verify Docker:

```bash
docker ps
```

Start and wait for MySQL 8:

```bash
docker run -d --name interoves-schedule-mysql-test \
  -e MYSQL_DATABASE=interoves_test \
  -e MYSQL_USER=interoves \
  -e MYSQL_PASSWORD=interoves_test_password \
  -e MYSQL_ROOT_PASSWORD=root_test_password \
  -p 13308:3306 mysql:8.0

for attempt in $(seq 1 30); do
  docker exec interoves-schedule-mysql-test \
    mysqladmin ping -uroot -proot_test_password --silent && break
  sleep 1
done
```

Run the MySQL integration test with the container connection:

```bash
RDS_HOSTNAME=127.0.0.1 \
RDS_PORT=13308 \
RDS_DB_NAME=interoves_test \
RDS_USERNAME=root \
RDS_PASSWORD=root_test_password \
../venv/interoves_django/bin/python manage.py test \
  games.tests.test_schedule_links_mysql
```

Remove only the disposable container after the test:

```bash
docker rm -f interoves-schedule-mysql-test
```

If the agent sandbox rejects Docker access, rerun these commands with the
execution tool's full permissions; `UtilBindVsockAnyPort` from the wrapper is
not sufficient evidence that Docker Engine is unavailable.

### WebSocket integration tests in agent sandboxes

The tests in `games.tests.test_track.TrackWebsocketIntegrationTests` must be run
outside the restricted agent sandbox (with the execution tool's escalation / full
permissions):

```bash
../venv/interoves_django/bin/python manage.py test games.tests.test_track.TrackWebsocketIntegrationTests
```

Inside the restricted sandbox, Channels can stall in
`aclose_old_connections()` / its thread-sensitive `database_sync_to_async`
executor before the consumer's `connect()` runs. The tests then report a
`WebsocketCommunicator.connect()` timeout even when the WebSocket implementation
is healthy. Do not treat that sandbox-only result as a regression and do not
change production or test code to accommodate it; rerun the same tests outside
the sandbox first.

## AWS / prod access (agents)

For Elastic Beanstalk, RDS, Redis (ElastiCache), IAM, and local AWS CLI with the **`ai-bot`** role, read **[agents/aws-eb.md](aws-eb.md)**. Production web is the **Green ALB environment** (`interoves-web-green-lb`); `interoves-env` is **Blue rollback**. `deploy.sh` is the Green production entrypoint; `eb_run.sh` and `with_rds.sh` default to the active Green ALB and accept `--environment` for an explicit target; workers use `scripts/deploy_worker.sh`. Use `required_permissions: ["network", "all"]` when running AWS/SSH scripts from tools.

For deploy tasks, also use [`.cursor/skills/interoves-deploy/SKILL.md`](../.cursor/skills/interoves-deploy/SKILL.md) to decide whether `BUNDLE_MICROSITES=1` is needed. Ordinary Django deploys leave it unset.

## Social queue (schedule posts)

To schedule Telegram / X / Instagram posts into prod `SocialQueuePost`, use the personal skill **`interoves-social-queue`** (`~/.cursor/skills/interoves-social-queue/SKILL.md`): `POST /support/social/create/` with `mode=tg_defer`. Do not upload images via `eb_run`/SCP.

## Modals / popups (new UI)

For **new** pages under `static/templates/new/`, use **one** pattern: the `new-rules-modal` stack (see `static/css/new.css` and examples in `new/base.html`, `new/team.html`, `new/task_group.html`).

- Outer: `class="new-rules-modal"`, `role="dialog"`, `aria-modal="true"`, `hidden` by default; open with `classList.add('is-open')` and remove `hidden`, close the reverse; lock scroll on `document.body` while open.
- Backdrop: `new-rules-overlay` with a `data-*-close` handler (or equivalent) where needed.
- Content: `new-rules-modal__box pal-card`, close button `new-rules-modal__close`, **Escape** closes.
- Prefer unique `id` and `aria-labelledby` per dialog.

Do **not** add another modal library (e.g. Bootstrap modal, Magnific Popup) for new UI work; legacy templates may still use older stacks—keep new code consistent with `new-rules-modal` only.

## Deploy version banner (hard refresh hint)

**`SITE_DEPLOY_VERSION`** (see `interoves_django/settings.py`) is resolved in order: **`SITE_DEPLOY_VERSION` env** → file **`interoves_django/deploy_version.txt`** → **`git rev-parse --short HEAD`** (local dev when `.git` exists). When it changes, `deploy_version_check.js` hits **`GET /meta/deploy-version/`** (no-store), syncs `localStorage`, then **`location.replace`** with **`_interoves_cb`**.

- **Elastic Beanstalk:** repo **`deploy.sh`** is the production Green entrypoint. It snapshots committed HEAD, prepares a bundle from the current `interoves-web-green-lb` source bundle, preserves live `.ebextensions`/`.platform`, and updates only `interoves-web-green-lb`. It does not deploy Blue. Green packaging must not replace live `.ebextensions` or `.platform` from the git tree.
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

## Games app layout (refactor)

Новый код в **`games/`** клади в предметные пакеты; корень приложения держи тонким.

**Facade retirement (2026-10):** удалены ~120 корневых `games/*.py` aliases и каталоги `games/views`, `games/support`, `games/telegram`, `games/social`, `games/instagram`, `games/twitter`, `games/threads`, `games/background`, `games/tutorials`, `games/google`, `games/matcher` — prod/tests уже на canonical paths. **Не удаляй** entry points ниже и puzzle bootstrap (`games.wall`, `games.word_salad`, …). Имена **loggers** (`games.views.new_ui`, `games.daily_timing`, …) намеренно legacy; `assertLogs` и prod-логи на них завязаны.

**Оставшиеся entry shims (Django / bootstrap):** `games.admin`, `games.storage_backends`, `games.provider` (allauth autodiscover), `games.templatetags.filters`, `games.wall` / `word_salad` / `raddle` / `grid_puzzle` / `replacements_lines`, пакеты **`games.censorly`** (`__init__` + `models`), **`games.alphabetty`** (`models` + `core` для migration `0150`), **`salad` / `ladder`** (model load only). Ladder tutorial: **`games.daily.ladder.tutorial`**. Django settings: HTTP **`games.site.http.*`**, inlineedit **`games.site.http.adaptors`**. Yandex Direct — только **`games.ops.social.ads.*`** (каталог `games/ads_direct` удалён).

| Concern | Canonical import | Notes |
|--------|------------------|-------|
| Checkers + `CheckerFactory` | `games.puzzles.core.check_runtime` | — |
| HTTP / main UI handlers | `games.site.views.*`, `games.site.ui.*` | `games.views.*` removed |
| Live track / WebSocket helpers | `games.realtime.track_compat` | logger `games.views.track` |
| MySQL lock retry helpers | `games.realtime.db` | — |
| ASGI DB admission middleware | `games.realtime.admission` | — |
| HTTP middleware (timing, anon identity, DB transient) | `games.site.http.middleware.*` | `settings.MIDDLEWARE` |
| Yandex Direct ads CLI | `games.ops.social.ads.*` | — |
| Main UI symbol bundle | `games.site.views.pages.ui_exports` | logger `games.views.new_ui` |
| Norm matching / pymorphy | `games.puzzles.core.norm_matcher` | — |
| Censorly IDs / flags | `games.puzzles.censorly.ids` | `games.censorly` package re-export |
| Censorly implementation | `games.puzzles.censorly.*` | — |
| Grid wall/shading puzzle | `games.puzzles.replacements.grid_puzzle` | bootstrap: `games.grid_puzzle` |
| SQS worker handlers | `games.site.views.workers.{background,identity,integrations,word_salad}` | — |
| Worker registry / ECS config / Lambda SQS | `games.ops.workers.{contract,config,lambda_entry}` | — |
| Gameplay write actor (team/user/anon) | `games.identity` (`games.identity.actor`) | — |
| Profile/team presence on User | `games.identity.profile` or `games.identity` | site views / UI import `games.identity` |
| Account / ticket / subscription | `games.site.views.account.*` | — |
| Daily play (alphabetty, censorly) | `games.site.views.daily.*` | — |
| Recheck / chain replay jobs | `games.ops.workers.jobs.recheck` | — |
| Word salad async recheck queue | `games.ops.workers.jobs.salad` | `games.word_salad_recheck` |
| Recheck outbox transport | `games.ops.workers.jobs.outbox` | `games.word_salad_outbox` |
| Auth/session gameplay diagnostics | `games.site.http.auth_observability` | `games.apps` ready hook |
| Account / anonymous merge | `games.club.merge.{account,anon,anonymous,queue,events}` | — |
| Task group navigation | `games.tasks.navigation` | — |
| HTML-forms puzzle rendering | `games.puzzles.replacements.forms` | — |
| Results leaderboard semantics | `games.results.standings.leaderboard` | — |
| Daily difficulty cache | `games.daily.difficulty.{core,refresh,health}` | — |
| Admin/support attempt ops | `games.ops.workers.jobs.actions` | — |
| Attempt text normalize helpers | `games.results.util` | `games.util` |
| Section URLs / hub / share hashes | `games.sections.{paths,hub,navigation,placement_share,week_task_pool,project_navigation}` | — |
| Daily result projection | `games.daily.projection` (+ `projection.cron`) | — |
| Targeted completion reconcile | `games.daily.completion.targeted_completion_reconciliation` | — |
| Chain replay planning | `games.site.session.replay_planner` | — |
| Weekly task catalog | `games.daily.catalog.week_task` | — |
| Next-game vote campaign | `games.club.vote` | — |
| Actor task scoring / progress SQL | `games.daily.completion.scoring` | — |
| Raddle response contract matrix | `games.puzzles.raddle.contract` | — |
| Telegram OIDC auth | `games.club.telegram_oidc` | `games.urls`, tests |
| Template context processors | `games.site.http.context` | `settings.TEMPLATES` |
| Daily schedule / section page | `games.daily.page.section` | — |
| Daily archive UI helpers | `games.daily.page.archive` | — |
| Ladder daily catalog | `games.daily.catalog.ladder` | — |
| Club archive / subscription gate | `games.club.access.archive` | — |
| Daily catalog (censorly / alphabetty / salad) | `games.daily.catalog.{censorly,alphabetty,word_salad}` | — |
| Player offers (ladder / alphabetty / censorly / salad) | `games.club.offers.*` | — |
| Replay session | `games.site.session.replay` (+ `replay_navigation`) | — |
| Gameplay context / play mode | `games.site.session.{gameplay_context,play_mode}` (`effective_play_mode`, `get_play_mode`, …) | import `games.site.session.play_mode` in views |
| Offer profile names | `games.club.offers.profiles` | — |
| Results snapshot builders | `games.results.snapshots.snapshot` | — |
| Logical game completion | `games.daily.completion.completion_coordinator` | — |
| Daily timing core | `games.daily.timing` (+ `timing.reset`) | — |
| Daily share card PNG/payload | `games.daily.page.share.{card,render}` | — |
| Projection / stats refresh events | `games.realtime.projection` | — |
| EB worker poll/config/contract | `games.ops.workers.{http,config,contract,polling,lambda_entry,heartbeat,messages}` | — |
| Site HTTP forms | `games.site.forms` | — |
| Django admin registrations | `games.site.admin` | `games.admin` (autodiscover entry) |
| Template filter helpers | `games.site.http.tags.filters` | `games.templatetags.filters` (Django tag library path) |
| Distributed cron locks | `games.ops.cron.lock` | — |
| EB runtime role / heavy-processing gate | `games.ops.cron.runtime` | — |
| Profile feedback reports | `games.site.ui.profile.feedback` | — |
| Product metrics CLI | `games.ops.analytics.metrics` | — |
| Media storage health check | `games.ops.health.checks` | — |
| Daily UI transitions / streak | `games.daily.timing.{transitions,streak}` | — |
| Task display titles | `games.tasks.titles` | — |
| Chain task state lookup (board/daily kwargs) | `games.tasks.actor_state.chain_state_for_actor` | — |
| Chain state for results-table actors | `games.tasks.result_details.chain_state_for_actor` (signature `game, task, actor`) | — |
| Game access / schedule gates | `games.club.access.game` | — |
| Attempt/view exceptions | `games.site.http.exceptions` | — |
| Daily statistics API | `games.daily.timing.statistics` | — |
| Telegram offer linking | `games.club.link` | — |
| Allauth custom providers (`provider_classes`) | `games.club.provider` | `games.provider` (allauth autodiscover) |
| Telegram bot/channels/images/transport | `games.ops.telegram.*`, `games.ops.cron.telegram` | `games.telegram.*` (facades; settings may still reference legacy app path) |
| Support console / observatory | `games.ops.support.{http,feed,games,people,observatory,google}` | — |
| Product analytics persistence | `games.ops.analytics.persistence` | — |
| Social queue / Instagram API | `games.ops.social.{models,publish,views,instagram.*}` | — |
| Raddle engine helpers | `games.puzzles.raddle.engine` | — |
| Raddle DB locks | `games.puzzles.raddle.concurrency` | — |
| Admin bulk recheck actions | `games.ops.workers.jobs.bulk` | — |
| Product analytics | `games.ops.analytics.{events,identity,persistence}` | — |
| Ticket billing service | `games.club.billing.tickets` | — |
| Pay page ticket routes | `games.club.billing.routes` | — |
| Club YooKassa / access service | `games.club.billing.yookassa.{club,client}`, `games.club.access.service` | — |
| Social queue / static S3 backends | `games.ops.health.storage` | `games.storage_backends` shim for migrations only; `settings.STORAGES` → canonical |
| Subscription gifts | `games.club.billing.gifts.{offers,payments}` | — |
| Donations / crypto / YooKassa util | `games.club.billing.{donations,crypto,yookassa.util}` | `donation_service`, `nowpayments_util`, `yookassa_util` |
| Tribute checkout (prod entry) | `games.club.billing.tribute.service` | — |
| Tribute config / webhook HMAC | `games.club.billing.tribute.{config,util}` | `games.tribute_config`, `tribute_util` |
| Results share formatting | `games.results.share` | — |
| SQL results aggregate | `games.results.sql` | — |
| JSON body / public task / browser-form HTTP helpers | `games.site.http.request_util` | — |
| Puzzle engines (wall / salad / replacements) | `games.puzzles.{wall.engine,salad.rules,replacements.*}` | `games.wall`, `word_salad`, `replacements_*` |
| Ladder onboarding tutorial | `games.daily.ladder.tutorial.{ladder,views}` | `games.ladder` — только models bootstrap |
| Alphabetty engine | `games.puzzles.alphabetty.*` | `games.alphabetty.*` |
| Alphabetty/Censorly offer models in `models/__init__` | `games.alphabetty.models` / `games.censorly.models` (+ `games.alphabetty.core` for migration `0150`) |
На старте Django (до готовности `games.models`) не импортируй `games.puzzles.*` на уровне модуля: `games.wall` / `games.word_salad` грузят engine/rules через `importlib` без `games.puzzles.__init__`. Константа `WORD_SALAD_GAME_ID` живёт в `games.daily.page.section` (не тянет puzzles); `games.puzzles.salad.rules` только re-export для legacy. `models.catalog.Wall` — `games.wall`.

Правила: не менять URL names, checker type ids, `app_label`, схему БД. **Новый код** — только canonical import; не добавляй новые root shims (entry points — см. выше). В prod и тестах не импортируй удалённые `games.*` aliases; для `@patch` предпочитай модуль, где имя **привязано** (не legacy `games.views.*`):

| Symbol | Patch target |
|---|---|
| `check_attempt` / send-attempt flow | `games.site.views.game.attempt.track_actor_task_change` |
| Raddle assist/UI commits | `games.site.views.game.raddle.track_actor_task_change` |
| Recheck / word-salad actor notify | `games.ops.workers.jobs.recheck.track_actor_task_change` |
| `Task.save` / `Hint.save` admin broadcast | `games.realtime.track_compat.track_task_change` (lazy import inside model `save`) |
| Salad queue post-commit notify | `games.realtime.track_compat.track_actor_task_change` (lazy import in `ops.workers.jobs.salad._schedule_actor_notification`) |

URLConf imports view callables from `games.site.views.{account,game,pages,results}.*` directly; worker code — same game modules.

## Raddle (Лесенка)

Auto-submit, контракт `/send_attempt/` и smoke перед деплоем: **[agents/raddle.md](raddle.md)**. Матрица сценариев в `games/puzzles/raddle/contract.py`; тесты: `games.tests.test_raddle_send_attempt`, `games.tests.test_raddle_response_contract`.

## Realtime / team synchronization

For WebSocket, live task state, teammate synchronization, pending rechecks, and clock-driven UI changes, use the repo skill **`.cursor/skills/interoves-realtime/SKILL.md`** and read **[docs/realtime-architecture.md](../docs/realtime-architecture.md)**.

## Daily-game difficulty cache

Star ratings for ladder / alphabetty / salad are stored snapshots, refreshed by a minute cron (not on page load). See **[docs/difficulty-cache.md](../docs/difficulty-cache.md)**.
