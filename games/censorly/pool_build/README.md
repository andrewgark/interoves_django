# Censorly article pool builder

Goal: ~10k Russian Wikipedia titles suitable for Цензурка random/daily generation.

## Sources

1. **Vital 1000** — `Википедия:Список статей…/Статьи` (meta “every Wikipedia should have”).
2. **Vital ~10k** — `/10000/{theme}` subpages (People, Geography, History, …). Themes come for free.
3. **Top pageviews** — Wikimedia pageviews top for `ru.wikipedia` (recent days), minus service titles.
4. **Current curated pool** — `article_pool.txt` (keep proven titles).

## Hard filters

| Filter | Default | Notes |
|--------|---------|--------|
| Main namespace only | yes | Drop `Википедия:`, `Служебная:`, `Файл:`, interwiki |
| Not redirect (resolve) | yes | Keep canonical title |
| Not disambiguation | `pageprops.disambiguation` | |
| Not list / index | title `Список …` | Weak heuristic |
| Wikitext length ≥ N | **8000** bytes | Proxy for playable extract |
| Langlinks ≥ M | **20** | Cap probe at 21 via API |
| Title guessable | ≥1 content lemma | Same rule as puzzle build |
| Not pure year / digits | yes | |

## Theme balance (caps of final pool)

| Theme bucket | Cap |
|--------------|-----|
| Люди | 25% |
| География | 15% |
| История | 12% |
| Естественные + Биология + Математика + Техника | 28% |
| Общество + Гуманитарные + Философия + Антропология + Система мер | 15% |
| pageviews / uncategorized fill | 15% |

Fill order: vital-1000 → vital-10k under caps → pageviews under fill cap → current pool always eligible if it passes hard filters.

## Commands

```bash
../venv/interoves_django/bin/python manage.py build_censorly_article_pool harvest
../venv/interoves_django/bin/python manage.py build_censorly_article_pool filter
../venv/interoves_django/bin/python manage.py build_censorly_article_pool publish
```

### Keep/drop review UI

```bash
# whole article_pool.txt, skipping already decided titles
../venv/interoves_django/bin/python manage.py review_censorly_pool
# only obscure_drop_candidates
../venv/interoves_django/bin/python manage.py review_censorly_pool obscure
# http://127.0.0.1:8765/  ← убрать · → оставить
```

Decisions: `var/censorly_pool/pool_review_decisions.json` (synced with legacy
`obscure_review_decisions.json`). **«Применить к пулу»** removes drop-marked titles
and writes durable lists:

| File | Role |
|------|------|
| `games/censorly/article_pool_deny.txt` | never re-add (filter reject `review_deny`) |
| `games/censorly/article_pool_allow.txt` | curated remaining snapshot |
| `var/censorly_pool/review_keep.txt` / `review_drop.txt` / `review_remaining.txt` | local copies |

Artifacts in `var/censorly_pool/` (gitignored): `harvest.json`, `meta_cache.json`,
`rejected.jsonl`, `pool_draft.txt`, `report.md`, `review_sample.txt`.
