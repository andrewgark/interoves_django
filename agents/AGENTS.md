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

| Concern | Canonical import | Legacy shim |
|--------|------------------|-------------|
| Checkers + `CheckerFactory` | `games.puzzles.core.check_runtime` | `games.check` (`sys.modules` → `check_runtime`) |
| HTTP / main UI handlers | `games.site.views.*`, `games.site.ui.*` | `games.views.*` (facades) |
| Live track / WebSocket helpers | `games.realtime.track_compat` | `games.views.track` |
| MySQL lock retry helpers | `games.realtime.db` | `games.db_retry` |
| ASGI DB admission middleware | `games.realtime.admission` | `games.asgi_admission` |
| Yandex Direct ads CLI | `games.ops.social.ads.*` | `games.ads_direct.*` |
| Main UI symbol bundle | `games.site.views.pages.ui_exports` | `games.views.new_ui` |
| Norm matching / pymorphy | `games.puzzles.core.norm_matcher` | `games.matcher.norm_matcher` |
| Censorly IDs / flags | `games.puzzles.censorly.ids` | `games.censorly` (re-export без `games.puzzles` при import) |
| Censorly implementation | `games.puzzles.censorly.*` | `games.censorly.*` (module facades) |
| Grid wall/shading puzzle | `games.puzzles.replacements.grid_puzzle` | `games.grid_puzzle` |
| SQS worker handlers | `games.site.views.workers.{background,identity,integrations,word_salad}` | `games.views.*_worker` |
| Account / ticket / subscription | `games.site.views.account.*` | `games.views.ticket`, `subscription`, … |
| Daily play (alphabetty, censorly) | `games.site.views.daily.*` | `games.views.alphabetty_views`, `censorly_views` |
| Recheck / chain replay jobs | `games.ops.workers.jobs.recheck` | `games.recheck` |
| Word salad async recheck queue | `games.ops.workers.jobs.salad` | `games.word_salad_recheck` |
| Recheck outbox transport | `games.ops.workers.jobs.outbox` | `games.word_salad_outbox` |
| Auth/session gameplay diagnostics | `games.site.http.auth_observability` | `games.auth_observability` |
| Account / anonymous merge | `games.club.merge.{account,anon,anonymous,queue,events}` | `games.account_merge`, `anon_migrate`, … |
| Task group navigation | `games.tasks.navigation` | `games.task_group_navigation` |
| HTML-forms puzzle rendering | `games.puzzles.replacements.forms` | `games.html_forms` |
| Results leaderboard semantics | `games.results.standings.leaderboard` | `games.leaderboard` |
| Daily difficulty cache | `games.daily.difficulty.{core,refresh,health}` | `games.difficulty`, `difficulty_refresh`, `difficulty_health` |
| Admin/support attempt ops | `games.ops.workers.jobs.actions` | `games.ops_actions` |
| Attempt text normalize helpers | `games.results.util` | `games.util` |
| Section URLs / hub / share hashes | `games.sections.{paths,hub,navigation,placement_share,week_task_pool,project_navigation}` | `games.section_*`, `placement_share`, … |
| Daily result projection | `games.daily.projection` (+ `projection.cron`) | `games.daily_result_projection*` |
| Targeted completion reconcile | `games.daily.completion.targeted_completion_reconciliation` | `games.targeted_completion_reconciliation` |
| Chain replay planning | `games.site.session.replay_planner` | `games.replay_planner` |
| Weekly task catalog | `games.daily.catalog.week_task` | `games.week_task_weekly` |
| Next-game vote campaign | `games.club.vote` | `games.next_game_vote` |
| Actor task scoring / progress SQL | `games.daily.completion.scoring` | `games.scoring` |
| Raddle response contract matrix | `games.puzzles.raddle.contract` | `games.raddle_response_contract` |
| Telegram OIDC auth | `games.club.telegram_oidc` | `games.telegram_oidc` |
| Template context processors | `games.site.http.context` | `games.context_processors` (settings keep legacy strings) |
| Daily schedule / section page | `games.daily.page.section` | `games.daily_section` |
| Daily archive UI helpers | `games.daily.page.archive` | `games.daily_archive` |
| Ladder daily catalog | `games.daily.catalog.ladder` | `games.ladder_daily` |
| Club archive / subscription gate | `games.club.access.archive` | `games.club_access` |
| Daily catalog (censorly / alphabetty / salad) | `games.daily.catalog.{censorly,alphabetty,word_salad}` | `games.*_daily` (except ladder → `catalog.ladder`) |
| Player offers (ladder / alphabetty / censorly / salad) | `games.club.offers.*` | `games.*_offer` |
| Replay session | `games.site.session.replay` (+ `replay_navigation`) | `games.replay`, `replay_navigation` |
| Gameplay context / play mode | `games.site.session.{gameplay_context,play_mode}` | `games.gameplay_context`, `play_mode` |
| Offer profile names | `games.club.offers.profiles` | `games.offer_profiles` |
| Results snapshot builders | `games.results.snapshots.snapshot` | `games.results_snapshot` |
| Logical game completion | `games.daily.completion.completion_coordinator` | `games.completion_coordinator` |
| Daily timing core | `games.daily.timing` (+ `timing.reset`) | `games.daily_timing`, `daily_progress_reset` |
| Daily share card PNG/payload | `games.daily.page.share.{card,render}` | `games.daily_share_card`, `daily_share_card_render` |
| Projection / stats refresh events | `games.realtime.projection` | `games.projection_events` |
| EB worker poll/config/contract | `games.ops.workers.{http,config,contract,polling,lambda_entry,heartbeat,messages}` | `games.worker_*`, `queue_heartbeat`, `background.messages` |
| Site HTTP forms | `games.site.forms` | `games.forms` |
| Django admin registrations | `games.site.admin` | `games.admin` (autodiscover entry) |
| Distributed cron locks | `games.ops.cron.lock` | `games.cron_lock` |
| EB runtime role / heavy-processing gate | `games.ops.cron.runtime` | `games.runtime` |
| Profile feedback reports | `games.site.ui.profile.feedback` | `games.feedback` |
| Product metrics CLI | `games.ops.analytics.metrics` | `games.product_metrics` |
| Media storage health check | `games.ops.health.checks` | `games.health_checks` |
| Daily UI transitions / streak | `games.daily.timing.{transitions,streak}` | `games.daily_transitions`, `daily_streak` |
| Task display titles | `games.tasks.titles` | `games.task_titles` |
| Game access / schedule gates | `games.club.access.game` | `games.access` |
| Attempt/view exceptions | `games.site.http.exceptions` | `games.exception` |
| Daily statistics API | `games.daily.timing.statistics` | `games.daily_statistics` |
| Telegram offer linking | `games.club.link` | `games.telegram_linking` |
| Telegram bot/channels/images/transport | `games.ops.telegram.*`, `games.ops.cron.telegram` | `games.telegram.*` (facades; settings may still reference legacy app path) |
| Support console / observatory | `games.ops.support.{http,feed,games,people,observatory,google}` | `games.support.*` |
| Product analytics persistence | `games.ops.analytics.persistence` | `games.analytics_persistence` |
| Social queue / Instagram API | `games.ops.social.{models,publish,views,instagram.*}` | `games.social.*`, `games.instagram.*` |
| Raddle engine helpers | `games.puzzles.raddle.engine` | `games.raddle` |
| Raddle DB locks | `games.puzzles.raddle.concurrency` | `games.raddle_concurrency` |
| Admin bulk recheck actions | `games.ops.workers.jobs.bulk` | `games.attempt_admin_bulk` |
| Product analytics | `games.ops.analytics.{events,identity,persistence}` | `games.analytics*` |
| Ticket billing service | `games.club.billing.tickets` | `games.ticket_service` |
| Pay page ticket routes | `games.club.billing.routes` | `games.payment_routes` |
| Club YooKassa / access service | `games.club.billing.yookassa.{club,client}`, `games.club.access.service` | `games.club_yookassa`, `club_yookassa_client`, `club_service` |
| Social queue / static S3 backends | `games.ops.health.storage` | `games.storage_backends` (settings/migrations keep legacy path) |
| Subscription gifts | `games.club.billing.gifts.{offers,payments}` | `games.subscription_gifts`, `subscription_gift_payments` |
| Donations / crypto / YooKassa util | `games.club.billing.{donations,crypto,yookassa.util}` | `donation_service`, `nowpayments_util`, `yookassa_util` |
| Tribute checkout (prod entry) | `games.club.billing.tribute.service` | `games.tribute_service` |
| Tribute config / webhook HMAC | `games.club.billing.tribute.{config,util}` | `games.tribute_config`, `tribute_util` |
| Results share formatting | `games.results.share` | `games.share_result` |
| SQL results aggregate | `games.results.sql` | `games.results_sql_aggregate` |
| Puzzle engines (wall / salad / replacements) | `games.puzzles.{wall.engine,salad.rules,replacements.*}` | `games.wall`, `word_salad`, `replacements_*` |
| Ladder onboarding tutorial | `games.ladder.tutorial.{ladder,views}` | `games.tutorials.*` |
| Alphabetty engine | `games.puzzles.alphabetty.*` | `games.alphabetty.*` |
| Alphabetty/Censorly offer models in `models/__init__` | keep `games.alphabetty.models` / `games.censorly.models` facades (avoid `games.puzzles` at Django model load) |

