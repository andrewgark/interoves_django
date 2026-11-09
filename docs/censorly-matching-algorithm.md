# Цензурки: алгоритм матчинга слов

Снимок от 2026-11-09 (master `295999ce`+). Описан **живой** путь
`lexical/match/__init__.py`: только его видит игрок. Параллельные реализации
(`semantics.explain`, `resolver.relation_kind`, `text/roots.py`) и история
перечислены в [censorly-matching-architecture.md](censorly-matching-architecture.md).
Где чинить конкретную жалобу, написано в [agents/censorly-matching.md](../agents/censorly-matching.md).

Цифры получены прогоном `rootbank.provenance()` и замером слоёв на
8 000 самых частых лемм корпуса. Если код изменился, пересчитай их.

---

## 0. Картинка целиком

```
                 СТАТЬЯ                                   ДОГАДКА
                    │                                        │
          text/tokenize.py                                   │
  kind=content|stop, lemma (pymorphy),                       │
  контекст __censorly_*__ для омографов                      │
                    │                                        │
       ┌────────────┴────────────┐                ┌──────────┴──────────┐
       │ _Pack на (surface,lemma)│                │ _GuessPack(surface) │
       │  ① видимость (service)  │                │  те же ①②③④, но     │
       │  ② чтения pymorphy      │◄── reading_    │  без контекста*     │
       │     + правки контекста  │    overrides   │                     │
       │  ③ лексемы (para,lemma) │                │                     │
       │  ④ корневые структуры ──┼──► rootbank ◄──┼─                    │
       └────────────┬────────────┘   (лемма →     └──────────┬──────────┘
                    │                 структуры)             │
          _ArticleIndex: постинги                            │
   fold/alias/proper/lexeme/root/grammar ──► candidate_ids ◄─┘
                                                 │
                                    для каждого кандидата
                                                 │
                                         _relation(g, t)   ← дерево решений, §5
                                                 │
                              непустой ответ → лемма токена открыта
```

\* Если написание догадки встречается в статье ровно с одной леммой,
догадка **берёт контекст статьи** (`analyze_guess`, `article_lemma=…`).

Открытие экзистенциальное: достаточно одной согласованной пары чтений.
Открывается не токен, а **сохранённая лемма** токена: открыв одну форму,
игрок открывает все токены с той же леммой.

---

## 1. Словарь терминов

| Термин | Что это | Пример |
|---|---|---|
| **fold** | NFC, без ударений, casefold, ё→е | `Ёлки` → `елки` |
| **чтение** (`_Reading`) | разбор pymorphy: `lemma, para_id, pos, score` | `стали` → (сталь, NOUN), (стать, VERB) |
| **лексема** | пара `(para_id, lemma)`: одна парадигма | все формы «улей» |
| **root id** | `fam:<семья Кузнецовой>`, `tikh:<морф>`, `root:<ручной>` | `fam:мен¹`, `root:read` |
| **sense** | `sense:<кластер>`: метка смысла, сама корнем не бывает | `sense:weather` |
| **структура** | отсортированный кортеж root id + sense. Сравнивается целиком | `('fam:бел', 'fam:снег')` |
| **семья** (`families_of`) | семья Кузнецовой до игровых сплитов. Нужна только отчёту near-miss | `мен¹` |
| **service** | токен открыт с начала игры | «и», «он», «5» |
| **контекст** | синтетическая лемма из токенизатора | `__censorly_nem_german__` |

---

## 2. Сущности

В алгоритме четыре вида сущностей.

### 2A. Внешние источники (не правим руками)

| Источник | Файл | Что даёт | Кто читает |
|---|---|---|---|
| pymorphy3 / OpenCorpora | пакет | чтения: лемма, парадигма, POS, score | всё |
| Кузнецова | `dictionaries/lemmas_to_roots.tsv` (54.9k строк) | лемма → корень | rootbank |
| Кузнецова, группы | `dictionaries/root_groups.txt` (4.4k) | корень → семья алломорфов | rootbank, splits |
| Тихонов | `dictionaries/RuMorphs-Lemmas.txt` (96k) | лемма → морфы `…:ROOT` | rootbank |
| Wiktionary | `dictionaries/wiktionary_*.tsv.gz` | морфемы, гнёзда | **только офлайн-тулы** |

### 2B. Наши данные

Сгруппированы по тому, **на какой вопрос** отвечают. Колонка «Где»
показывает, что многое до сих пор живёт в коде.

