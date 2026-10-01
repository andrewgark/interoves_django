# Платные подарочные подписки

Этот документ описывает отдельный поток покупки подарка. Его нельзя строить на
рекуррентной клубной подписке: текущий YooKassa-flow сохраняет платёжный метод
для автопродления, а текущие товары Tribute являются месячными подписками.

## Пользовательский сценарий

1. На `/subscription/` пользователь нажимает «Подарить подписку» и выбирает
   срок: 1 или 3 месяца.
2. Сайт показывает две независимые кнопки оплаты: рубли через одноразовый
   YooKassa-платёж и евро через одноразовый Tribute Digital Product.
3. После подтверждённой оплаты сайт показывает одноразовый код вида
   `IO-XXXX-XXXX`. Покупатель может скопировать его и отправить получателю.
   Код не активирует подарок у покупателя автоматически.
4. Получатель входит в свой Inter Oves-аккаунт, вводит код в уже существующей
   форме «Активировать подарок». Выданное право добавляется к текущему сроку,
   а не перезаписывает его.

Email и автоматическая отправка в Telegram не являются обязательными для первой
версии: без подтверждённого канала доставки нельзя раскрывать код третьей
стороне. Позже можно добавить отправку Telegram только если получатель уже
однозначно известен по Telegram ID.

## Данные

Расширить `SubscriptionGift`:

- `status`: `created`, `paid`, `claimed`, `expired`, `revoked`;
- `duration_months` оставить источником срока подарка;
- `code_hash` хранить как сейчас, открытый код хранить в зашифрованном виде
  для показа покупателю;
- `purchaser`, `provider`, `amount`, `currency`;
- `paid_at`, `expires_at`, `claimed_by`, `claimed_at`;
- уникальные ограничения на `(provider, provider_payment_id)` и на
  `code_hash`.

Лучше добавить отдельную модель `SubscriptionGiftPayment` для платёжной
попытки, а не перегружать `ClubYooKassaPayment` и `ClubSubscriptionEvent`:

- `gift`, `purchaser`, `provider`, `provider_payment_id`, `idempotency_key`;
- `duration_months`, `amount`, `currency`, `status`;
- `confirmation_url`, `raw_event_excerpt`, `succeeded_at`, `created_at`.

После успешного webhook создать `ClubEntitlement(kind='gift', gift=gift)` с
получателем только при активации кода. До активации подарок не должен давать
доступ покупателю или неизвестному пользователю.

## YooKassa

Добавить отдельный `start_gift_yookassa` и отдельный webhook-обработчик.
Платёж создаётся с `save_payment_method=false`, без `payment_method_id` и без
каких-либо записей в `SavedPaymentMethod`. В metadata передавать только
`purpose=club_gift`, `gift_payment_id`, `gift_id`, `duration_months` и
`purchaser_id`. Сумму брать только из серверной таблицы тарифов, не из POST.

Обработчик webhook должен быть идемпотентным: проверить назначение, валюту,
сумму, статус и соответствие локальному payment ID; затем в одной транзакции
пометить платёж успешным и подарок `paid`. Повторный webhook не должен создать
новый код или второй подарок. При `payment.canceled` доступ и код не выдаются.

## Tribute

Нужны отдельные одноразовые продукты Tribute для каждого разрешённого срока
(`gift_1` и `gift_3`) в валюте EUR. Нельзя
переиспользовать `TRIBUTE_CLUB_SUBSCRIPTION_EUR_*`: это recurring-продукт, а
его webhook семантически привязывает оплату к Telegram подписчику.

Уже созданные ссылки продуктов:

| Срок | Tribute URL |
|---|---|
| 1 месяц | `https://web.tribute.tg/p/FOI` |
| 3 месяца | `https://web.tribute.tg/p/FOL` |

В конфигурации хранить product ID, URL, валюту и сумму для каждого срока;
сервер выбирает конфигурацию по сроку. Перед редиректом создать локальную
`SubscriptionGiftPayment` со случайным токеном correlation, а webhook сначала
идентифицировать по product ID и correlation/покупателю. Если Tribute не
возвращает достаточно данных для надёжного сопоставления с локальным заказом,
платёж отправлять в `unmatched` и разбирать администратором, не выдавая код
автоматически.

Пока в production сохранены только публичные URL продуктов:

```text
TRIBUTE_CLUB_GIFT_EUR_1_URL=https://web.tribute.tg/p/FOI
TRIBUTE_CLUB_GIFT_EUR_1_ID=160748
TRIBUTE_CLUB_GIFT_EUR_1_AMOUNT=555
TRIBUTE_CLUB_GIFT_EUR_3_URL=https://web.tribute.tg/p/FOL
TRIBUTE_CLUB_GIFT_EUR_3_ID=160751
TRIBUTE_CLUB_GIFT_EUR_3_AMOUNT=1665

CLUB_GIFT_YOOKASSA_1_AMOUNT_KOPECKS=60000
CLUB_GIFT_YOOKASSA_3_AMOUNT_KOPECKS=180000
```

Numeric ID и цены уже получены через Tribute API и сохранены в конфигурации;
URL сам по себе был бы недостаточен для надёжного сопоставления Tribute
webhook.

## Безопасность и эксплуатация

- CSRF на старте платежа; rate limit на ввод кодов (10 попыток в минуту).
- Код показывать только покупателю после подтверждённого webhook; в логах,
  аналитике и webhook-аудите хранить только hash/маскированный идентификатор.
- Не принимать срок, цену, валюту или provider из доверенного redirect.
- `select_for_update()` при claim; повторный claim должен быть безопасным.
- Указать срок действия неоплаченного заказа и срок неактивированного подарка,
  правила возврата и ручной revoke в админке.
- Неоднозначные ответы YooKassa оставлять в `manual_review`, а не отменять
  локально: webhook может прийти после таймаута запроса.
- Команда `expire_subscription_gifts` должна запускаться ежечасно через
  production scheduler.
- Задать стабильный `SUBSCRIPTION_GIFT_ENCRYPTION_KEY` в Secrets Manager;
  ротация `DJANGO_SECRET_KEY` не должна ломать старые коды.
- Тесты: тарифы/валидация, запрет сохранения карты, YooKassa success/cancel/
  duplicate, Tribute matching/unmatched/duplicate, выдача поверх действующей
  подписки, конкурентный claim, revoked/expired gift и возврат.

## Порядок реализации

1. Накатить миграцию `0257_subscriptiongift_code_ciphertext_and_more`.
2. Создать `SUBSCRIPTION_GIFT_ENCRYPTION_KEY` и добавить hourly запуск
   `expire_subscription_gifts` в production scheduler.
3. Выполнить sandbox/webhook smoke-тесты Tribute и YooKassa.
4. После этого включить покупку для пользователей и проверить возвраты.
