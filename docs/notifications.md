# Уведомления игрокам

Код: `games/notifications/`. Единственная точка входа для доменного кода —
`games.notifications.service.notify(user, kind, actor=..., **ids)`. Telegram
напрямую (`send_user_telegram_message`) для личных уведомлений не используем.

## Модель

- `Notification` — одна строка во входящих. Хранит `kind`, `payload` с id
  объектов и `group_key`. Текст собирается при показе (`catalog.py`), поэтому
  отменённая заявка или удалённое сообщение просто перестают показываться.
  Непрочитанные строки с одинаковым `group_key` склеиваются: растёт
  `actor_count`, `payload` обновляется.
- `in_inbox=False` — группа выключена на сайте, но включена в Telegram: строка
  нужна только для доставки.
- `NotificationSetting` — переопределения каналов (сайт / Telegram) по группам.
  Нет строки — дефолт группы из `kinds.GROUPS`. Обязательные каналы
  (`site_mandatory`, `telegram_mandatory`) выключить нельзя.
- `NotificationPrefs` — тихие часы (по умолчанию 23:00–09:00 в
  `Profile.timezone`), `seen_at` для бейджа колокольчика, `telegram_blocked_at`.

## Как добавить тип

1. В `catalog.py` зарегистрировать `NotificationKind`: группа, `group_key`,
   `render` (сайт), `render_telegram`, `is_valid` (перечитать базу).
2. Вызвать `notify(...)` из доменного кода. Внутри транзакции безопасно:
   побочные эффекты идут после commit, исключения не пробрасываются.
3. Тест в `games/tests/test_notifications.py`.

Параметры типа: `telegram_delay` (по умолчанию 2 минуты — если игрок на сайте и
прочитал, в Telegram не шлём), `urgent` (игнорирует тихие часы),
`telegram_on_merge` (новое событие в склеенной строке шлётся снова),
`telegram_cooldown` (не чаще раза в N для одного `group_key`), `mark_read_on`
(url names, открытие которых гасит уведомление).

## Доставка в Telegram

1. `notify` помечает строку `tg_state=pending`, `tg_due_at` = сейчас + задержка,
   сдвинутое за тихие часы.
2. После commit, если до `tg_due_at` не больше 15 минут, сообщение
   `telegram.user_notice` с `{'kind': 'notification', 'notification_id'}` уходит
   в очередь integrations с `DelaySeconds`.
3. Worker (`telegram.deliver`) атомарно забирает строку (`pending → sending`) и
   перепроверяет: прочитано ли, включена ли группа, `is_valid`, cooldown, тихие
   часы. Ответ 403 записывается в `NotificationPrefs.telegram_blocked_at`.
4. Минутный тик анонсов (`run_announcement_live`) вызывает `telegram.sweep`: он
   досылает строки, отложенные за тихие часы, и строки, чьё сообщение потерялось
   (поставлено больше 10 минут назад и всё ещё pending). Раз в час удаляет
   прочитанное старше 90 дней и всё старше 180.

Payload `{'kind': 'friend_request', ...}` старого формата worker ещё понимает —
для сообщений, поставленных до выкладки. Его можно удалить после 2026-11-10.
