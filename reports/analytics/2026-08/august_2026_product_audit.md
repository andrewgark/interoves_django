# Inter Oves: продуктовый аудит за август 2026

Период: **2026-08-01 00:00:00 — 2026-08-31 23:59:59 Europe/Moscow**. В запросах использован эквивалентный полуинтервал `[2026-08-01 00:00:00+03:00; 2026-09-01 00:00:00+03:00)`. Снимок production-данных сделан **2026-09-02 06:44 MSK**; полностью наблюдаемый последний календарный день для retention — **2026-09-01**.

Production не изменялся. Все запросы выполнялись в `START TRANSACTION READ ONLY`, результат записывался только в локальные Markdown/CSV/JSON.

## Краткий итог

Главный вывод: Inter Oves уже имеет заметное игровое ядро, но основная масса новых игроков остаётся одноразовой, а связь ядра с монетизацией почти отсутствует.

| Метрика | Значение | Статус/оговорка |
|---|---:|---|
| August MAU сайта | N/A | Нет server-side visit identity; данные Метрики не хранятся в БД |
| August players | **799** | Backend `game_start`, только с 15 августа; нижняя оценка |
| Attempt-active actors | **1 142** | Полный август, но это другая сущность: actor с `Attempt(skip=0)` |
| New players | **533** | Первое наблюдаемое игровое взаимодействие в августе; когорты только 15–31 августа |
| Registered player identities | **159** | Текущий canonical identity среди 799 игроков |
| Anonymous player identities | **640** | Не включает анонимных посетителей без игры |
| New registrations | **53** | `auth_user.date_joined`, полный август |
| Game starts | **7 268** | Неполный август: backend starts доступны с 15 августа 16:49 MSK |
| Game completes | **5 479** | С 10 августа; Salad completion — только с 26 августа |
| Completion rate | **75,69%** | 5 501 из 7 268 started instances завершены к 2 сентября |
| Starts/player | **9,10** average / **3** median | Наблюдаемое окно 15–31 августа |
| Completes/player | **6,86** average / **1** median | Наблюдаемое окно с разной coverage по форматам |
| D1 retention (`game_start`) | **18,01%** | 96 / 533 eligible |
| D7 retention (`game_start`) | **15,82%** | 50 / 316 eligible; поздние когорты исключены |
| D30 retention | N/A | Нет ни одной полностью наблюдаемой backend-start когорты |
| 7+ active days | **162** | 20,3% игроков, несмотря на максимум 17 наблюдаемых дней |
| 10+ completions | **190** | 23,8% игроков |
| 20+ completions | **98** | 12,3% игроков |
| Strict linked signup after first start | **7 / 533 = 1,31%** | Недооценка: anon→user склеивается только при переносе прогресса |
| Successful ticket orders | **9** | 7 YooKassa + 2 Tribute Digital |
| Directly linked paying users | **2** | Ещё 7 accepted orders не имеют `created_by_id` |
| Player → payer | **≥0,25%** | Нижняя граница: 2 напрямую связанных payer / 799 players |
| Revenue | **11 000 RUB + 30 EUR** | Валюты не складываются; это ticket revenue, не подписка на puzzles |

Это не полный August MAU: официальная таблица starts появилась в середине месяца. Числа нельзя удваивать или экстраполировать на 31 день.

## 1. Источники данных, identity и определения

### Использованные таблицы

| Таблица | Назначение |
|---|---|
| `games_playerstartedgame` | Уникальный backend `game_start` на actor × game instance |
| `games_playercompletedgame` | Уникальный backend `game_complete` на actor × game instance |
| `games_playeranalyticsstate` | `signup_at`, `activated_at`, backfill/ack state |
| `games_attempt` | Сохранённые попытки; использовались только агрегаты, без текста |
| `games_hintattempt` | Самое раннее реальное игровое взаимодействие для проверки new/returning |
| `games_anonaccountclaim` | Безопасная склейка подтверждённого anon identity с user |
| `auth_user` | Регистрации (`date_joined`) без выборки email/username |
| `games_game`, `games_gametaskgroup` | Game type и placement |
| `games_dailygamedifficulty` | Текущий сохранённый difficulty snapshot |
| `games_ticketrequest` | Канонические ticket orders и их текущий status |
| `games_donation` | NOWPayments donations |
| `games_tributepaymentintent`, `games_tributepurchase` | Tribute intents/webhooks; purchase не суммируется повторно с ticket order |
| `django_migrations` | Подтверждённое production-время появления схемы analytics |

Не использовались IP, email, Telegram username, тексты попыток или иные PII.

### User / player / game / attempt

