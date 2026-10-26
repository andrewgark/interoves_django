# Инвентаризация main UI (`games/site/ui/`)

Код main UI живёт в пакете **`games/site/ui/`**. URL handlers — [`games/site/views/pages/ui.py`](../games/site/views/pages/ui.py). Legacy logger name: `games.views.new_ui`.

## Модули по области

| Пакет / модуль | Ответственность |
|----------------|-----------------|
| [`hub/`](../games/site/ui/hub/) | Hub, карточки игр, folder/main game entry |
| [`game/`](../games/site/ui/game/) | Страницы игры, progress, access |
| [`play/`](../games/site/ui/play/) | Task group, answers, live, replay, reactions, context |
| [`results/`](../games/site/ui/results/) | Results pages, compute |
| [`profile/`](../games/site/ui/profile/) | Profile, anon migration, feedback, form |
| [`team/`](../games/site/ui/team/) | Team UI |
| [`pay/`](../games/site/ui/pay/) | Tickets, payments, Telegram linking |

Общие re-export и logger — [`games/site/ui/__init__.py`](../games/site/ui/__init__.py).

## Следующий безопасный срез (roadmap §3)

1. Следующий крупный срез — [`hub/cards.py`](../games/site/ui/hub/cards.py). **Oct 26:** task-group → [`games/tasks/task_group_context.py`](../games/tasks/task_group_context.py); hub — [`links.py`](../games/site/ui/hub/links.py), [`actor.py`](../games/site/ui/hub/actor.py).
2. Зафиксировать URL names и тесты, которые вызывают его handlers.
3. Перенести только helpers без смены поведения; не менять URL names.
4. Не смешивать с payments, merge, migrations или mass-format.

## Риски

- helpers используют общий доменный контекст и большое количество моделей;
- часть функций одновременно готовит данные для HTML и JSON;
- account merge и payment flows являются production-sensitive;
- перенос может менять порядок импортов и побочные эффекты startup.

Критерий успеха — сохранение поведения и более ясная граница ответственности.
