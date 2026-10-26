# Разработка и поддержание структуры

Этот документ описывает безопасный рабочий процесс для изменений в репозитории.
Он дополняет предметные инструкции из `agents/AGENTS.md`, а не заменяет их.

## Быстрый старт и проверки

Из корня репозитория используется проектное virtualenv:

```bash
../venv/interoves_django/bin/python manage.py check
./run_tests.sh
./scripts/lint_python.sh          # ruff on *.py changed vs origin/master
```

Конфиг Ruff: [`ruff.toml`](../ruff.toml) (см. [`docs/code-health-roadmap.md`](code-health-roadmap.md) §2).

Для узкого изменения предпочтительнее сначала запускать тесты соответствующего
домена, например:

```bash
./run_tests.sh games.tests.test_raddle
```

`run_tests.sh` — обязательная точка входа для локальных Django-тестов: он
запускает responsive/tooltip lint, [`scripts/lint_python.sh`](../scripts/lint_python.sh)
(ruff по изменённым и незакоммиченным `*.py`), использует persistent SQLite test DB
и `--keepdb`, поэтому не пересоздаёт тестовую схему из сотен миграций при каждом запуске. Для параллельного запуска
задай отдельный `INTEROVES_TEST_DB_PATH`.

### Если `--keepdb` ведёт себя странно

Persistent SQLite (`interoves_django.test_settings`) ускоряет прогоны, но **старая
файловая БД** может расходиться с миграциями и фикстурами: массовые `DoesNotExist`,
`IntegrityError`, «closed database» в websocket-тестах. Симптом — сотни ошибок на
полном `./run_tests.sh`, тогда как узкий прогон на новом файле проходит.

Пересоздать test DB:

```bash
./scripts/reset_test_db.sh
./run_tests.sh
```

Или явно другой файл:

```bash
INTEROVES_TEST_DB_PATH=/tmp/interoves_test.sqlite3 ./scripts/reset_test_db.sh
INTEROVES_TEST_DB_PATH=/tmp/interoves_test.sqlite3 ./run_tests.sh
```

Путь по умолчанию: `$TMPDIR/interoves-django-test.sqlite3` (см. `INTEROVES_TEST_DB_PATH`
в [`interoves_django/test_settings.py`](../interoves_django/test_settings.py)).

Специализированные проверки из `agents/AGENTS.md` обязательны, если изменение
затрагивает соответствующую область: responsive UI, realtime, Raddle, deploy
или внешние интеграции.

## Где искать код

- `games/models/` — модели и локальные инварианты данных.
- `games/puzzles/markers/` — типы заданий без обычной проверки ответа (метка, автоподсказка, раздача по командам).
- `games/site/` — HTTP. Экраны текущего интерфейса в `games/site/pages/`, прочие обработчики в `games/site/views/`, плюс `urls` и `http`. `games/site/data/` — JSON для legacy meta100 endpoints (`meta.py`).
- Имена игр: лесенка — каталог `ladder`, движок `games/puzzles/raddle/`, тип задания `raddle`; салатик — движок `games/puzzles/salad/`, каталог и тип `word_salad`. Алфавитки и Цензорли: `alphabetty`, `censorly`. Офлайн-инструменты Цензорли — `games/puzzles/censorly/offline/`.
- `games/identity/` — кто играет (`actor`, `profile`), склейка аккаунтов (`merge/`), allauth-адаптер и Telegram OIDC.
- `games/console/` — консоль поддержки (`/support/`). Лесенка внутри консоли — `games.console.games.ladder`.
- `games/task_group/` — контекст карточки задания (навигация, заголовки, состояние). Очереди воркеров — `games/ops/workers/`. Их HTTP-обработчики — `games/ops/workers/handlers/` (шим `games.site.views.workers`).
- Тело ежедневного тайминга — `games/daily/timing/session.py`, тело проекций — `games/daily/projection/materialize.py`. Импорт по-прежнему `games.daily.timing` и `games.daily.projection`: пакет реэкспортирует реализацию.
- `games/management/commands/` — административные и разовые команды.
- `scripts/` — deploy, cron, smoke-тесты и операционные инструменты.
- `static/templates/site/` — экраны текущего интерфейса. Рядом в `static/templates/` остаются старые `results.html`, `team.html`, `game.html`. Стили — `static/css/new.css` и `static/css/`, скрипты — `static/js/`. Классы `new-rules-modal` без переименования.
- `microsites/` и `nutrimatic_bundle/` — отдельные сайты и их bundles.
- `docs/` — архитектурные решения и контракты.
- `agents/` — доп. контекст для AI/агентов (deploy, UI, raddle и т.д.).
- `artifacts/` — сохранённые аудиты, CSV/JSON-отчёты и одноразовые планы
  ([`artifacts/README.md`](../artifacts/README.md)).
- `infra/` — шаблоны AWS/EB/ECS и IAM policy JSON ([`infra/iam/`](infra/iam/)).
- `artifacts/ads/` — состояние Yandex Direct CLI (`games.ops.social.ads`).

Новые каталоги `services/`, `selectors/`, `policies/` или `integrations/` не
создаются заранее. Они появляются только при наличии нескольких связанных
модулей с устойчивой общей ответственностью.

## Правила изменений

1. Один change-set — одна смысловая задача. Рефакторинг и изменение поведения
   по возможности разделяются.
2. Перед добавлением нового решения нужно найти ближайший существующий аналог
   и либо переиспользовать его, либо явно объяснить расхождение.
3. Общий helper выделяется только после сравнения всех вариантов поведения.
   Одинаковый текст ещё не означает одинаковую семантику.
4. После третьего устойчивого повторения код становится кандидатом на общую
   функцию или сервис. После двух повторений копипаста может быть безопаснее.
5. Не создавать универсальные `utils.py`, `helpers.py` и аналогичные свалки.
   Общий код должен иметь тематическое имя и понятный слой.
6. View должна координировать запрос, доступ и response; сложное изменение
   состояния выносится в доменную операцию, когда это уже повторяется или
   мешает читать endpoint.
7. Не скрывать ошибки широким `except Exception` без причины, логирования и
   теста ожидаемого fallback-поведения.
8. Не менять публичные URL, webhook-контракты, worker endpoints и миграции в
   рамках косметического структурного рефакторинга.
9. Не делать массовое форматирование старых файлов ради отдельного изменения.
   Форматирование применяется к изменяемому участку или отдельным механическим
   коммитом.
10. При переносе кода сначала сохранить поведение и старые импорты, если это
    возможно; удаление совместимости делается отдельным осознанным изменением.

## Обязательное описание изменения

Для структурных изменений в сообщении коммита или PR указать:

```text
Scope:
Behavior changed:
Files intentionally not touched:
Tests:
Follow-up:
```

Если тест не запускался, это указывается явно вместе с причиной.

## Направление зависимостей

Новые зависимости между слоями по умолчанию должны идти так:

```text
views -> domain operations / selectors / policies
tasks -> domain operations / selectors
integrations -> domain operations
models -> не импортируют views и integrations
```

Это ориентир, а не повод немедленно перестраивать весь старый код. При
изменении существующего модуля сначала сохраняется его контракт, затем
маленькими шагами уменьшается связность.
