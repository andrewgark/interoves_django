# Censorly matching: agent playbook

Читать перед любой правкой матчинга Цензурок. Архитектура описана в
[docs/censorly-matching-architecture.md](../docs/censorly-matching-architecture.md),
роадмап и журнал — в [docs/censorly-dictionary-roadmap.md](../docs/censorly-dictionary-roadmap.md).

## Что живое

Геймплей открывает слова **только** через
`games/puzzles/censorly/lexical/match/__init__.py::_relation`
(`analyze_guess`, `matching_lemmas`).

* `semantics.explain` — дубль, расходится с геймплеем примерно на 1% пар.
* `match/resolver.py::relation_kind`, `lexical/graph`, `russian/{compiler,proofs,decisions,context}`
  и `text/roots.py` в геймплей не подключены. `relation_kind` закрывает
  около половины живых открытий.

Регрессионные тесты пиши через `matching_lemmas(tokens, guess)` или
`_relation(_GuessPack(g), _Pack(t, article_lemma=l))`. Тест на
`relation_kind` или `explain` не доказывает поведение в игре.

## Жалоба → слой

Сначала воспроизведи:

```bash
../venv/interoves_django/bin/python -m games.puzzles.censorly.offline.tools.review.explain_pair ВВОД ОТКРЫТОЕ
```

Посмотри `FINAL`, `source=` у обеих сторон и `detail`.

| Симптом в `explain_pair` | Куда чинить |
|---|---|
| FALSE NEGATIVE, `source=UNRESOLVED`, `morph X matches 0 game roots` | `data/reviewed_opaque_roots.tsv` (вся семья Тихонова), трек A |
| FALSE NEGATIVE, `source=ABSENT` или `UNRESOLVED` у наречия или производного, а однокоренное слово с корнем есть (обычно → обычный) | `data/reviewed_nest_anchors.tsv` (структура копируется с sense), очередь `wiktionary.nest_queue`, трек W4 |
| FALSE NEGATIVE, `source=ABSENT` у базового слова | `data/reviewed_derived_roots.tsv`, трек B |
| FALSE NEGATIVE, `UNRESOLVED` составного (`a+b`) | `reviewed_derived_roots.tsv` с полной структурой, трек C |
| FALSE NEGATIVE, `matches 2 game roots` | `data/reviewed_existing_root_assignments.tsv`, трек D |
| FALSE NEGATIVE, имя собственное ↔ прилагательное | `lexical/match/proper_names.py`, трек P |
| FALSE POSITIVE через общий корень семьи | sense-сплит в `lexical/splits.py`, скилл `censorly-root-family`, трек F |
| FALSE POSITIVE через ошибочное чтение pymorphy | `lexical/russian/reading_overrides.py` |

Чини семью целиком, а не одну пару. Отвергнутую семью запиши в
`data/rejected_opaque_roots.tsv` с причиной. Это память: `candidate_miner`
её больше не предложит.

## Инструменты

| Задача | Команда (`python -m games.puzzles.censorly.offline.tools.…`) |
|---|---|
| разбор пары | `review.explain_pair A B` или `--pairs-file` |
| метрика покрытия и дыры по трекам (`SUMMARY rooted_russian_tokens=…`) | `coverage_report --gaps /tmp/gaps.tsv` (~1 мин) |
| пересобрать корпус приоритетов (`data/corpus_lemma_freq.tsv.gz`) | `corpus.build_corpus --size 1000` (~35 мин, кэш в `var/censorly_corpus/`) |
| лист ревью топ-пропусков по семьям (трек A) | `review_sheet --start 0 --count 150` (~2 мин) |
| семьи составных без корня (трек C) | `compound_queue --count 150 --tsv /tmp/c.tsv` (~1 мин) |
| лист ревью базовых слов вне словарей (трек B) | `absent_sheet /tmp/gaps.tsv --count 160` (~1 мин, gaps из `coverage_report`) |
| очереди кандидатов (opaque, authorization holes) | `candidate_miner --output-dir /tmp/miner` (~40 с) |
| открывающие пары статьи | `candidates.article_candidates` |
| пропущенные открытия статьи | `candidates.near_miss_candidates` |
| Wiktionary: вердикт и гнёзда по каждой дыре | `wiktionary.compare --gaps-out /tmp/wk.tsv` (~1 мин) |

Данные `dictionaries/wiktionary_*` (сегментации, гнёзда `родств:*`,
«Родственные слова») пока читают только офлайн-тулы, runtime их не
трогает. Пересобираются из дампа командой
`python -I …/offline/tools/wiktionary/extract.py` (~15 мин, дамп в
`var/wiktionary/`). Гнездо — это этимологическое свидетельство, а не
игровая семья: в `родств:вид` попадает и «завидный».

Каждый запуск тратит около 10 с на `django.setup` и сборку банка. Пары
собирай в `--pairs-file`, а не гоняй по одной.

## Что runtime читает из `data/`

`dictionaries/*`, `reviewed_opaque_roots.tsv`, `reviewed_derived_roots.tsv`,
`reviewed_existing_root_assignments.tsv`, `reviewed_nest_anchors.tsv`,
`adverb_bases.tsv` (генерируется `adverb_rule`), `negation_pairs.tsv`, `half_compounds.tsv`. Остальные TSV и TXT — это ревью-очереди, снимки аудитов
или память тулов. Их изменение не влияет на игру.

## Тесты

```bash
INTEROVES_TEST_DB_PATH=/tmp/<свой>.sqlite3 ./run_tests.sh \
  games.tests.test_censorly_roots games.tests.test_censorly_lexical \
  games.tests.test_censorly_semantics games.tests.test_censorly
```

С 2026-11-06 набор зелёный: любое падение считай своим. Если тест
`test_semantic_structures_always_have_a_root_component` назвал лемму, значит
в sense-кластер `splits.py` попало слово без корня ни в одном словаре. Проверь
его через `explain_pair` и pymorphy, и либо убери слово (так было с
«западок»: его нет ни в одном словаре, pymorphy видит только «западкий»),
либо дай ему корень.
