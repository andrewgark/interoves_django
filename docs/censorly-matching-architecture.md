# Цензурки: архитектура матчинга

Снимок от 2026-11-05. Цифры получены командой
`games.puzzles.censorly.offline.tools.coverage_report` и замерами из этого
разбора. Если код изменился, пересчитай их, а не доверяй документу.

Роадмап пополнения словарей лежит в
[censorly-dictionary-roadmap.md](censorly-dictionary-roadmap.md), правила для
агентов — в [agents/censorly-matching.md](../agents/censorly-matching.md).

## 1. Что решает матчер

Игрок вводит слово (догадку). Для каждого контентного токена статьи нужно
решить, открывается ли он. Открытие **экзистенциальное**: достаточно одной
согласованной пары чтений «догадка × токен». Корневые структуры сравниваются
как мультимножества, поэтому общий корень одной части сложного слова сложное
слово не открывает.

## 2. Живой путь в геймплее

```
play.py / article/redact.py
  └─ lexical/match/__init__.py        analyze_guess, decision, matching_lemmas
       ├─ _ArticleIndex               индекс статьи: поверхность×чтение → постинги
       │                              (LRU-кэш на процесс, single-flight build)
       ├─ _GuessPack / _Pack          анализ догадки и токена статьи
       │    ├─ semantics.py           чтения pymorphy, служебные слова, _roots,
       │    │                         alias, grammar, proper pairs
       │    ├─ russian/reading_overrides.py  контекстные чтения (нем, том, Ли…)
       │    └─ match/rootbank.py      лемма → корневые структуры
       │         ├─ dictionaries/lemmas_to_roots.tsv   Кузнецова (54.9k строк)
       │         ├─ dictionaries/root_groups.txt       группы алломорфов
       │         ├─ dictionaries/RuMorphs-Lemmas.txt   Тихонов (96k лемм)
       │         ├─ splits.py                          sense-кластеры (8268 строк)
       │         ├─ match/proper_names.py              кластеры имён собственных
       │         └─ data/reviewed_*.tsv, negation_pairs.tsv, half_compounds.tsv
       └─ _relation()                 решение по слоям (порядок важен)
```

Слои `_relation` идут по порядку, побеждает первый сработавший:
`exact` → контекст «нем» → `alias` → `_REVIEWED_LEXICAL_LINKS` → цифры →
служебные слова (только lexeme/pronoun/numeral) → `proper` → не-русское
закрыто → `lexeme` (общий `(para_id, lemma)`) → обратный lexeme → `root`
(общая структура) → `root_sense_compatible` → `grammar`.

### Как лемма получает корень (`rootbank._build_bank`)

Приоритет сверху вниз, срабатывает первый:

| Источник | Лемм в банке | Что это |
|---|---:|---|
| `SEMANTIC_SPLIT_ROOTED` / `SEMANTIC_ROOT_ASSIGNMENT` | 24 020 / 500 | лемма есть в `splits.py` или `proper_names.py`: корень плюс `sense:*` |
| `REVIEWED_DERIVED_ROOT` | 241 | `reviewed_derived_roots.tsv`: полная структура вручную |
| `REVIEWED_EXISTING_ROOT` | 43 | `reviewed_existing_root_assignments.tsv` |
| `KUZNETSOVA(_MULTI)` | 30 909 | семья Кузнецовой |
| `REVIEWED_OPAQUE_ROOT` | 4 117 | морф Тихонова из `reviewed_opaque_roots.tsv`, у которого нет написания в Кузнецовой |
| `UNIQUE_SPELLING_FALLBACK` | 9 515 | морф Тихонова совпал по написанию ровно с одним корнем Кузнецовой. **Никто это не проверял** |
| `NEGATION_OF_BASE`, `HALF_COMPOUND`, `NUMERAL_*` | 2 909 | постобработка поверх построенного банка |
| `REVIEWED_NEST_ANCHOR` | 144 (с 2026-11-05) | `reviewed_nest_anchors.tsv`: лемма без корня копирует структуры якоря из своего гнезда Wiktionary; проход идёт между двумя проходами отрицаний |
| `ADVERB_OF_ADJECTIVE` | 302 (с 2026-11-06) | `adverb_bases.tsv` (генерирует `offline/tools/adverb_rule.py`): наречие копирует структуры прилагательного; только там, где runtime не связывает его иначе |
| `UNRESOLVED` | **34 500** | корня нет: 20.5k с одним морфом, 14.7k составных |