**Вопрос 1. Виден ли токен с начала?**

| Данные | Где | Размер |
|---|---|---|
| `STOP_WORDS` (предлоги, союзы, частицы) → `kind=stop`, в матчинг не попадают | `text/stopwords.py` | 104 |
| `_SERVICE` POS + порог score 0.15 | `semantics.initially_open` | код |
| `_NEVER_INITIAL_OPEN`, `_FORCE_INITIAL_OPEN_PRONOUNS` | `semantics.py` | 20 / 3 |
| `_PRONOUN_ADJECTIVE_LEMMAS`, `_INITIAL_NUMBER_LEMMAS`, цифры 0–9 | `semantics.py` | 35 / 19 / 10 |
| `closed_contexts`: контексты, снимающие service | `_ArticleIndex.__init__` | 10 + языки |
| `кому`+`кома` → не service | `_Pack.__init__`, хардкод | 1 |

**Вопрос 2. Какие чтения у написания?**

| Данные | Где | Размер |
|---|---|---|
| `REJECTED_READINGS`: (форма, лемма, POS) запрещены везде | `russian/reading_overrides.py` | 30 |
| `TARGET_LEMMA_READINGS`: (форма, лемма токенизатора) → разрешённые леммы | там же | 24 |
| `TARGET_LEMMA_OVERRIDES`: (форма, контекст) → синтетические леммы | там же | 19 |
| `guess_lemma_override`: «лет»→год, «Троя» с заглавной | там же, код | 2 |
| `proper_name_root_lemma` (Троя) | там же, код | 1 |
| `_LETTER_NAME_LEMMAS` (е, ш как буквы) | `match/__init__.py` **и** `semantics.py` | 2 |
| Контексты токенизатора (нем, том, г., в., стр., ПО, Ли, инициалы, языковые метки…) | `text/tokenize._annotate_contextual_readings` | ~15 правил |

**Вопрос 3. Какие корни у леммы?** (вход `rootbank._build_bank`)

| Данные | Где | Строк/лемм | Источник в банке |
|---|---|---:|---|
| sense-кластеры (`CLUSTERS`, 10 переопределений) | `splits.py` | 1 596 кластеров / 24.4k лемм | `SEMANTIC_SPLIT_ROOTED` |
| `MULTI_READINGS` | `splits.py` | 229 | то же |
| `SEMANTIC_ROOT_ASSIGNMENTS` (+ автокорни proper-кластеров) | `splits.py` | 685 | `SEMANTIC_ROOT_ASSIGNMENT` |
| ещё ~20 sense-кластеров прямо в `rootbank.py` | `rootbank.py` | ~150 лемм | `SEMANTIC_SPLIT_ROOTED` |
| proper-кластеры (Рим/римский…) | `match/proper_names.CLUSTERS` | 112 / 369 лемм | `SEMANTIC_*` |
| `reviewed_derived_roots.tsv`: полная структура | data | 2 163 | `REVIEWED_DERIVED_ROOT` |
| `reviewed_existing_root_assignments.tsv` | data | 49 | `REVIEWED_EXISTING_ROOT` |
| `reviewed_opaque_roots.tsv`: морф Тихонова → `tikh:*` | data | 1 359 морфов | `REVIEWED_OPAQUE_ROOT` |
| `rejected_fallback.tsv`: запрет совпадения по написанию | data | 36 | (блокирует fallback) |
| `negation_pairs.tsv`: не-X → X | data | 2 584 | `NEGATION_OF_BASE` |
| `reviewed_nest_anchors.tsv`: лемма → якорь гнезда | data | 371 | `REVIEWED_NEST_ANCHOR` |
| `adverb_bases.tsv` (генерируется `adverb_rule`) | data | 339 | `ADVERB_OF_ADJECTIVE` |
| `half_compounds.tsv`: пол-X → X | data | 331 | `HALF_COMPOUND` |
| `_DIGIT_CARDINALS`: «5» → «пять» | `rootbank.py` | 38 | `NUMERAL_*` |
| `_game_id`: чит/чет и голов/глав разведены | `rootbank.py`, код | 2 | — |
| `_ROOT_ALIAS_LEMMAS` (англ→английский), `_COMBINING_FORM_LEMMAS` (юго→юг) + правило `-ско/-ико` | `semantics.py` | 10 / 15 | не в банке: подмешиваются в `_roots` |

**Вопрос 4. Какие пары связаны напрямую, мимо корней?**

