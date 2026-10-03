# AWS / Elastic Beanstalk — Green, Blue и worker-окружения

Аккаунт `916000456640`, регион `eu-central-1`. Состояние ниже проверено
2026-09-28 read-only запросами. Секреты, chat id и значения EB option settings
намеренно не приводятся.

## Кто сейчас прод

Прод для игроков — Green ALB, окружение `interoves-web-green-lb`. Cloudflare
records для `interoves.com` и `www.interoves.com` указывают на DNS-имя его
Application Load Balancer. Старое single-instance окружение
`interoves-web-green` выведено из production и удаляется. Blue,
`interoves-env`, остаётся поднятым только как аварийный откат через DNS.

На момент проверки:

- `GET https://interoves.com/health/live/` вернул `200` и `ok`;
- страница сайта отдала `data-site-deploy-version="890091c"`;
- Green и Blue были `Ready/Green`; Green имел version label
  `app-890g-260926_232655`, Blue — `app-3517-260928_022920979823`;
- четыре worker environment также были `Ready/Green`.

Для следующей проверки не доверяйте этой таблице как источнику текущего
прода: сопоставьте Cloudflare DNS, `/health/live/`, deploy version на сайте и
`describe-environments`. `deploy.sh` и старая строка `EB environment =
interoves-env` для определения прода не подходят.

| Окружение | Роль | Прод? | Примечание |
|---|---|---:|---|
| `interoves-web-green-lb` | web, `INTEROVES_RUNTIME_ROLE=web` | да | ALB, baseline 1× `t3.small`, ASG 1/2, On-Demand, health `/health/live/` |
| `interoves-env` | Blue web | нет | только rollback DNS; Blue не деплоить и не «чинить» без явного запроса |
| `interoves-background-worker` | background-worker | нет | difficulty, проекции результатов, фоновые тики |
| `interoves-identity-worker` | legacy EB identity-worker | нет | неактуальный consumer; identity merge сейчас обслуживает ECS `interoves-identity-ecs` |
| `interoves-integrations-worker` | integration-worker | нет | Telegram/Instagram/social тики и карточки игр |

Кроны Blue (`difficulty`, `telegram`, `word salad`) должны оставаться
выключенными: Blue, Green и workers делят одну RDS, поэтому Blue может
перехватить ту же работу.

## Как деплоить

### Green web

Обычный `eb deploy` checkout на `interoves-web-green-lb` запрещён. В git нет
живого `.ebextensions/zzzz-green-web.config`; такой deploy может попытаться
подменить VPC, instance type и IAM profile репозитория (`t3.small`,
`aws-elasticbeanstalk-ec2-role`) или завершиться ошибкой `You cannot remove an
environment from a VPC`.

Правильный процесс выполняет `./deploy.sh`: скачать текущий zip Green, наложить
в него код приложения, сохранить живые `.ebextensions` и `.platform`, затем
создать application version и обновить только `interoves-web-green-lb`.
Не делать `rsync --delete` по `.ebextensions` или `.platform`: в живом zip есть
хуки, которых нет в git. `eb deploy` должен быть направлен явно на
`interoves-web-green-lb` и только на заранее подготовленный такой zip.

`./deploy.sh --dry-run` только собирает и проверяет локальный bundle. Реальный
релиз выполняет `./deploy.sh` (эквивалентно `./deploy.sh --deploy`). Если EB
metadata не содержит `SourceBundle`, упаковщик проверяет стандартный retained
object `s3://elasticbeanstalk-<region>-<account>/<application>/<VersionLabel>.zip`.
Если не найден и он, упаковка останавливается; обычный deploy из git checkout
не является fallback.

В zip Green сохраняются также следующие особенности:

- `.ebignore` означает, что EB пакует рабочее дерево; `.gitignore` для этого
  не является заменой `.ebignore`;
- роль `interoves-green-web-role` может `s3:GetObject` для
  `arn:aws:s3:::interoves-django-static/*`, но не `PutObject`;
- `scripts/collectstatic_if_changed.sh` запускается на свежей инстанции;
  container command `03_collectstatic` требует, чтобы скрипт в zip сразу
  завершался с кодом 0 (этот skip остаётся только в zip, в git его не коммитить);
