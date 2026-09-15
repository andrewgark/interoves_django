# ЮKassa: самостоятельная отвязка способа оплаты

Production-автоплатежи остаются выключенными: `YOOKASSA_RECURRING_ENABLED=False`.
Включать их можно только после явного подтверждения ЮKassa. Первый этап —
банковские карты. Другие методы требуют отдельного последующего подключения.

## Поведение

- «Отключить автопродление» оставляет сохранённую карту. До конца оплаченного
  периода пользователь может возобновить автопродление.
- «Отвязать карту» доступно на `/subscription/` независимо от статуса подписки,
  наличия профиля и включения checkout/recurring. После подтверждения
  `POST /api/payment-method/detach/` сразу удаляет все сохранённые токены ЮKassa
  текущего пользователя и отключает продление ЮKassa. Endpoint требует
  сессию и CSRF; идентификаторы карт и пользователей из запроса не используются.
- `paid_until` не меняется. Это граница оплаченного доступа (эквивалент
  `current_period_end`). `auto_renew=False` и `next_charge_at=NULL` — эквивалент
  `cancel_at_period_end=True`.
- После отвязки подписка оформляется обычным initial flow, с новым вводом карты
  и `save_payment_method=true`. Можно оплатить сразу: новый месяц начинается
  после уже оплаченного периода (или сейчас, если доступ истёк). Использованная
  introductory price не восстанавливается: следующий initial стоит 900 ₽.
- До подтверждённого сервером `payment.succeeded`, `saved is True` и типа
  `bank_card` новый способ оплаты не сохраняется.

## Данные и миграция

Только `SavedPaymentMethod.provider_payment_method_id` содержит повторно
используемый токен. `ClubSubscription` ссылается на внутренний ID модели.
`ClubYooKassaPayment` хранит ID платежа и ключ идемпотентности, но не токен карты
и не полный ответ провайдера. При отвязке токен становится NULL, последние
четыре цифры очищаются, остаются внутренний ID и даты для аудита.