- **Canonical actor**: `u:<user_id>`, `a:<anon_key>` или `t:<team_id>`. Если существует `AnonAccountClaim`, оставшийся anon actor маппится в claim user. В production-коде при явном переносе прогресса starts/completes/attempts физически переносятся на user с сохранением исходных timestamps.
- **Registered actor в момент события**: canonical actor имеет `user_id`, и `auth_user.date_joined <= event_at`. Анонимная игра, позже перенесённая в аккаунт, остаётся анонимной на момент события.
- **Player**: actor с хотя бы одним non-backfilled `PlayerStartedGame` в периоде.
- **Game instance / placement**: `game_instance_id = game.id + ':' + task_group.id`. Ограничения БД дают максимум одну строку start/complete на actor × instance.
- **`game_start`**: `COUNT(*) FROM games_playerstartedgame WHERE is_backfilled=0 AND started_at in period`. Открытие страницы не является start. Для Ladder — первый реальный word/hint request; Alphabetty — первая попытка/hint; Salad после полного исправления — первый непустой path или hint; Replacement — первая строка; прочие игры — первая сохранённая попытка/реальная подсказка.
- **`game_complete`**: non-backfilled `PlayerCompletedGame` в периоде. Ladder — решены все слова; Alphabetty — `won=true`; Salad — найдены все ответы; Replacement — решены все строки. Для других форматов terminal event не реализован, поэтому их `0 completes` означает «не инструментировано», а не «никто не решил».
- **Attempt**: строка `games_attempt` с `skip=0`. Это не игровая сессия: одна Ladder/Salad может создать много `Partial` rows. За август: 171 456 attempts = 151 869 `Partial`, 12 187 `Ok`, 7 400 `Wrong`.
- **Session**: аналитического session id нет. Django session не привязана к start/complete/attempt, поэтому session-based metrics не рассчитаны.

### Дедупликация и склейка

`PlayerStartedGame` и `PlayerCompletedGame` дедуплицируются уникальными constraints по `(user|anon|team, game_instance_id)`. В отчёте дополнительно не используются `metrika_acked_at`: ack показывает доставку в Яндекс Метрику, а не факт игры.

Склейка anon→user достоверна только когда пользователь подтвердил перенос браузерного прогресса. Если он зарегистрировался на другом устройстве, не принял перенос или очистил identity, связать две стороны нельзя. Поэтому strict signup funnel и directly linked payer conversion — нижние оценки.

### Точные логические определения остальных метрик

| Метрика | Определение |
|---|---|
| Unique players | `COUNT(DISTINCT canonical_actor)` среди non-backfilled starts |
| Registered / anonymous players | Distinct actors, сегментированных по `date_joined <= started_at`; в общей сводке также показан текущий canonical type |
| New registration | `auth_user.date_joined` в московском периоде |
| New player | Actor, у которого earliest existing `Attempt(skip=0)` / real `HintAttempt` и earliest backend start попадают в август; это исключает известных до августа игроков |
| Starts/completes per player | Число period rows на actor; average/median считаются по August players, отсутствующие completes = 0 |
| Completion distribution | Число period non-backfilled completes на actor из объединения observed starters/completers |
| Daily unique player | Distinct canonical actor с start в московский календарный день |
| Returned from previous day | Пересечение actor sets starts текущего и предыдущего полного наблюдаемого дня |
| Dn retention | Actor стартовал хотя бы одну игру ровно на `cohort_date + n`; denominator содержит только когорты, для которых target day полностью завершён |
| Rolling Dn | Есть start в любой день `>= cohort_date+n` до snapshot; denominator также только eligible cohorts |
| Active day | Distinct московская дата с хотя бы одним backend start |
| Streak | Максимальная длина последовательности consecutive active dates |
| Completion rate в KPI | Started instances августа, для которых существует non-backfilled complete к snapshot / все starts августа |
| Placement solve time | `completed_at - started_at` для matched actor × instance; это wall-clock, не active time |
| Attempts to complete | `COUNT(Attempt skip=0)` того же actor/game/task_group между start и complete включительно |
| Current game | Дата start равна сохранённой дате публикации daily placement |
| Archive | Daily placement опубликован раньше даты start |
| Signup position | Число completed rows canonical user с `completed_at < auth_user.date_joined`; работает только при корректной identity link |
| Successful ticket | August-created `TicketRequest` с текущим `status='Accepted'` |
| Ticket revenue | `SUM(money)` accepted August-created orders, отдельно по currency/provider |
| Donation revenue | Confirmed donation по `confirmed_at`, фактические `pay_amount/pay_currency` |
| Paying user | Только явный `created_by_id`, `Donation.user_id` или matched Tribute user; team-only order не приписывается человеку |
| Top X% | `ceil(X% × 799)` actors, сортировка по August completes descending; никаких PII в результате |

### Инструментация в августе

| Дата/время MSK | Изменение | Следствие для отчёта |
|---|---|---|
| 2026-08-10 22:20 | Production migration `0168`: completed games / analytics state | До этого backend completion events отсутствуют; historical backfill исключён |
| 2026-08-15 16:49 | Production migration `0172`: durable starts, signup fields, backfill flags | Starts до этого времени N/A; 15 августа — неполный день |
| 2026-08-23 01:49 | Production migration daily Salad section | Salad начал набирать starts 23 августа |
| 2026-08-26 | Code commit добавил Salad в supported completion и новый onboarding `/start/` | Первые live Salad completes появляются 26 августа |
| 2026-08-27 07:26 | Production difficulty scheduler schema | Difficulty — сохранённый snapshot, не ретроспективная оценка на дату игры |
| 2026-08-28 | Code fix: Salad wrong/non-persisted paths считаются start; исправлена onboarding delivery | Сопоставимая полная Salad start coverage — с 28 августа |