Лемма, которой нет ни в одном словаре, получает `ABSENT`: её нет в банке
вообще. Это касается даже базовых слов: «вид», «роль», «производная».

### Покрытие на фикстурном корпусе (29 статей, 42k контентных токенов)

* У **72.3%** контентных токенов есть корень (без учёта служебных).
* `ABSENT`: 19.4% токенов. Из них латиница и греческие буквы из формул 4.3k
  (это нормально), обычные слова 1.9k, неизвестные pymorphy 1.1k, имена 0.8k.
* `UNRESOLVED`: 5.5% токенов. Opaque-одиночки (теорема, вероятность, помощь)
  1.2k, составные (треугольник, атмосфера, взаимодействие) 0.95k.

Слово без корня открывается только своими словоформами через `lexeme`.
Однокоренные слова оно не открывает. Это главный источник жалоб
FALSE NEGATIVE.

## 3. Параллельные механизмы, которых нет в проде

Это главный источник путаницы для агентов. В пакете четыре реализации
«открывается ли пара», а в геймплее работает только одна.

| Модуль | Статус | Кто использует |
|---|---|---|
| `lexical/match/__init__.py::_relation` | **живой** | play, redact, `explain_pair`, `candidate_miner`, `article_candidates` |
| `lexical/semantics.py::explain` | дубль `_relation` без overrides, `_REVIEWED_LEXICAL_LINKS` и контекста | 34 вызова в `test_censorly_semantics`, аудиты, wikidata/proper-тулы |
| `lexical/match/resolver.py::relation_kind` + `lexical/graph` (`ru_graph.tsv.gz`) + `russian/{compiler,proofs,decisions,context}.py` | старый граф доказательств, в докстринге прямо написано «not wired in» | `offline/audit/prelaunch.py`, POSITIVES/NEGATIVES в `test_censorly_lexical` |
| `text/roots.py::opens_with` | ещё более ранний кандидат | только тесты (`test_censorly_roots`, memory footprint) |

Насколько они расходятся, проверено на 6 000 пар из фикстур (пары токенов с
общими первыми 4 буквами):

* `explain` и живой путь расходятся в 54 парах (~1%): служебные слова,
  порядковые («второй»/«второго»), «в»/«во». Тест на `explain` может пройти,
  а игра повести себя иначе.
* `resolver.relation_kind` закрывает **1 503 из 2 797** живых открытий. Поэтому
  `prelaunch_report.txt`, `prelaunch_fuzz.tsv` и тесты POSITIVES/NEGATIVES
  измеряют не то, что видит игрок.

## 4. `splits.py`

8 268 строк, в которых почти нет кода. По сути это база данных, записанная как
Python.

* `CLUSTERS` переопределяется **10 раз** подряд: базовые кластеры,
  `_REVIEW_SPLITS` (удаляет леммы из старых кластеров), `_REVIEW_EXTRA_CLUSTERS`,
  `_review_family_clusters()`, `_SENSE_MOVES`, `_corrected_clusters`,
  `_family_corrected`, `_kosa_clusters`, затем ещё три `CLUSTERS + (...)` и
  `_LATE_REVIEW_SPLITS`. Итоговое состояние кластера можно узнать, только
  выполнив модуль. Агент, который правит ранний блок, легко получает правку,
  перекрытую поздним.