| Данные | Где | Размер | Транзитивно? |
|---|---|---:|---|
| `_ALIAS_GROUPS` (кг/килограмм, фр/французский) + арабские↔римские 1–99. Сравниваются **написания**, не леммы | `semantics.py` | 94 + 99 | нет, только внутри группы |
| `_GRAMMAR_GROUPS` (два/второй, хороший/лучше) | `semantics.py` | 13 | внутри группы |
| `_REVIEWED_LEXICAL_LINKS` | `match/__init__.py` | 3 | — |
| `PAIR_RELATIONS` + `GIVEN_NAME_VARIANTS` (Александр/Саша, Британия/британский) | `match/proper_names.py` | 617 рёбер | **да**, связные компоненты |
| `LANGUAGE_LABEL_LEMMAS` (кит.→китайский в позиции метки) | `reading_overrides.py` | 6 | — |

### 2C. Решатели

Решатель — функция, которая отвечает на один вопрос и чей ответ потребляют
другие.

| # | Решатель | Вопрос | Где |
|---|---|---|---|
| R0 | токенизатор | content или stop? какая лемма? какой контекст? | `text/tokenize.py` |
| R1 | `initially_open` | виден ли токен сразу (service)? | `semantics.py` |
| R2 | выбор чтений: `readings_of` / `guess_readings_of` / `preferred_readings_of` + overrides | какие чтения считать? | `semantics.py`, `reading_overrides.py` |
| R3 | `_build_bank` + `_authorize_component` + `_explode` | какие структуры у леммы? | `rootbank.py` |
| R4 | `_roots` | какие структуры у написания (чтения + surface + метки + дефис)? | `semantics.py` |
| R5 | `_Pack` / `_GuessPack` | собрать R1–R4 в один пакет | `match/__init__.py` |
| R6 | `candidate_ids` | кого вообще проверять? (префильтр) | `_ArticleIndex` |
| R7 | `_relation` | открывает ли догадка токен и каким слоем? | `match/__init__.py` |
| R8 | `_reject` | почти открыл? (только отчёт) | `match/__init__.py` |

### 2D. Контекст статьи

Токенизатор пишет в `lemma` либо обычную лемму pymorphy, либо синтетический
маркер `__censorly_*__`. Дальше маркер работает как ключ в
`TARGET_LEMMA_READINGS` / `TARGET_LEMMA_OVERRIDES` и в `closed_contexts`.
Это единственный канал, по которому предложение влияет на матчинг.

---

## 3. Сборка пакета (`_Pack`, токен статьи)

```
_Pack(surface, article_lemma)
│
├─ service = initially_open(surface)            (R1)
│    └─ кому+кома → False;  контекст из closed_contexts → False (в индексе)
│
├─ reads = preferred_readings_of(surface)       (R2: при омографии предпочесть
│    │                                           существительное в начальной форме)
│    ├─ е/ш → только чтение-буква
│    ├─ Троя с заглавной → reads=(), roots = структуры «троя»
│    ├─ (surface, ctx) ∈ TARGET_LEMMA_OVERRIDES → reads = чтения синтетических лемм
│    └─ (surface, lemma) ∈ TARGET_LEMMA_READINGS → отфильтровать reads
│
├─ lexemes = {(para_id, lemma) for reads}
│
├─ roots (R4):
│    ├─ override        → structures_of(каждой синтетической леммы)
│    ├─ контекстный     → _roots(reads, surface, include_surface=False)
│    └─ иначе           → _roots(guess_readings_of(surface), surface)
│                          ├─ structures_of(fold(surface))
│                          ├─ structures_of(label_lemmas)        фр→французский, юго→юг
│                          ├─ structures_of(лемма каждого чтения)
│                          ├─ structures_of(предсказанной леммы, если она в reviewed_derived)
│                          └─ дефис: декартово произведение структур частей (≤32)
│
└─ families = семьи Кузнецовой (только для отчёта near-miss)
```

`_GuessPack` вызывает `super().__init__` и затем **пересчитывает** reads,
lexemes, roots и families по-своему: чтения берёт из `guess_readings_of`
(предпочесть чтение, у которого написание и есть лемма), применяет
`guess_lemma_override` и сохраняет `lexemes_by_fold` для обратной лексемы.

Три разных набора чтений используются для трёх разных целей:

| Цель | Токен статьи | Догадка |
|---|---|---|
| лексема | `preferred_readings_of` | `guess_readings_of` |
| корни | `guess_readings_of` | `guess_readings_of` |
| proper | `readings_of` (сырые, без overrides) | `readings_of` |