Для code-only изменений точный production deploy timestamp не хранится в аналитической БД; указаны commit date и первый подтверждённый день данных. Несовместимые Salad 23–25 августа не используются для вывода о completion bottleneck.

## 2. Общая аудитория

### Что удалось и не удалось посчитать

- **Уникальные пользователи сайта / August MAU / DAU**: N/A. В БД нет visit/pageview/session actor. Яндекс Метрика подключена, но её raw data/UTM/device не реплицируются в production DB.
- **Аккаунтов существовало к концу августа**: 2 277. Это inventory accounts, а не активная аудитория.
- **Новых регистраций**: 53.
- **Официально наблюдаемых игроков**: 799 с 15 августа; 159 текущих registered identities и 640 anonymous identities.
- **Полномесячный дополнительный lower bound**: 1 142 actors оставили хотя бы один persisted Attempt. Он не заменяет player MAU, потому что не включает hint-only/non-persisted starts и, наоборот, считает task attempts, а не игры.
- **Игроков с хотя бы одним observed complete**: 541 / 799 = 67,7%.
- **Starts**: 7 268; **completes**: 5 479; **started instances completed by snapshot**: 5 501 / 7 268 = 75,69%.

### Распределение completions на игрока

| Completes | All | Registered identity | Anonymous identity |
|---|---:|---:|---:|
| 0 | 258 | 27 | 231 |
| 1 | 147 | 6 | 141 |
| 2 | 59 | 7 | 52 |
| 3 | 37 | 7 | 30 |
| 4–5 | 52 | 10 | 42 |
| 6–10 | 69 | 15 | 54 |
| 11–20 | 83 | 28 | 55 |
| 21+ | 94 | 59 | 35 |

Факт: у 32,3% observed players нет complete, а у 11,8% — 21+ completes. Registered identities гораздо сильнее смещены в heavy use: 59 из 159 имеют 21+ completes против 35 из 640 anonymous. Это correlation/self-selection, не эффект регистрации.

## 3. Дневная динамика

`N/A` означает отсутствие совместимого event source, `0` — реальный ноль в существующей таблице. 15 августа starts неполные; 10 августа completes неполные. Полный CSV дополнительно содержит registered/anonymous/team и coverage flags.

| Дата | DAU | Нов. рег. | Игроки | Starts | Completes | Completers | Signup | Activated | Нов. игроки | Вернулись D-1 | Attempts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-08-01 | — | 1 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 5018 |
| 2026-08-02 | — | 1 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 5733 |
| 2026-08-03 | — | 4 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 6066 |
| 2026-08-04 | — | 2 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 5864 |
| 2026-08-05 | — | 2 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 5760 |
| 2026-08-06 | — | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 5572 |
| 2026-08-07 | — | 3 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 5196 |
| 2026-08-08 | — | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 4298 |
| 2026-08-09 | — | 1 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 4119 |
| 2026-08-10 | — | 4 | N/A | N/A | 0 | 0 | N/A | N/A | N/A | N/A | 4908 |
| 2026-08-11 | — | 4 | N/A | N/A | 0 | 0 | N/A | N/A | N/A | N/A | 7693 |
| 2026-08-12 | — | 1 | N/A | N/A | 0 | 0 | N/A | N/A | N/A | N/A | 4872 |
| 2026-08-13 | — | 1 | N/A | N/A | 2 | 2 | N/A | N/A | N/A | N/A | 5006 |
| 2026-08-14 | — | 2 | N/A | N/A | 0 | 0 | N/A | N/A | N/A | N/A | 5394 |
| 2026-08-15 | — | 0 | 43 | 109 | 69 | 35 | 0 | 2 | 15 | N/A | 3719 |
| 2026-08-16 | — | 4 | 157 | 439 | 226 | 110 | 4 | 5 | 40 | N/A | 5781 |
| 2026-08-17 | — | 1 | 148 | 313 | 221 | 115 | 0 | 1 | 26 | 83 | 5145 |
| 2026-08-18 | — | 2 | 178 | 322 | 224 | 130 | 2 | 8 | 37 | 97 | 4666 |
| 2026-08-19 | — | 1 | 189 | 391 | 290 | 146 | 1 | 12 | 62 | 98 | 5202 |
| 2026-08-20 | — | 2 | 158 | 320 | 256 | 131 | 2 | 11 | 24 | 102 | 4393 |
| 2026-08-21 | — | 0 | 153 | 326 | 282 | 135 | 0 | 7 | 14 | 98 | 5280 |
| 2026-08-22 | — | 0 | 124 | 205 | 191 | 108 | 0 | 2 | 6 | 79 | 3643 |
| 2026-08-23 | — | 2 | 179 | 407 | 228 | 112 | 2 | 5 | 26 | 82 | 5698 |
| 2026-08-24 | — | 1 | 201 | 475 | 271 | 144 | 1 | 9 | 32 | 120 | 5364 |
| 2026-08-25 | — | 3 | 208 | 624 | 347 | 148 | 1 | 6 | 34 | 121 | 7723 |
| 2026-08-26 | — | 1 | 201 | 533 | 463 | 174 | 1 | 8 | 20 | 131 | 6657 |
| 2026-08-27 | — | 2 | 203 | 552 | 502 | 188 | 2 | 8 | 24 | 142 | 6647 |
| 2026-08-28 | — | 2 | 229 | 630 | 566 | 201 | 2 | 15 | 44 | 137 | 7710 |
| 2026-08-29 | — | 2 | 224 | 566 | 449 | 174 | 2 | 10 | 53 | 125 | 6750 |
| 2026-08-30 | — | 2 | 209 | 509 | 423 | 170 | 2 | 10 | 45 | 126 | 6133 |
| 2026-08-31 | — | 2 | 212 | 547 | 469 | 185 | 2 | 10 | 31 | 124 | 5446 |

