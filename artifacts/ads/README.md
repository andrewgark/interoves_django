# Yandex Direct (ads CLI)

Состояние и логи для `games.ops.social.ads` — не секреты, пути заданы в
`games/ops/social/ads/state.py`.

| Файл | В git | Назначение |
|------|-------|------------|
| `state.json` | да | IDs кампании, baseline, decisions |
| `audit.jsonl` | нет (`.gitignore`) | append-only журнал API-операций |
| `next_test_spec.json` | да | снимок спеки тестовой кампании (справочно) |