---

## 4. Банк корней (`rootbank._build_bank`, лемма → структуры)

Строится один раз на процесс (~3.8 с, ~65 МБ). На 2026-11-09 в банке
76 460 лемм.

### 4.1. Порядок для одной леммы: побеждает первый

```
лемма
│
├─① есть в _SENSE?  (splits.CLUSTERS, MULTI_READINGS, proper_names, inline _sense)
│     корневая часть:
│       SEMANTIC_ROOT_ASSIGNMENTS[lemma]?   → её структуры
│       иначе Тихонов ≥2 морфа              → _explode
│       иначе Кузнецова                     → её корни
│       иначе 1 морф                        → _authorize_component
│     к каждой структуре дописывается sense:*
│     (имя вида fam:/tikh:/root: в MULTI_READINGS ставится как корень напрямую)
│     нет корня → UNRESOLVED_SEMANTIC_NO_ROOT, стоп
│                                              ─► SEMANTIC_SPLIT_ROOTED      24 073
│                                              ─► SEMANTIC_ROOT_ASSIGNMENT      685
├─② reviewed_derived_roots.tsv                ─► REVIEWED_DERIVED_ROOT       2 156
├─③ reviewed_existing_root_assignments.tsv    ─► REVIEWED_EXISTING_ROOT         48
├─④ Тихонов ≥2 морфа  → _explode: каждый морф через _authorize_component,
│     любой неавторизованный → вся лемма UNRESOLVED; >32 комбинаций → TOO_AMBIGUOUS
├─⑤ есть в Кузнецовой                         ─► KUZNETSOVA(_MULTI)      30 916
└─⑥ Тихонов 1 морф → _authorize_component     ─► REVIEWED_OPAQUE_ROOT     5 763
                                               ─► UNIQUE_SPELLING_FALLBACK 9 148
                                               ─► UNRESOLVED              31 257
```

### 4.2. Авторизация одного морфа Тихонова (`_authorize_component`)

```
морф → кандидаты = все игровые корни с тем же написанием (без надстрочных цифр)
│
├─ у леммы есть корни Кузнецовой?  → оставить пересечение; пусто → UNRESOLVED
├─ морф в reviewed_opaque и в Кузнецовой такого написания нет → tikh:<морф>
├─ ровно 1 кандидат и (морф, корень) ∉ rejected_fallback → UNIQUE_SPELLING_FALLBACK
└─ иначе → UNRESOLVED   (detail: «morph X matches N game roots»)
```

### 4.3. Постпроходы: копируют структуры другой леммы

Идут строго в таком порядке:

1. `negation_pairs`: не-X получает структуры X, если своих нет **или** свои
   без sense и их корни ⊆ корней X.
2. `reviewed_nest_anchors`: только леммам без корня, цепочки до глубины 3.
3. `adverb_bases`: так же, ручные якоря из п. 2 важнее.
4. `negation_pairs` ещё раз, только безкорневым (чтобы «необычно» пошло за «обычно»).
5. `half_compounds`: **перезаписывает** структуры безусловно.
6. `_DIGIT_CARDINALS`: «5» = структуры «пять»; у безкорневого числительного
   появляется `root:numeral_N`.

---

## 5. Дерево решения `_relation(guess, target)`

Порядок важен: побеждает первый сработавший слой.

```
_relation(g, t)
│
├─ пустой fold                                          → закрыто
├─ fold(g) == fold(t)
│     оба service → закрыто, иначе                      → exact
├─ t в контексте «о нём» (NEM_PRONOUN)
│     общая лексема и g читается как он/оно → lexeme, иначе закрыто   [хардкод]
├─ alias-группа (кг/килограмм, X/10)                    → alias
├─ _REVIEWED_LEXICAL_LINKS                              → reviewed_lexical
├─ одна сторона цифры и нет numeric_root_match          → закрыто
├─ g или t service (и не числовое совпадение):
│     ├─ общая лексема                                  → lexeme
│     ├─ общая структура и есть местоимённое чтение     → root
│     ├─ g == «нем» как метка «немецкий» и общая структура → root    [хардкод]
│     ├─ grammar-группа с числительным 2..9             → grammar
│     └─ иначе                                          → закрыто
├─ proper-компонента общая (воз ≠ ВОЗ)                  → proper
├─ g или t не русское                                   → закрыто
├─ общая лексема (para_id, lemma)                       → lexeme
├─ обратная лексема (лемма g по написанию t)            → lexeme
├─ общая структура (целиком, с sense)                   → root
├─ root_sense_compatible (SENSE_COMPATIBLE пуст)        → мёртвый слой
├─ grammar-группа                                       → grammar
└─ иначе                                                → закрыто
```