### Min / max / average / median

Summary использует только полные дни конкретной instrumentation.

| Метрика | Min | Max | Average | Median | Полных дней |
|---|---:|---:|---:|---:|---:|
| New registrations | 0 | 4 | 1,71 | 2 | 31 |
| Unique players | 124 | 229 | 185,81 | 195 | 16 |
| Game starts | 205 | 630 | 447,44 | 457 | 16 |
| Game completes | 0 | 566 | 260,90 | 256 | 21 |
| Completed users | 0 | 201 | 114,67 | 131 | 21 |
| Signup event | 0 | 4 | 1,50 | 2 | 16 |
| Activated player | 1 | 15 | 7,94 | 8 | 16 |
| First-time players | 6 | 62 | 32,38 | 31,5 | 16 |
| Returned from previous day | 79 | 142 | 111 | 120 | 15 |
| Persisted attempts | 3 643 | 7 723 | 5 530,84 | 5 394 | 31 |

### Подтверждённые пики и провалы

- **22 августа — минимум полного start-окна**: 124 players, 205 starts, 3 643 attempts. Снижение видно одновременно в Ladder и Alphabetty, то есть это не поломка одного формата. Источник/кампания не сохраняются, поэтому причину определить нельзя.
- **23 августа — запуск Salad**: 116 Salad starts подняли total starts с 205 до 407. Нулевые Salad completes 23–25 августа — instrumentation gap, а не реальный 0% completion.
- **25 августа — максимум attempts (7 723)**: одновременно 278 Ladder, 198 Salad и 109 Alphabetty starts; рост широкий, не один аномальный placement.
- **28 августа — максимум players/starts/completes**: 229 / 630 / 566. Breakdown: 278 Ladder starts, 248 Salad, 92 Alphabetty; completes 255 / 218 / 87. День совпадает с исправлением полной Salad start delivery, поэтому часть скачка — coverage, но высокий уровень виден и в Ladder.
- **19 августа — 62 first-time players**, максимум окна. Без source data нельзя подтвердить рекламу или конкретную публикацию.

## 4. Funnel первого знакомства

Initial cohort — 533 actors, у которых earliest existing playing interaction и first backend start находятся в августе. Пользователи, известные до августа по Attempt/real HintAttempt, исключены.

Landing/session/onboarding steps отсутствуют в server-side хранилище, поэтому доступная часть funnel начинается с `game_start`.

| Шаг | Users | Conversion from previous | Conversion from initial |
|---|---:|---:|---:|
| First `game_start` | 533 | 100% | 100% |
| ≥1 `game_complete` после start | 314 | 58,91% | 58,91% |
| ≥2 completes | 198 | 63,06% | 37,15% |
| ≥3 completes | 160 | 80,81% | 30,02% |
| Live `activated_player` marker | 110 | 68,75% | 20,64% |
| Return next day | 96 | — | 18,01% |
| Return on any day 1–7 | 131 / 316 eligible | — | 41,46% eligible |

Дополнительные markers:

- 47 / 533 (8,82%) уже были зарегистрированы к first start.
- Только 7 / 533 (1,31%) имеют достоверно linked signup после first start.
- `activated_player` по коду означает достижение **трёх** unique completed games. Он логически идёт после third complete. 110 меньше 160 из-за backfilled activation markers и mid-month instrumentation; это не behavioural drop третья игра→activation.
- «Первая валидная попытка» как отдельная сущность/goal не существует. `game_start` уже означает реальное interaction; выделять искусственный step было бы подменой определения.

Landing, onboarding view/select, recommended flag и source cohort нельзя присоединить к backend actor. Поэтому acquisition→first game и onboarding bottleneck остаются неизвестными.

## 5. Retention

Основное определение: хотя бы один `game_start` ровно на Dn. Поздние когорты не записываются как churn.

