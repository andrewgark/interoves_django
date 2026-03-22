# ElastiCache Redis для Django Channels (Interoves)

Проект уже переключает `CHANNEL_LAYERS` на Redis, если заданы переменные окружения `REDIS_HOST` и `REDIS_PORT` (см. `interoves_django/settings.py`).

Ниже — **оценка класса ноды** и **настройка в AWS** для типичного сценария: WebSocket (track), несколько инстансов Elastic Beanstalk.

---

## 1. Какой класс ноды выбрать

### Что нагружает Redis здесь

| Нагрузка | Порядок величины |
|----------|------------------|
| **Channels** | Pub/sub + короткоживущие сообщения между воркерами; память растёт с числом **активных** подписок и **частотой** сообщений. |
| **Счётчики `seq` в треке** | Сейчас через `django.core.cache` (по умолчанию локальный кэш). Если позже переведёте `CACHES` на Redis — добавится немного ключей и операций `INCR`. |

Точный расход памяти зависит от числа одновременных WebSocket, числа групп и частоты `group_send`. Для ориентира:

| Ожидаемая нагрузка | Рекомендация по ноде | Память |
|--------------------|----------------------|--------|
| Разработка / очень мало пользователей онлайн | `cache.t4g.micro` | 0.5 GiB |
| Небольшой прод: десятки–сотня одновременных WS, умеренный трафик | **`cache.t4g.small`** | 1 GiB |
| Рост: сотни одновременных WS или частые пуши | `cache.t4g.medium` | 2 GiB |
| Высокая нагрузка | Смотреть метрики (CPU, память, `CurrConnections`) и повышать класс | 4+ GiB |

**Практическая рекомендация для первого прода:** начать с **`cache.t4g.small`**: запас по памяти, стабильнее при пиках, разница в цене с `micro` обычно умеренная. Если бюджет жмёт и нагрузка минимальная — **`cache.t4g.micro`** допустим как старт с обязательным мониторингом.

**Почему t4g (Graviton):** обычно дешевле x86 при сопоставимой производительности для Redis.

---

## 2. «Кластер» в терминах AWS и что выбрать

В ElastiCache есть два разных понятия:

1. **Replication group (репликация)** — один **primary** и опционально **replicas** для чтения и отказоустойчивости. **Cluster mode = Disabled** — **один шард**, один URL primary endpoint. Так проще всего подключать **`channels_redis`** (один хост:порт).
2. **Cluster mode enabled** — несколько **шардов** (настоящее шардирование ключей). Для `channels_redis` нужна отдельная конфигурация под Redis Cluster; для вашего текущего кода в `settings.py` **не требуется**, пока нет экстремальной нагрузки.

**Итого:** для этого проекта настройте **Redis OSS** с **Cluster mode disabled** (один шард), при желании включите **1 read replica** для HA. Полноценный «Redis Cluster» с несколькими шардами **не обязателен** и усложняет клиент.

---

## 3. Пошаговая настройка в AWS (консоль)

### 3.1. Сеть

1. **Subnet group** для ElastiCache: **VPC та же**, что у Elastic Beanstalk; подсети **private** (рекомендуется), в нескольких AZ при multi-AZ.
2. **Security group для Redis** (например `sg-redis-interoves`):
   - **Inbound:** TCP **6379** (или порт, который вы зададите в ElastiCache) **Source** = security group **инстансов EB** (или SG, с которой ходят к бэкенду приложения).
   - **Outbound:** по умолчанию достаточно или `0.0.0.0/0` для обновлений — не открывайте Redis в интернет.

### 3.2. Создание кластера

1. Консоль **ElastiCache** → **Redis OSS** → **Create**.
2. **Cluster mode:** **Disabled** (один шард).
3. **Engine version:** актуальный **Redis 7.x** (совместим с `channels_redis`).
4. **Node type:** например **`cache.t4g.small`** (см. раздел 1).
5. **Replicas:** для прод — `1` replica в другой AZ (если нужна отказоустойчивость); для минимального бюджета можно `0` и принять риск перезапуска при падении ноды.
6. **Subnet group:** вы выбрали выше.
7. **Security groups:** SG Redis, созданный в 3.1.
8. **Encryption in transit:** если в ElastiCache включён **TLS (Required)**, в EB задайте **`REDIS_TLS=1`** — в `settings.py` используется `rediss://` (см. `interoves_django/settings.py`). Внутри VPC без TLS можно оставить transit encryption выключенным — тогда `REDIS_TLS` не задаёте.
9. Создайте кластер и дождитесь статуса **Available**.

### 3.3. Endpoint и порт

1. В карточке кластера откройте **Primary endpoint** / **Configuration endpoint** (для single shard — primary).
2. **Host** — без `redis://`, только hostname, например:  
   `master.xxxxx.use1.cache.amazonaws.com`
3. **Port** — обычно **6379** (см. в консоли).

Переменные окружения в EB (минимум):

| Переменная | Обязательно | Значение |
|------------|-------------|----------|
| `REDIS_HOST` | да | hostname primary endpoint (без схемы) |
| `REDIS_PORT` | нет | по умолчанию `6379` |
| `REDIS_TLS` | нет | `1` / `true` / `yes`, если в ElastiCache включён **encryption in transit** |
| `REDIS_PASSWORD` | нет | если включён AUTH в Redis |
| `REDIS_SSL_CERT_REQS` | нет | `none` — отключить проверку сертификата (только для самоподписанного dev-Redis; для ElastiCache обычно не нужно) |

После деплоя приложение подхватит Redis для `CHANNEL_LAYERS`.

---

## 4. Elastic Beanstalk: куда прописать переменные

1. **EC2 → Elastic Beanstalk** → ваше окружение → **Configuration** → **Software** → **Environment properties**.
2. Добавьте как минимум `REDIS_HOST` (и при TLS — `REDIS_TLS=1`). См. таблицу выше.

Сохраните и перезапустите приложение (или примените конфигурацию).

---

## 5. Проверка

- Логи воркера: при старте не должно быть ошибок подключения к Redis.
- При необходимости с инстанса EB (SSM Session Manager / bastion):  
  `redis-cli -h <REDIS_HOST> -p 6379 ping` → `PONG` (если security group и CLI позволяют).

---

## 6. Дальше (по желанию)

- **Мониторинг:** CloudWatch метрики ElastiCache — `CPUUtilization`, `DatabaseMemoryUsagePercentage`, `CurrConnections`, `NetworkBytesIn/Out`.
- **Кэш Django для `seq`:** если нужен общий `seq` на всех инстансах — настройте `CACHES` на Redis (тот же кластер или отдельная DB index в Redis через `OPTIONS`); это отдельная правка `settings.py`.
- **Планировщик** по времени старта/конца игры — см. обсуждение cron/celery; для него Redis не обязателен.

---

## 7. Краткая шпаргалка

| Параметр | Рекомендация |
|----------|----------------|
| Режим | Redis OSS, **cluster mode disabled** |
| Класс | Старт прода: **`cache.t4g.small`**; минимум: **`cache.t4g.micro`** |
| Сеть | Та же VPC, private subnets, SG: только от EB |
| Env | `REDIS_HOST`, `REDIS_PORT`; при TLS в ElastiCache — **`REDIS_TLS=1`** |

---

*Цены зависят от региона и типа ноды — смотрите [AWS ElastiCache pricing](https://aws.amazon.com/elasticache/pricing/) и [Pricing Calculator](https://calculator.aws/).*