`candidate_ids` строит кандидатов по тем же ключам: fold, alias, proper,
lexeme, root, grammar. Если догадка — service без местоименного или
числового чтения, кандидаты ищутся только по fold. **Слой, у которого нет
постинга, в игре не сработает**, даже если `_relation` вернёт для пары
непустой ответ (см. §7, `_REVIEWED_LEXICAL_LINKS`).

### Сколько срабатывает каждый слой

Замер: 8 000 частых лемм корпуса, 4 000 догадок по их же формам, все
кандидаты с другой леммой.

| Слой | Открытий | Примеры |
|---|---:|---|
| `root` | 10 771 (95%) | был→бытия, года→полгода |
| `lexeme` | 370 | года→г, был→есть, начале→начал |
| `proper` | 104 | александр→саша, британской→британии, поля→Поль |
| `alias` | 40 | 10→X, русский→рус |
| `grammar` | 1 | больше→большая (обратное направление идёт через lexeme) |
| `reviewed_lexical` | 0 | — |
| `root_sense_compatible` | 0 | — |

Вывод: игра — это **сравнение структур из банка** плюс лексема. Всё
остальное — заплатки, которые вместе дают около 1% открытий, но занимают
большую часть ветвлений.

---

## 6. Почему так запутано

Каждый крайний случай чинили в том месте, где его заметили. Поэтому одна и
та же идея реализована несколькими механизмами:

| Идея | Механизмы |
|---|---|
| «X читается как лемма Y» | `TARGET_LEMMA_OVERRIDES`, `guess_lemma_override`, `proper_name_root_lemma`, `_ROOT_ALIAS_LEMMAS`, `_COMBINING_FORM_LEMMAS`, `LANGUAGE_LABEL_LEMMAS`, `_DIGIT_CARDINALS` |
| «у леммы X корни леммы Y» | `negation_pairs`, `nest_anchors`, `adverb_bases`, `half_compounds`, `_DIGIT_CARDINALS`, `reviewed_existing` |
| «X и Y связаны напрямую» | alias, grammar, `_REVIEWED_LEXICAL_LINKS`, proper `PAIR_RELATIONS` |
| «у X и Y общая семья» | общая структура в банке, proper `CLUSTERS` |
| «чтение X неверно» | `REJECTED_READINGS`, `TARGET_LEMMA_READINGS`, `_LETTER_NAME_LEMMAS`, хардкоды «воз», «кому» |
| «токен открыт с начала» | `STOP_WORDS`, `initially_open`, `closed_contexts`, хардкод «кому» |

---

## 7. Что можно упростить

Отсортировано от «удалить сегодня» к «большой рефакторинг». Инвариант
для любого шага: снимок «лемма → структуры» и результат `matching_lemmas`
на корпусе не меняются, если шаг не меняет поведение намеренно.

### Мёртвый код (удалить без изменения поведения)

* `_ATOMIC_LEXICALIZED` в `rootbank.py` (55 строк) **нигде не читается**.
  Его леммы уже переехали в `SEMANTIC_ROOT_ASSIGNMENTS`.
* `SENSE_COMPATIBLE = frozenset()` и обе копии `_root_sense_compatible`
  (`match/__init__.py`, `semantics.py`): слой выполняется на каждую пару и
  ничего не открывает.
* `_REVIEWED_LEXICAL_LINKS`: у всех трёх пар (фамилия/фамильный,
  поменять/сменить, долгоживущий/долгоживучий) теперь одинаковые структуры,
  они открываются через `root`. Слой лишний. К тому же у него нет постинга
  в `candidate_ids`: новая пара без общей структуры в игре не сработала бы.
* `TRANSLIT_GROUPS = ()`.

### Дубли (свести в одно место)

* `_DIGIT_CARDINAL_LEMMAS` скопирован трижды (`match/__init__`, `semantics`,
  `rootbank._DIGIT_CARDINALS`), `_numeric_root_match` дважды,
  `_LETTER_NAME_LEMMAS` дважды.