- статику отдельно публикует IAM user `interoves`: `AWS_PROFILE=interoves`,
  `USE_S3=1`, bucket `interoves-django-static`, `manage.py collectstatic`;
  имена файлов стабильны, без manifest hash, поэтому старый и новый static
  могут временно разъехаться.

Миграции при деплое не выполняются: `01_migrate` из `django.config` работает
только при `RUN_PRODUCTION_MIGRATIONS=true`. Колонки для нового кода сначала
накатываются через `./scripts/with_rds.sh`, затем выкладывается Green; иначе
возможен HTML 500 (например, `games_gametaskgroup.share_hash`).

### Worker environments

Каждый EB worker environment — одна очередь и один `HttpPath`. Ответ игры от
них не зависит, но все они делят ту же RDS; в комментарии `django.config`
указан лимит `max_connections=60`, у Green `ASGI_THREADS=4`.

Исключение: identity merge больше не нужно искать в EB environment. Его
актуальный consumer — ECS service `interoves-identity-ecs`, queue
`interoves-identity`, DLQ `interoves-identity-dlq`, logs
`/interoves/workers/identity`. Полная карта очередей находится в
`docs/worker-queues.md`.

`interoves-recheck-worker` деплоится своим zip и своими worker settings, не
веб-zip и не Green web ebextensions. Он переигрывает цепочки: стена,
замены, лесенка, алфавитка и салатик. Старое окружение
`interoves-word-salad-worker` удалено как legacy; очередь обслуживается только
`interoves-recheck-worker`.

`interoves-integrations-worker` имеет `USE_S3=FALSE`: файлы на его диске
исчезают при деплое. Playwright-скриншот карточки игры и текст «за день / за
час / старт / конец» Десяточки отправляет именно этот worker.

Telegram routing:

- `TELEGRAM_ANNOUNCE_CHAT_IDS` обязан быть на integrations worker; пустое
  значение оставляет тик живым и коротким, без сообщений в чаты. Переменная на
  Green или Blue этот тик не кормит;
- admin chat нужен integrations worker для отчёта и «старт через час», а Green
  — для веб-сигналов билетов и оплат;
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_USER_SESSION` и
  `TELEGRAM_CHANNEL_CHAT_ID` относятся к постам в канал, не к списку игровых
  чатов; bot token — секрет;
- integrations worker выполняет `telegram.announcements`,
  `telegram.admin_report`, `instagram.token_refresh`, `social.publish`.

### Social images for daily games

Для изображений любых ежедневных заданий в Telegram/X/Instagram/Threads всегда
использовать рендер через Playwright-скриншот соответствующей публичной страницы
(для салатика — `render_word_salad_teaser_png(...,
fallback_to_pillow=False)`). Pillow-фолбэк
для social-постов запрещён: он может отличаться от публичного UI и показать
данные, скрытые шаблоном. Если Chromium/Playwright недоступен на выбранном
инстансе, остановиться и сообщить о проблеме; не переключаться на резервный
рендер. Нужно выполнять рендер на worker/инстансе, где установлен Chromium,
либо отдельно восстановить его доступность.

Read-only snapshot option names from AWS on 2026-09-28 (наличие переменной не
означает, что этот environment должен выполнять соответствующий тик):

| Environment | Найденные `TELEGRAM_*` names |
|---|---|
| `interoves-web-green-lb` | `ADMIN_CHAT_ID`, `ANNOUNCE_CHAT_IDS`, `API_ID`, `CHANNEL_CHAT_ID`, `OIDC_CLIENT_ID`, `API_HASH`, `BOT_TOKEN`, `OIDC_CLIENT_SECRET`, `USER_SESSION`, `WEBHOOK_SECRET` |
| `interoves-env` (Blue) | `ADMIN_CHAT_ID`, `ANNOUNCE_CHAT_IDS`, `API_ID`, `NOTIFY_CHAT_ID`, `CHANNEL_CHAT_ID`, `OIDC_CLIENT_ID`, `API_HASH`, `BOT_TOKEN`, `OIDC_CLIENT_SECRET`, `USER_SESSION`, `WEBHOOK_SECRET` |
| `interoves-background-worker` | `API_HASH`, `BOT_TOKEN`, `OIDC_CLIENT_SECRET`, `USER_SESSION`, `WEBHOOK_SECRET` |
| `interoves-identity-worker` | `API_HASH`, `BOT_TOKEN`, `OIDC_CLIENT_SECRET`, `USER_SESSION`, `WEBHOOK_SECRET` |
| `interoves-integrations-worker` | `ADMIN_CHAT_ID`, `ANNOUNCE_CHAT_IDS`, `API_ID`, `CHANNEL_CHAT_ID`, `API_HASH`, `BOT_TOKEN`, `OIDC_CLIENT_SECRET`, `USER_SESSION`, `WEBHOOK_SECRET` |

Смена EB option settings — это configuration deployment и рестарт. Отдельный
restart app server уже запущенный процесс новыми options не наполняет.

## AWS и безопасные read-only проверки

Для AWS CLI используй `./scripts/aws_with_role.sh` (роль `ai-bot`), не печатай
секреты и значения options:

```bash
./scripts/aws_with_role.sh aws sts get-caller-identity
./scripts/aws_with_role.sh aws elasticbeanstalk describe-environments \
  --region eu-central-1 --application-name interoves \
  --environment-names interoves-web-green-lb interoves-env \
  interoves-background-worker interoves-identity-worker \
  interoves-integrations-worker