На старте Django (до готовности `games.models`) не импортируй `games.puzzles.*` на уровне модуля: `games.wall` / `games.word_salad` грузят engine/rules через `importlib` без `games.puzzles.__init__`. Константа `WORD_SALAD_GAME_ID` живёт в `games.daily.page.section` (не тянет puzzles); `models.catalog.Wall` — `games.wall`.

Правила: не менять URL names, checker type ids, `app_label`, схему БД; фасады — `import sys` + `sys.modules[__name__] = implementation` (кроме осознанных re-export, напр. `results_sql_aggregate`, `games.censorly` package `__init__` с ids). В prod и тестах импортируй canonical path; для `@patch` предпочитай модуль, где имя привязано (`games.realtime.track_compat`, `games.site.views.game.attempt`, …), а не legacy `games.views.*`.

## Raddle (Лесенка)

Auto-submit, контракт `/send_attempt/` и smoke перед деплоем: **[agents/raddle.md](raddle.md)**. Матрица сценариев в `games/puzzles/raddle/contract.py`; тесты: `games.tests.test_raddle_send_attempt`, `games.tests.test_raddle_response_contract`.

## Realtime / team synchronization

For WebSocket, live task state, teammate synchronization, pending rechecks, and clock-driven UI changes, use the repo skill **`.cursor/skills/interoves-realtime/SKILL.md`** and read **[docs/realtime-architecture.md](../docs/realtime-architecture.md)**.

## Daily-game difficulty cache

Star ratings for ladder / alphabetty / salad are stored snapshots, refreshed by a minute cron (not on page load). See **[docs/difficulty-cache.md](../docs/difficulty-cache.md)**.