* `_GuessPack.__init__` вызывает `_Pack.__init__` и пересчитывает почти
  всё заново. В `_Pack.__init__` `target_lemma_override` и `target_readings`
  вызываются дважды. Лучше одна функция
  `analyze(surface, context, mode='guess'|'target') -> Pack`, где различия
  режимов видны в одном месте (таблица в §3).
* `semantics.explain` дублирует `_relation` (это пункт R2 архитектурного
  документа, всё ещё открыт).
* При `CENSORLY_LEXICAL_RESOLVER=0` работает ещё и старое сравнение лемм
  (`play.lemmas_matching_guess`). Если везде включено, ветку можно удалить.

### Хардкоды в коде решателей → в таблицы

`кому`/`кома` в `_Pack`, `воз`/`ВОЗ` в `_proper_pair_from_readings`, две ветки
«нем» в `_relation`, «лет» и Троя в `guess_lemma_override`, чит/чет и голов/глав
в `_game_id`, ~20 sense-кластеров в теле `rootbank.py`. Каждый из них —
строка данных, которая сейчас требует читать код, чтобы о ней узнать.

### Свести 11 источников корней к трём видам

Почти все источники в §4 — это одно из трёх:

1. **явная структура**: `reviewed_derived`, `reviewed_existing`,
   `SEMANTIC_ROOT_ASSIGNMENTS`, sense-кластеры;
2. **копия структур другой леммы** (якорь): negation, nest anchor, adverb,
   half compound, digit, плюс `_ROOT_ALIAS_LEMMAS` и `_COMBINING_FORM_LEMMAS`,
   которые сейчас подмешиваются в `_roots` мимо банка;
3. **выведено из словарей**: Кузнецова, Тихонов + opaque/fallback/rejected.

Тогда банк — это один проход: (3), затем (1) поверх, затем (2) топологически
по якорям. Таблица якорей одна: `lemma, anchor, kind, note`, где `kind`
in {negation, nest, adverb, half, digit, label}. Исчезает порядок
«negations → anchors → adverbs → negations ещё раз → half (перезапись)», а
провенанс получается из `kind`.

### Свести прямые связи к структурам

* **proper**: имена уже частично живут в банке (`proper_names.CLUSTERS` →
  `root:proper_*`). Параллельно `PAIR_RELATIONS` даёт транзитивные компоненты
  по **сырым** чтениям `readings_of`, мимо `preferred_readings_of` и
  контекстных правок. Живой ложноположительный пример: догадка «Полина»
  открывает «поля» и «полей» (поле), потому что у pymorphy «поля» — ещё и
  имя Поля, а Поля↔Полина есть в `GIVEN_NAME_VARIANTS`. Если перевести
  компоненты в структуры банка, слой `proper` и его постинг исчезнут, а
  выбор чтений станет общим с остальными слоями.
* **alias** сравнивает написания (`fold`), а не леммы. Поэтому «кг»
  открывает только «килограмм» в именительном падеже, но не «килограммов»,
  и наоборот (проверено через `matching_lemmas`). Скорее всего, это
  незамеченный пропуск. Если alias сделать якорем `кг → килограмм` в
  таблице якорей, падежи заработают, но «кг» начнёт открывать и
  «килограммовый». Если это нежелательно, alias остаётся слоем, но
  сравнивает леммы.
* **grammar** срабатывает 1 раз на 4 000 догадок: формы одной парадигмы уже
  ловит лексема. Достаточно оставить числительные (два/второй) и сделать их
  якорями или общими структурами.

После этого `_relation` сводится к:

```
exact → (service-гейт) → lexeme → root → закрыто
```

плюс одно исключение на контекст «о нём».

### Данные как код

`splits.py` (8.4k строк, `CLUSTERS` переопределяется 10 раз) — пункт R4
архитектурного документа. Golden-снимок банка (R1) на 2026-11-09 тоже не
сделан, а без него безопасно не сделать ни этот шаг, ни шаги выше.

### Тест на согласованность префильтра

Нужен тест: на выборке пар «полный перебор `_relation`» == «через
`candidate_ids`». Он ловит слой, который `_relation` открывает, а индекс не
находит.

---

## 8. Как пересчитать цифры из этого документа

```python
from collections import Counter
from games.puzzles.censorly.lexical.match import rootbank
Counter(v['source'] for v in rootbank.provenance().values())   # §4
```

Распределение слоёв (§5): построить `_ArticleIndex` по топу
`offline.corpus._from_file()` (лемма, `top_surface`) и посчитать
`_relation` для `candidate_ids(..., include_rejections=False)`.