* `_classify_review_lemma` занимает ~1 150 строк: 130 веток
  `if family == ...` и 382 проверки `startswith` по префиксам. При импорте он
  читает `lemmas_to_roots.tsv` и `root_groups.txt`. Это код-классификатор, а не
  данные: новая лемма в словаре молча попадёт в `sense:review_misc` или в первый
  подходящий префикс.
* `SEMANTIC_ROOT_ASSIGNMENTS` (~450 строк) и `MULTI_READINGS` (2 блока)
  смешаны с кластерами.
* `SENSE_COMPATIBLE = frozenset()`: слой `root_sense_compatible` в
  `_relation` и `explain` мёртв, но выполняется на каждую пару.
* Похожие данные-как-код есть и в других местах: `_REVIEWED_LEXICAL_LINKS`
  в `match/__init__.py`, `ACCEPT`/`REJECT` в `russian/decisions.py` (живут
  только в мёртвом графе), `_ATOMIC_LEXICALIZED` и `_DIGIT_CARDINALS` в
  `rootbank.py`.

Импорт `splits.py` занимает ~0.9 с.

## 5. Производительность

| Этап | Время | Память |
|---|---|---|
| `django.setup()` | 6.1–6.5 с | 150 МБ |
| импорт `rootbank` + `splits` | 0.7–0.9 с | — |
| сборка банка (`_build_bank`) | **3.8 с** | +65 МБ, 72k лемм |
| `provenance()` (диагностика, второй полный проход) | +3.9 с | только тулы |
| индекс одной статьи (~8k токенов) | 0.5–0.7 с | LRU на процесс |
| одна догадка по горячему индексу | 0.1–1 мс, с отчётом 2–4 мс | — |
| токенизация статьи | ~0.35 с | — |
| `candidate_miner` целиком | ~40 с | — |

Горячий путь в порядке. Узкие места такие:

1. **Холодный старт каждого воркера**: 3.8 с и 65 МБ на сборку банка из трёх
   текстовых словарей плюс ~1 с на импорт. Всё это пересчитывается в каждом
   процессе. Кэша сборки (pickle/marshal с ключом по хешу входных файлов) нет.
2. **Двойной парсинг словарей**: `splits._review_family_clusters` читает
   Кузнецову при импорте, `_build_bank` читает её ещё раз, `provenance()`
   строит весь банк заново с `record=True`.
3. Первая догадка по статье стоит 0.5–0.7 с на построение индекса. После
   деплоя или вытеснения из LRU это платит игрок.
4. Офлайн-тулы каждый раз платят ~11 с за django, банк и provenance. Каждый
   запуск `explain_pair` из-за этого занимает 10+ секунд, а агенты вызывают
   его десятками.

## 6. Читаемость и «агенты не понимают, что происходит»

1. Четыре реализации матчинга (раздел 3). Агент находит `relation_kind` или
   `explain`, пишет тест на них, тест зелёный, а в игре поведение другое.
2. `.cursor/skills/censorly-root-family/SKILL.md` ссылался на несуществующие
   пути (`games/censorly/...`, `semantic_splits.py`, `match.py`).
   Исправлено 2026-11-05.
3. Нет единой точки входа «жалоба → какой слой чинить». Решение размазано
   между `splits.py`, 5 TSV `reviewed_*`, `reading_overrides.py`,
   `proper_names.py` и `_REVIEWED_LEXICAL_LINKS`.
4. Нет памяти отказов. Майнер каждый раз заново предлагал казан, титан и
   прус. Добавлен `data/rejected_opaque_roots.tsv`, майнер его читает.
5. В `data/` лежат десятки отчётов (`prelaunch_*`, `inflection_*`,
   `full_semantics_*`, `ru_graph.*`, `root_family_*`). Из имени файла не
   понять, вход это для runtime, ревью-очередь или устаревший снимок. Runtime
   читает только: `dictionaries/*`, `reviewed_opaque_roots.tsv`,
   `reviewed_derived_roots.tsv`, `reviewed_existing_root_assignments.tsv`,
   `reviewed_nest_anchors.tsv`, `negation_pairs.tsv`, `half_compounds.tsv`.