| Retention | Users | Eligible | Rate |
|---|---:|---:|---:|
| D1 start | 96 | 533 | 18,01% |
| D2 start | 96 | 502 | 19,12% |
| D3 start | 75 | 457 | 16,41% |
| D7 start | 50 | 316 | 15,82% |
| D14 start | 21 | 118 | 17,80% |
| D21 start | N/A | 0 | N/A |
| D30 start | N/A | 0 | N/A |

| Complete-based retention | Users | Eligible | Rate |
|---|---:|---:|---:|
| D1 complete | 77 | 533 | 14,45% |
| D2 complete | 81 | 502 | 16,14% |
| D3 complete | 62 | 457 | 13,57% |
| D7 complete | 42 | 316 | 13,29% |
| D14 complete | 19 | 118 | 16,10% |

Rolling retention по start:

- после 1 дня: 175 / 533 = **32,83%**;
- после 7 дней: 87 / 316 = **27,53%**;
- после 14 дней: 26 / 118 = **22,03%**.

Registered-vs-anonymous при first start:

| Segment at first start | D1 | D7 |
|---|---:|---:|
| Registered | 17 / 47 = 36,17% | 9 / 41 = 21,95% |
| Anonymous | 79 / 486 = 16,26% | 41 / 275 = 14,91% |

Это сильный self-selection bias: люди с аккаунтом уже отличаются intent/историей. Делать вывод «регистрация удваивает retention» нельзя.

Полная cohort table с `N/A` для незрелых D7/D14/D21/D30 находится в `retention_cohorts.csv`.

## 6. Частота, привычка и ядро

Окно starts — только 17 календарных дней, поэтому 20+ active days физически недостижимы.

| Active days | Players | Share of 799 |
|---|---:|---:|
| 1 | 431 | 53,9% |
| 2 | 69 | 8,6% |
| 3–4 | 89 | 11,1% |
| 5–7 | 71 | 8,9% |
| 8–14 | 91 | 11,4% |
| 15–20 | 48 | 6,0% |
| 21+ | 0 | Не наблюдаемо |

- Average active days: **3,775**; median: **1**.
- Average interval между активными днями для игроков с повтором: **1,553 дня**.
- Максимальный streak: **17 дней**; 48 actors активны 15–17 дней.
- Maximum-streak distribution: 1 день — 505 users; 2 — 75; 3 — 56; 4 — 32; 5 — 18; 6 — 17; 7 — 20; 8 — 10; 9 — 15; 10 — 8; 11 — 5; 12 — 7; 13 — 3; 14 — 4; 15 — 5; 16 — 16; 17 — 3.

Размер ядра:

| Определение | Users | Share |
|---|---:|---:|
| A) 7+ active days | 162 | 20,3% |
| B) 10+ completes | 190 | 23,8% |
| C) 20+ completes | 98 | 12,3% |
| D) 3+ calendar active weeks | 166 | 20,8% |
| Минимум 3 active days | 299 | 37,4% |
| Минимум 14 active days | 60 | 7,5% |

Факт: распределение бимодальное — median один active day, но одновременно есть 162 users с 7+ days и 98 users с 20+ completes. Продукт не только one-off puzzle; у него уже есть реальное habit core.

## 7. Игры и типы игр

Таблица ниже — всё наблюдаемое окно и потому несёт разные instrumentation caveats. `Continue` означает любой более поздний start после первого observed start этого типа; cohort включает и существующих пользователей.

| Type | Players | Starts | Completes | Event ratio | Starts/player | Completes/player | Continue | D1 | D7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Ladder | 517 | 3 708 | 3 169 | 85,46% | 7,17 | 6,13 | 73,89% | 38,10% | 34,98% |
| Alphabetty | 198 | 1 163 | 1 110 | 95,44% | 5,87 | 5,61 | 84,85% | 52,02% | 52,71% |
| Salad | 444 | 1 831 | 1 148 | 62,70% | 4,12 | 2,59 | 70,50% | 39,64% | 56,34% |
| Replacement | 20 | 68 | 52 | 76,47% | 3,40 | 2,60 | 85,00% | 60,00% | 57,14% |
| Week task | 60 | 95 | N/A | N/A | 1,58 | N/A | 85,00% | 55,00% | 45,83% |

Salad 62,7% нельзя сравнивать напрямую: starts есть с 23 августа, completes — с 26-го, полная фиксация wrong starts — с 28-го. Для published placements **28–31 августа** более сопоставимый cohort completion:

- Alphabetty: 196 / 218 = **89,91%**;
- Ladder: 539 / 610 = **88,36%**;
- Salad: 632 / 785 = **80,51%**.

Первая игра 533 new players:

- Ladder — 247 (46,3%);
- Salad — 198 (37,1%);
- Alphabetty — 44 (8,3%);
- прочие — 44 (8,3%).

Самые частые полные первые три starts: `Ladder → Ladder → Ladder` — 92, `Salad → Salad → Salad` — 30, `Alphabetty → Alphabetty → Alphabetty` — 12. Ещё 113 Salad-first и 106 Ladder-first players не дошли до второго observed start; это важнее редких cross-type sequences.

