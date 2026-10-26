# Inter Oves

Django-сайт с интеллектуальными играми (daily, клуб, Telegram, workers на AWS).

## Быстрый старт

Virtualenv **рядом с репозиторием**, не внутри него: `../venv/interoves_django`.

```bash
python3 -m venv ../venv/interoves_django
../venv/interoves_django/bin/pip install -r requirements.txt

../venv/interoves_django/bin/python manage.py migrate
./run_server.sh          # или: ../venv/interoves_django/bin/python manage.py runserver
```

Секреты для локальной разработки — в `secrets/` (см. `secrets/README.md`, файлы в git не коммитятся).

## Команды

| Действие | Команда |
|----------|---------|
| Проверка Django | `../venv/interoves_django/bin/python manage.py check` |
| Тесты (lint UI + `--keepdb`) | `./run_tests.sh` |
| Один модуль тестов | `./run_tests.sh games.tests.test_raddle` |
| Миграции | `../venv/interoves_django/bin/python manage.py makemigrations games` |

Подробнее: [`docs/development.md`](docs/development.md).

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `games/` | Основное приложение: `models/`, `site/` (UI, views, HTTP), `puzzles/`, `ops/`, `daily/`, `realtime/` |
| `interoves_django/` | Settings, URLs, ASGI |
| `static/` | Шаблоны, CSS, JS |
| `scripts/` | Deploy, cron, smoke, утилиты |
| `docs/` | Архитектура и runbook’и |
| `agents/` | Конventions для разработки и агентов ([`agents/AGENTS.md`](agents/AGENTS.md)) |
| `microsites/`, `nutrimatic_bundle/` | Отдельные микросайты |
| `infra/` | AWS (EB, ECS, IAM policy templates) |
| `artifacts/` | Сохранённые аудиты и одноразовые планы |
| `secrets/` | Локальные ключи и `.env` |

Корневой [`AGENTS.md`](AGENTS.md) — указатель на `agents/`.
