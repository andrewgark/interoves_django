# Yandex Metrika analytics

| Goal | Meaning |
| --- | --- |
| `game_start` | Пользователь реально начал игровое задание |
| `game_complete` | Пользователь завершил уникальное игровое задание |
| `signup` | Аккаунт успешно создан |
| `activated_player` | Пользователь впервые завершил 3 уникальные игры |
| `ticket_checkout` | Пользователь начал процесс оплаты |
| `ticket_purchase` | Оплата подтверждена |

## Game values

- `ladder`
- `alphabet`
- `replacement`
- for every other game, its stable `Game.id`

## What counts as `game_start`

- `ladder`: первый реальный игровой запрос по лесенке, то есть отправка слова или использование raddle-подсказки.
- `alphabet`: первая отправка слова или первая буквенная подсказка.
- `replacement`: первая отправка строки на проверку.
- остальные игры: первая сохранённая попытка или первая реальная подсказка.

Простое открытие страницы не считается стартом.

Backend writes one `PlayerStartedGame` per analytics actor and game instance. This
is the source of truth for start counts. Until the `reachGoal` callback is
acknowledged, subsequent valid game responses may return the same `game_start`
payload again. The browser deduplicates it by key and keeps pending delivery in
`localStorage` for up to 14 days.

`metrika_acked_at` measures browser delivery acknowledgement. It can remain null
for ad blockers and browsers where `mc.yandex.ru` is unavailable, so Metrika is
not expected to equal the backend count exactly.

Operational commands:

```bash
../venv/interoves_django/bin/python manage.py report_player_started_games --days 14 --game ladder
../venv/interoves_django/bin/python manage.py report_yandex_goals --days 14
../venv/interoves_django/bin/python manage.py backfill_player_started_games --dry-run
../venv/interoves_django/bin/python manage.py backfill_player_started_games --since 2026-08-01T00:00:00+03:00
```

Backfilled rows preserve the first `Attempt.time`, are marked `is_backfilled`,
and never emit historical Yandex goals.

## What counts as `game_complete`

- `ladder`: серверное состояние raddle впервые стало полным, то есть решены все слова конкретной лесенки.
- `alphabet`: `won=true` для конкретной алфавитки.
- `replacement`: впервые решены все строки конкретного задания `replacements_lines`.

`game_complete` дедуплицируется по уникальному игровому инстансу `game.id + task_group`.
Исторические completion-записи помечаются `is_backfilled` и не отправляются задним
числом. Новое завершение повторяется до callback-ack Метрики.

## Activation

`activated_player` считается на backend по таблице уникально завершённых игр. Событие отправляется один раз, когда число уникальных завершённых игровых инстансов впервые переходит порог `3`.
Состояние активации и `signup` хранятся на backend и повторяются до callback-ack.

## Tickets

- `ticket_checkout`: после успешного создания `TicketRequest` и получения платёжного маршрута.
- `ticket_purchase`: когда `TicketRequest.status == Accepted`. Webhook ставит цель
  в долговечную серверную очередь; status polling и каждая следующая страница
  авторизованного покупателя повторяют её до подписанного callback-ack Метрики.

Обе платёжные цели используют подписанный callback-ack. Покупка привязывается к
пользователю, создавшему заказ; вместе с заказом сохраняется `_ym_uid` как ClientID
для будущей server-to-server отправки. Для старых заказов без такой привязки
используется участник купившей билет команды, чтобы подтверждённая цель не оставалась
в очереди.

## Known limitation

Для `replacement` завершением считается полное решение всех строк. Просмотр ответа по отдельной строке сейчас не помечает игру завершённой, потому что в существующей архитектуре это не фиксируется как отдельный terminal state на backend.