Из дополнительных карточных данных сохраняются исключительно четыре ASCII-цифры
`payment_method.card.last4`, если они есть в проверенном ответе. Установленный
SDK рекурсивно преобразует `Payment.find_one()` через `dict(payment)`.
Номер, первые шесть цифр, срок действия, CVV/CVC и произвольный `title` не сохраняются.
Контракт: [привязка во время платежа](https://yookassa.ru/developers/payment-acceptance/scenario-extensions/recurring-payments/save-payment-method/save-during-payment).

Миграция `0209_saved_payment_method` переносит legacy-токены и удаляет старую
колонку `ClubSubscription.yookassa_payment_method_id`. Старый код не сохранял тип
способа оплаты: для перенесённых записей тип остаётся неизвестным, новые списания
отключаются; запись доступна для отвязки. Новая обычная оплата подтверждает тип.
Обратная миграция намеренно запрещена, чтобы не восстанавливать удалённые токены.
При выкладке остановить старые billing workers и старые процессы приложения до
смены схемы; recurring flag должен оставаться выключенным. Старый код не совместим
с удалённой колонкой.

## Конкурентность и незавершённые платежи

Порядок блокировок: пользователь → подписка → платёж/способ оплаты. Этот порядок
используют initial checkout, cancel, resume, detach, webhook и renewal.
На MySQL/Postgres это `SELECT FOR UPDATE`; для локальной SQLite используется
write lock. Проверка нового списания происходит под той же блокировкой,
что и отвязка, непосредственно перед обращением к ЮKassa; блокировка удерживается
до окончания HTTP-вызова. После успешного ответа detach новый запрос со старой
картой отправлен быть не может.

Попытка продления фиксируется отдельной транзакцией до обращения к провайдеру:
даже авария процесса не стирает свидетельство возможного списания. `submitted_at`
означает начало попытки отправки, а не доказательство её получения ЮKassa.
После фиксации попытки worker повторно получает блокировку и проверяет отвязку.
Автоматически повторно отправлять незавершённую попытку нельзя.

Резервирование помечает попытку `submission_reserved`. Отдельная транзакция
перед HTTP-вызовом переводит её в `submission_unknown` и фиксирует единственное
право на отправку. Повторный вызов dispatcher, в том числе после падения процесса,
не отправляет эту попытку снова. После получения права worker ещё раз проверяет
состояние подписки под общей блокировкой с detach. Авария между фиксацией права
и HTTP-вызовом оставляет неопределённую попытку для сверки; автоматического
повтора нет. Legacy pending-попытки без маркера резерва также не отправляются.

Если запрос уже отправлен, отвязка не отменяет его у ЮKassa. UI предупреждает,
что ранее запущенный платёж ещё может завершиться. Предупреждение консервативно
показывается и для попыток с неизвестным результатом после сбоя/таймаута.
Предупреждение остаётся на `/subscription/` при повторных открытиях страницы
до подтверждённого success/canceled, даже после прочтения сообщения об отвязке.
Поздний success продлевает оплаченный доступ, но не сохраняет старую карту
и не включает отменённое автопродление. На timeout сохраняется `submission_unknown`,
а не ложный статус «отменён». HTTP-клиент Club использует connect/read timeout
3.05/10 секунд без автоматических повторов. Неопределённые результаты сверяются
по verified webhook/ID платежа; отправлять их заново после detach нельзя.

Аудит после commit: `payment_method_saved`, `payment_method_detached`,
`subscription_auto_renew_disabled`; только внутренние ID, без provider token.
Отдельный API-вызов ЮKassa для отвязки не выполняется.

## Staff-only approval preview: скриншоты до подключения платежей

URL: **https://interoves.com/subscription/?approval_preview=1**.

Условия доступа: авторизованная сессия, `is_staff` **или** `is_superuser`,
точное значение `approval_preview=1`. Для остальных запросов параметр игнорируется:
пользователь получает обычную страницу и свои настоящие способы оплаты, если они есть.
Наличие профиля, подписки, карты и значения billing feature flags не требуются.

Preview передаёт только значения для отображения в существующий production-шаблон:
«Банковская карта •••• 4242» и демонстрационную дату через календарный месяц.
Записи моделей не создаются, реальные способы оплаты не загружаются, provider token
не используется. Страница помечена как предпросмотр для согласования, `noindex`
и `private, no-store`. Она не записывает событие просмотра checkout в аналитику.

Карточка, confirmation dialog и блок результата используют одну разметку
с настоящим интерфейсом. Подтверждение в preview только скрывает карточку и открывает
существующий блок «Карта отвязана / Автопродление отключено» в DOM. Ни POST,
ни запросов к ЮKassa нет. В HTML preview нет action настоящего detach endpoint:
без JavaScript форма делает безопасный GET этой же страницы. Обновление страницы
восстанавливает демонстрационную карту. Реальный endpoint и его auth/CSRF не меняются.

### Deployment checklist (выполняется оператором, не автоматически)

1. Просмотреть и зафиксировать в deploy-коммите изменения подписки и preview.
   Новых миграций для самого preview нет. Перед выпуском выполнить:

   ```bash
   ../venv/interoves_django/bin/python manage.py test games.tests.test_subscription_approval_preview games.tests.test_club_yookassa games.tests.test_club_subscription --noinput
   INTEROVES_APPROVAL_PREVIEW_BROWSER_TESTS=1 ../venv/interoves_django/bin/python manage.py test games.tests.test_subscription_approval_preview --noinput
   ../venv/interoves_django/bin/python scripts/check_inline_js_syntax.py static/templates/new/subscription.html
   bash scripts/lint_new_ui_responsive.sh
   git diff --check
   ```

   Browser-тест требует установленного Playwright Chromium и возможности запускать
   браузер (в агентском sandbox может потребоваться запуск вне sandbox).
   Все обращения браузера перехватываются локально; платежей тесты не проводят.

2. Проверить production-флаги, выводя только эти два значения:

   ```bash
   ./scripts/eb_run.sh manage.py shell -c 'from django.conf import settings; print("CLUB_YOOKASSA_ENABLED", settings.CLUB_YOOKASSA_ENABLED); print("YOOKASSA_RECURRING_ENABLED", settings.YOOKASSA_RECURRING_ENABLED); assert not settings.CLUB_YOOKASSA_ENABLED and not settings.YOOKASSA_RECURRING_ENABLED'
   ./scripts/eb_run.sh manage.py showmigrations games
   ```

   Оба флага должны оставаться `False`. Credentials не менять, никаких платежей
   не проводить. Если `0209_saved_payment_method` ещё не применена, учесть описанный
   выше несовместимый переход: старые процессы не должны работать с новой схемой.
   Проверить план миграций выпуска до выкладки; preview не требует заполнения БД.

3. Выполнить штатный `./deploy.sh`. Он формирует deploy version, запускает
   `eb deploy interoves-env` и smoke страниц; EB выполняет миграции и collectstatic.
   Проверить успешное завершение обновления и `./scripts/aws_with_role.sh eb status`.

4. После выкладки выполнить `./scripts/eb_run.sh manage.py check --database default`,
   `./scripts/eb_run.sh manage.py migrate --check` и повторить проверку двух флагов
   из шага 2. Они по-прежнему должны быть `False`.

5. Войти на interoves.com существующим staff/superuser аккаунтом. Открыть URL выше.
   Снять три экрана **с видимой настоящей адресной строкой браузера**:
   - «Способ оплаты / Банковская карта •••• 4242 / Отвязать карту»;
   - после нажатия — confirmation dialog;
   - после подтверждения — «Карта отвязана / Автопродление отключено» и дату доступа.
   В Network подтверждение не должно отправлять запрос к `/api/payment-method/detach/`.
   Reload возвращает карточку и позволяет повторить съёмку.

6. Проверить этот URL без авторизации и обычным аккаунтом: демонстрационной карты
   и preview-режима нет. Обычный `/subscription/` под staff тоже работает как прежде.
   В письме ЮKassa указать, что снимки демонстрируют интерфейс до подключения
   автоплатежей. Они не являются доказательством проведённого платежа или удаления
   настоящего токена. Recurring включать только отдельным решением после согласования.

## Отдельная проверка настоящей отвязки после разрешения тестового платежа

1. Применить миграции и выложить код с выключенным production recurring.
2. Выполнить согласованный тестовый initial-платёж и дождаться подтверждения
   сохранения карты. Не подставлять вымышленные production-токены.
3. Открыть `https://interoves.com/subscription/` в обычном desktop-браузере.
4. Снять экран с настоящей адресной строкой и блоком «Способ оплаты» / «Отвязать карту».
5. Нажать «Отвязать карту» и снять confirmation dialog с видимым URL.
6. Подтвердить и снять результат «Карта отвязана», «Автопродление отключено»,
   дату сохранённого доступа. Если есть незавершённый платёж, сохранить предупреждение.
7. Проверить БД, выводя только безопасные поля:

```python
SavedPaymentMethod.objects.filter(user_id=USER_ID, provider='yookassa').values(
    'id', 'is_active', 'detached_at',
)
assert not SavedPaymentMethod.objects.filter(
    user_id=USER_ID, provider='yookassa',
    provider_payment_method_id__isnull=False,
).exists()
sub = ClubSubscription.objects.get(user_id=USER_ID)
assert sub.saved_payment_method_id is None
assert not sub.auto_renew and sub.next_charge_at is None
assert sub.paid_until == paid_until_before_detach
```

Снимки headless-браузера не включают настоящую адресную строку: это локальные
UI-превью, а не готовые доказательства для службы безопасности ЮKassa.

Тесты: `games.tests.test_club_yookassa`, `games.tests.test_club_yookassa_race`.
Race-тесты используют потоки и отдельные соединения. Для SQLite задать файловый
`DATABASES['default']['TEST']['NAME']`; shared-memory SQLite не ждёт table locks.
Перед активацией прогнать те же race-тесты на тестовой MySQL, как в production.

Descriptor согласовывается вне приложения и в коде не задан.
