# New web interface (`/new/`)

## URL map

| Path | Purpose |
|------|--------|
| `/new/` | Hub: list of content folders |
| `/new/games/` | Десяточки (slug `games`): grouped by `Game.theme` |
| `/new/` (hub) | Разделы: плитка «Десяточки» (project main) + по одной плитке на игру из project **sections** (например Палиндромы → `/games/palindromes/`) |
| `/new/profile/` | Edit Profile (name, email, avatar URL, VK) |
| `/new/team/` | Team: create / join / manage (POSTs to existing routes) |

## Folder config

Folders are defined in `games/views/new_ui.py` as `NEW_UI_FOLDERS`:

- `slug`, `title`, `description`
- `type` — `games` | `palindromes`

## Палиндромы

В админке у **TaskGroup** в JSON **tags** задать: `{"palindrome": true}` (можно вместе с другими ключами). - **Project «sections»**: игры из этого проекта отображаются на хабе Разделы отдельными плитками (одна страница на игру). Игра **Палиндромы** (`id=palindromes`) создаётся миграцией в project sections; группы заданий-палиндромов добавляют в эту игру в админке.

## Games → categories

Under **Десяточки**, games are grouped by `Game.theme`. Each row links to `/games/<game_id>/`.

## Follow-ups

- Optional: показывать палиндромы без фильтра по доступу (публичные наборы).
- HTMX; i18n.