6. Фикстурный корпус всего 29 статей с сильным естественнонаучным перекосом.
   Приоритеты майнера (`corpus_frequency`) почти везде 0–2, поэтому
   приоритизация слабая.
7. ~~Тест `test_semantic_structures_always_have_a_root_component` падает на
   master («западок»).~~ Исправлено 2026-11-06: несуществующее слово убрано
   из `sense:west`.

## 7. План рефакторинга

Шаги упорядочены так, чтобы каждый можно было закоммитить отдельно, не
ломая геймплей. Инвариант для всех шагов: снимок «лемма → структуры» до и
после совпадает побайтно (golden-файл), если шаг не меняет данные намеренно.

**R1. Golden-снимок банка.** Тул выгружает `structures_of` для всех лемм в
отсортированный TSV, а тест сравнивает хеш. Без него рефакторинг `splits.py`
небезопасен. *Малый.*

**R2. Один источник истины для «открывается ли пара».**
`semantics.explain` превращается в тонкую обёртку над
`_relation(_GuessPack(g), _Pack(t))`. Тесты переводятся на неё или на
`matching_lemmas`. POSITIVES/NEGATIVES из `test_censorly_lexical` переводятся
с `relation_kind` на живой путь, расхождения разбираются как реальные баги.
*Средний.*

**R3. Карантин мёртвых путей.** `resolver.py`, `lexical/graph`,
`russian/{compiler,proofs,decisions,context}.py`, `text/roots.py` и
`ru_graph.*` переносятся в `offline/legacy/` или удаляются вместе с
зависимыми аудитами. `prelaunch.py` переписывается на живой путь.
`SENSE_COMPATIBLE` и `root_sense_compatible` удаляются (или наполняются
данными). Общие хелперы (`_families`, `_lines`, `_norm`, `_plain`,
`_strip_note`) переезжают из `russian/compiler.py` в `lexical/dictionaries.py`.
*Средний.*

**R4. `splits.py` превращается в данные.** Выполнить текущий модуль и
выгрузить **итоговые** `CLUSTERS`, `MULTI_READINGS` и
`SEMANTIC_ROOT_ASSIGNMENTS` в `data/senses/<family>.tsv` (лемма → sense) или в
один `data/sense_clusters.tsv` с колонками `sense lemma family note`. Тогда
10 слоёв переопределений и `_classify_review_lemma` сворачиваются в явное
членство. Новые леммы перестают молча попадать в `review_misc`. Вместо
этого валидатор сообщает: «лемма семьи X без sense». Загрузчик занимает
~100 строк, golden из R1 обязан совпасть. Заметки из комментариев
переносятся в колонку `note`. Для агентов это главный выигрыш: одна
лемма — одна строка, `grep` показывает итоговое состояние. *Большой, но
механический.*

**R5. Единый реестр решений.** Все ручные решения (opaque, derived, existing,
lexical links, rejected) получают одну схему валидации и одну команду
`validate_data`: дубли, конфликты, несуществующие леммы, `sense:` в
`root_structure`. `_REVIEWED_LEXICAL_LINKS` уезжает в TSV. *Средний.*

**R6. Кэш собранного банка.** После `_build_bank` результат сериализуется в
`var/censorly_bank.<sha>.pickle`, где ключ — хеш всех входных файлов и версии
кода. Холодный старт сократится с 3.8 до <0.5 с. `provenance` хранится
в том же кэше, и тулы перестают строить банк дважды. *Малый-средний.*

**R7. Быстрый CLI для агентов.** `explain_pair` принимает много пар (уже есть
`--pairs-file`) и, после R6, использует кэш банка. Желательно добавить
`--json` для машинного чтения. *Малый.*