curl -sS https://interoves.com/health/live/
```

`./scripts/eb_run.sh` и `./scripts/with_rds.sh` по умолчанию подключаются к
`interoves-web-green-lb`/Green ALB. `eb_run.sh` выбирает running instance по EB tag и
идёт к нему через SSM `AWS-StartSSHSession`; public IP для Green не требуется.
`with_rds.sh` делает SSM RDS tunnel через Green. Blue выбирается только явно:

```bash
./scripts/eb_run.sh --environment interoves-env manage.py check --database default
./scripts/with_rds.sh --environment interoves-env manage.py migrate --plan
```

Это management access и не меняет, куда смотрит Cloudflare. Для RDS локальный
порт — `13306`; не выполняйте миграции через него без явной проверки плана.
`eb_run.sh` получает production environment из процесса Daphne и потому
предназначен прежде всего для web. Для worker management-команд нужен отдельный
worker-aware SSM режим; отсутствие Daphne на worker не означает, что нужно
подключаться к Blue.

Скрипты `eb status`, `eb logs`, `eb printenv` и команды вида `eb deploy` или
`update-environment` с `interoves-env` ниже в старых runbook-примерах означают
**Blue**, не production Green. Для Green используйте `./deploy.sh`; для worker
— `./scripts/deploy_worker.sh WORKER_ENVIRONMENT`.

## Emergency DNS rollback Green → Blue

Blue (`interoves-env`) остаётся rollback target. Перед откатом убедитесь, что
его difficulty, telegram и Word Salad jobs выключены (`CUTOVER HOLD`), процессы
не запущены, а текущие targets Blue здоровы. Не включайте эти jobs после
возврата HTTP на Blue.

Если Green доступен для проверки, порядок такой:

1. прочитать текущие Blue ASG targets и проверить их health;
2. проверить hold для Word Salad, difficulty и telegram; hourly `--health-check`
   difficulty можно оставить;
3. проверить, что `word_salad_recheck_cron.sh`, `difficulty_cron.sh` и
   `telegram_cron.sh` не выполняются;
4. только затем направить apex и `www` Cloudflare на
   `interoves-dev.eu-central-1.elasticbeanstalk.com`.

Если Green недоступен и ждать нельзя, DNS переключается первым, а holds
накладываются сразу после этого. Это degraded rollback; Blue jobs всё равно не
включать.

## RDS и известные ограничения

- Предпочтительная проверка БД из production web: `./scripts/eb_run.sh manage.py check --database default`.
- SSM-туннель: `./scripts/with_rds.sh`; по умолчанию он использует Green и `localhost:13306`.
- Секрет БД берётся через Secrets Manager; не печатать password/ARN.
- Долгие DDL и backfill выполняются отдельным background-процессом после
  совместимой state migration, а не во время EB deploy.

## Ключевые идентификаторы

| Resource | Значение |
|---|---|
| EB application | `interoves` |
| Production web | `interoves-web-green-lb` |
| Blue rollback | `interoves-env` |
| Region | `eu-central-1` |
| Account | `916000456640` |
| EC2 role Blue/legacy | `aws-elasticbeanstalk-ec2-role` |
| Green role | `interoves-green-web-role` |