По all-user first-of-type cohort Alphabetty лучше всего связан с продолжением, но это не чистый new-user experiment. В подмножестве new players, начавших с августовского placement, subsequent-start доли: Alphabetty 29/44 = 65,9%, Ladder 131/230 = 57,0%, Salad 85/198 = 42,9%. Для Salad сравнение дополнительно смещено instrumentation 23–27 августа.

## 8. Отдельные placements

Полный `placement_metrics.csv` содержит каждый августовский placement: дату, type/id, starters/completers, period и starter-cohort completion, solve time, attempts, difficulty snapshot, new players, continuation, D1/D7 eligibility.

Важные наблюдения после учёта coverage:

| Placement | Cohort completion | Median solve | Median attempts | Difficulty | New → another game | D1 |
|---|---:|---:|---:|---:|---:|---:|
| Salad #6, 28 Aug | 91,04% | 109 s | 6 | 2★ | 13/27 = 48,15% | 1/27 = 3,70% |
| Salad #7, 29 Aug | **67,89%** | **661 s** | **9** | **5★** | **6/40 = 15,00%** | **2/40 = 5,00%** |
| Salad #8, 30 Aug | 77,84% | 466 s | 9 | 4★ | 14/37 = 37,84% | 2/37 = 5,41% |
| Salad #9, 31 Aug | 86,96% | 324 s | 6 | 4★ | 8/22 = 36,36% | 4/22 = 18,18% |
| Ladder #42, 18 Aug | 76,74% | 662 s | 16 | 4★ | 30/56 = 53,57% | 11/56 = 19,64% |
| Ladder #51, 27 Aug | 94,25% | 363 s | 14 | 2★ | 4/4 = 100% | 0/4 |
| Alphabetty #30, 30 Aug | 84,44% | 248 s | 18 | 4★ | 0/0 | N/A |
| Alphabetty #31, 31 Aug | 94,00% | 99 s | 12 | 2★ | 0/4 | 0/4 |

Самый ясный negative signal — **Salad #7**: одновременно самый высокий difficulty, самый большой median solve time среди сопоставимых Salad, низкий completion и только 15% new-player continuation. Это согласованный сигнал нескольких метрик, а не вывод по одной конверсии.

Надёжного «лучшего converter placement» пока нет: у многих placements 1–16 new players, а D7 часто ещё не наблюдаем. Early Salad #1–3 нельзя ранжировать по completion из-за отсутствия server completion support до 26 августа.

`mean_solve_seconds` часто намного выше median: wall-clock включает уход пользователя и возврат через часы/дни. Для difficulty/UX основным показателем следует считать median; active play time корректно не измеряется.

## 9. Архив против текущей игры

Только Ladder/Alphabetty/Salad, где дата публикации однозначна.

| Segment | Starts | Share | Players | Cohort completion | Games/user | D1 after first segment start | D7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current | 4 044 | 60,34% | 660 | 80,66% | 6,13 | 32,73% | 37,97% |
| Archive | 2 658 | 39,66% | 448 | 82,28% | 5,93 | 38,84% | 34,56% |

Actors могут входить в обе строки; retention здесь по first start данного segment и включает существующих игроков. Это не causal comparison «архив улучшает retention».

- 261 / 478 = **54,60%** current-game completers затем открыли archive game в тот же день.
- 301 / 541 = **55,64%** users после своей первой августовской completion начали ещё одну игру в тот же день.
- Настоящую «ту же session» долю посчитать нельзя — session id отсутствует. Same-day является точной доступной заменой, но не эквивалентом session.

Архив — важная часть продукта, а не хвост: почти 40% daily starts и чуть более высокий observed completion.

## 10. Регистрация

- 53 registrations за полный август.
- Median observed completes before signup: **0**; mean **2,45**.
- 42 из 53 имели 0 linked completes до регистрации; 4 — одну; остальные единичные heavy histories (3, 4, 6, 14, 16, 24, 59) сильно поднимают mean.

Current registered share по количеству августовских completes:

| Completes | Registered | All players bucket | Share registered |
|---|---:|---:|---:|
| 1 | 6 | 147 | 4,08% |
| 2 | 7 | 59 | 11,86% |
| 3 | 7 | 37 | 18,92% |
| 4–5 | 10 | 52 | 19,23% |
| 6+ | 101 | 246 | 41,06% |

Факт: registration сильно ассоциирована с высокой частотой. Интерпретация: вероятен self-selection — мотивированные players и регистрируются, и играют больше. Гипотеза о причинном эффекте регистрации этими данными не проверяется.

Strict post-first-start signup = 7/533 — нижняя оценка из-за ручной anon migration. Для точной signup funnel требуется durable anonymous→account identity link в момент регистрации.

## 11. Onboarding, acquisition, geography, language, device

### Onboarding

`onboarding_view`, `onboarding_game_select`, selected game, `recommended`, `onboarding_first_game_complete` и `onboarding_second_game_start` отправляются client-side в Яндекс Метрику. В production DB нет event rows и parameters. Контекст хранится в browser `localStorage` 24 часа.

Следовательно, нельзя достоверно посчитать usage, recommended-vs-non-recommended, conversion после выбора или назвать onboarding bottleneck. 26 августа onboarding был существенно изменён; 28 августа исправлена Salad delivery. Смешивать эти окна было бы некорректно.

### Источники трафика

В backend analytics нет `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, referrer/domain. Рекламный и органический трафик не сравниваются. `acquisition_source_metrics.csv` не создан, потому что данных нет.

### География / языки / устройства

Country, UI language и device class не сохраняются в анализируемых таблицах. IP post-factum не использовался. Сегменты не рассчитаны.

Это самый серьёзный data gap перед growth work: невозможно измерить acquisition cost/quality и landing→play conversion в той же identity-модели, что retention.

## 12. Платежи

### Orders и revenue

| Событие | Count | Revenue/status |
|---|---:|---|
| TicketRequest created | 13 | 9 Accepted, 4 Rejected |
| YooKassa accepted | 7 | **11 000 RUB** |
| Tribute Digital accepted/issued | 2 | **30 EUR**; 2 issued purchase webhooks |
| Tribute intent expired | 1 | Expected 24 EUR, не revenue |
| Donations created | 2 | Обе Rejected |
| Confirmed donations | 0 | 0 revenue |

RUB, EUR и crypto не складываются. USD-equivalent не рассчитан: исторического exchange rate в данных нет.

`TicketRequest` не хранит accepted timestamp для всех providers. Поэтому August successful ticket = order создан в августе и имеет текущий final status Accepted; это точное доступное, но не идеальное revenue recognition определение.

### Payers и unit economics

- Directly linked payers: **2**, оба Tribute/EUR; first-time — 2, repeat — 0.
- Ещё **7 accepted YooKassa orders** имеют `created_by_id=NULL`; безопасно приписать их конкретному team member нельзя.
- Direct linked player→payer lower bound: **0,25%**.
- Linked EUR revenue/payer: **15 EUR**.
- RUB revenue/payer: N/A из-за отсутствия user link.
- Revenue per observed active player, без смешения валют: **13,77 RUB + 0,04 EUR**. Denominator неполный и ticket product не равен puzzle subscription, поэтому это не полноценный ARPMAU.

В production не найден отдельный платный puzzle product. Наблюдаемая выручка — tournament/event tickets, а не стабильная recurring monetization основного daily-puzzle habit.

## 13. Power users

| Group | Users | Completes | Share of all completes | Mean completes | Mean active days | Registered | Paid ever linked | Returned 1 Sep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Top 1% | 8 | 462 | 8,43% | 57,75 | 11,88 | 7 | 0 | 7 |
| Top 5% | 40 | 1 717 | 31,34% | 42,93 | 12,45 | 28 | 0 | 38 |
| Top 10% | 80 | 2 779 | 50,72% | 34,74 | 12,03 | 51 | 1 | 69 |

Preferred type по completes: top 10% — 58 Ladder, 22 Alphabetty; top 5% — 23 Ladder, 17 Alphabetty. Ни одного email/username/anon key в output нет.

Факт: половина completes создаётся 10% players. Факт: только один из top-10% имеет безопасно linked payment ever. Интерпретация: engagement core почти не монетизировано; ticket purchases и daily puzzle power users слабо соединены.

## 14. Где bottleneck

### Факты

- Acquisition и landing→start не наблюдаются.
- Среди started instances observed completion высокая: 75,69% overall, около 88–90% у трёх daily formats в сопоставимом окне 28–31 августа.
- 58,9% new-player cohort дошли до first complete; из них 63,1% дошли до второй completion.
- 55,6% completers запускают ещё одну игру в тот же день.
- Но median active days = 1; 53,9% players играют ровно один день; D1 = 18,0%, D7 = 15,8%.
- Strict linked signup после first start = 1,31%; directly linked payer conversion ≥0,25%.

### Интерпретация

Самый большой **наблюдаемый продуктовый bottleneck — переход от успешной первой сессии к возврату в другой день**, а не решение конкретной игры. Внутри первой сессии архив и вторая игра работают заметно лучше, чем next-day loop.

Самый большой **business bottleneck — monetization**: выручка существует, но относится к tickets; engagement core почти не имеет linked payments. Acquisition bottleneck может быть не меньше, но его невозможно ранжировать без landing/source data.

Signup — слабый/плохо измеренный промежуточный step. Делать обязательную регистрацию главным growth lever по текущим данным не стоит: она коррелирует с retention, но causal evidence нет.

## 15. One-off, habit и признаки PMF

### Факты

- One-off слой: 431/799 играли один день, median = 1.
- Periodic слой: 160 players играли 3–7 дней.
- Habit core: 162 players с 7+ active days; 48 активны 15–17 дней; 98 имеют 20+ completes.
- D1/D7 низкие для массовой аудитории, но power-user return на 1 сентября высокий: 69/80 у top 10%.
- Core concentration высокая: top 10% дают 50,7% completes.

### Интерпретация

Продукт выглядит как **one-off/короткая серия для большинства плюс ежедневная привычка для реального меньшинства**. Это сильнее, чем «случайный puzzle без ядра», но слабее устойчивого mass habit product.

### Признаки PMF, которые видны

- существует количественно значимое ядро даже в половинном start-окне;
- высокая completion у Ladder/Alphabetty и хорошее использование архива;
- пользователи добровольно играют длинные серии и много placements;
- разные daily formats формируют повторные same-type sequences.

### Признаки PMF, которых пока нет

- массовый D1/D7 loop;
- доказанная органическая acquisition/word-of-mouth динамика;
- измеримый landing→start funnel;
- recurring revenue от puzzle core;
- устойчивое payer conversion и repeat payer base.

## 16. Пять важнейших метрик

1. **New-player D1 и D7 retention по backend start** — главный outcome next-day/weekly habit.
2. **First complete → second start/complete в 24 часа и на следующий день** — мост между хорошей первой сессией и retention.
3. **7+ active-day users и 3+ active-week users** — размер habit core, менее шумный, чем среднее число игр.
4. **Landing unique actor → real game_start**, отдельно по source/campaign — сейчас N/A; без этого growth spend нельзя оценивать.
5. **Puzzle-core payer conversion и revenue per active player по currency/product**, отдельно от event tickets.

Signup стоит мониторить как identity/communication enabler, но не как конечную ценность.

## 17. Три продуктовых эксперимента

### 1. Сильный post-complete «ещё одна игра» loop

**Гипотеза:** персональный следующий placement после complete (сначала ещё один короткий puzzle, затем архив) увеличит second completion и D1. Основание: 55,6% уже сами стартуют вторую игру в тот же день, а 54,6% current completers идут в архив.

Primary metrics: first complete→second complete same day, D1 start. Guardrails: solve time, wrong attempts, exit after complete. Randomization — canonical anon/user actor.

### 2. Next-day promise + opt-in reminder/streak

**Гипотеза:** после второй/третьей completion показать конкретное обещание завтрашней игры, streak calendar и добровольное напоминание. Это целится в разрыв между strong same-day play и D1=18%.

Primary metrics: D1/D7, 3+ active days. Не считать регистрацию treatment; reminder opt-in может работать анонимно через browser notification/Telegram только после явного consent.

### 3. Supporter membership для доказанного ядра

**Гипотеза:** предложение monthly supporter membership после 7 active days или 10 completes даст выше payer conversion, чем общий ticket checkout. Не закрывать бесплатную daily game; ценность — удобство/архивные коллекции/cosmetics/поддержка проекта.

Primary metrics: payer conversion, revenue по валюте, 30-day renewal; guardrails — D7/D30 retention и completion. Сначала нужен direct user/payment identity и отдельный product code, чтобы не смешивать subscription с tickets.

## 18. Ограничения и качество данных

1. Starts покрывают только 15–31 августа; 15-е неполное. Это делает все player/frequency/core totals нижними оценками.
2. Completes появились 10 августа; Salad support — 26-го; полные Salad wrong starts — 28-го.
3. Backfilled rows исключены из event counts. Их creation timestamp не является историческим временем действия.
4. Site visits, DAU, landing, onboarding parameters, UTM/referrer, language/country/device отсутствуют в БД.
5. Session id отсутствует; same-day не равен same-session.
6. Anon→user link требует явного переноса. Signup funnel, registered retention и payer join могут недосчитывать.
7. Other game formats могут стартовать, но не имеют supported completion event.
8. Solve time — elapsed wall-clock, а не active time.
9. Difficulty — snapshot на 2 сентября, не неизменное историческое значение на дату публикации.
10. Ticket success определяется current final status у August-created order; универсального `paid_at` нет.
11. D21/D30 для backend-start когорт не наблюдаемы и оставлены N/A, не churn.

## 19. Безопасность запросов

Перед расчётом проверены production indexes. Использованы `started_at`, `completed_at`, actor+time, `game_kind+time`, `Attempt(skip,time)` и `Attempt(task,actor,time)`. `auth_user.date_joined` не индексирован, но таблица содержит только 2 279 rows, поэтому полный read-only scan мал. Raw Attempt text не извлекался: 171 тыс. строк агрегировались на сервере по day/status/actor, а per-placement attempt counts считались индексными joins.

Ни одной миграции, schema change, backfill, save/update/delete или исторического пересчёта состояния не выполнялось. Read-only transaction завершалась rollback.

## Файлы результата

- `daily_metrics.csv` — все 31 дня, coverage flags и registered/anonymous breakdown;
- `daily_game_type_metrics.csv` — evidence для дневных аномалий по форматам;
- `retention_cohorts.csv` — cohort table с N/A для незрелых горизонтов;
- `game_type_metrics.csv` — metrics всех существующих типов;
- `placement_metrics.csv` — полный placement-level анализ;
- `audit_summary.json` — machine-readable агрегаты;
- `scripts/analytics/august_2026_audit.py` — воспроизводимый read-only расчёт.

Acquisition/source CSV не создан: соответствующих production fields/events нет.
