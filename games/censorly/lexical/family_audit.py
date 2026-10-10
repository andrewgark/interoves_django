"""Root-family inventory and full simplified-resolver fuzz.

Review unit is a dictionary root family, not a pair. The fuzz uses the
same readings, lexemes, grammar, aliases and root multisets as
``semantics.explain``.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interoves_django.settings')

import django

django.setup()

from games.censorly.lexical.core import fold
from games.censorly.lexical.prelaunch import (
    OOV_GUESSES,
    UNICODE_GUESSES,
    content_surfaces,
    iter_fixtures,
    walk_dictionary,
)
from games.censorly.lexical.rootbank import _SENSE, _game_id, structures_of
from games.censorly.lexical.russian.compiler import (
    _families,
    _lines,
    _norm,
    _plain,
    _strip_note,
)
from games.censorly.lexical.russian.decisions import REJECT
from games.censorly.lexical.semantics import (
    _ALIAS_GROUPS,
    _GRAMMAR_GROUPS,
    initially_open,
    readings_of,
)
from games.censorly.tokenize import tokenize_text
from games.matcher.norm_matcher import MORPH_ANALYZER

DATA = Path(__file__).resolve().parent / 'data'
_KUZ_LEMMAS = Path(os.environ.get('CENSORLY_KUZ_LEMMAS', '/tmp/ruroots/lemmas_to_roots.tsv'))
_KUZ_GROUPS = Path(os.environ.get('CENSORLY_KUZ_GROUPS', '/tmp/ruroots/root_groups.txt'))
_TIKHONOV = Path(os.environ.get('CENSORLY_TIKHONOV', '/tmp/rumorphs/RuMorphs-Lemmas.txt'))
_PREFIXES = (
    'пере', 'пред', 'при', 'под', 'про', 'раз', 'рас', 'вос', 'воз', 'вз',
    'вы', 'до', 'за', 'из', 'ис', 'на', 'об', 'от', 'по', 'со', 'у',
)


# verdict, reason, proposed subfamilies. Absence is UNREVIEWED.
# SPLIT = already cut into sense:* clusters.
# TO_SPLIT = backlog: this family needs a modern-sense cut.
# AGENT_THINKS_SAFE = agent judges one modern family; do not cut unless revisited.
# SAFE_ONE_FAMILY = legacy human-safe (prefer AGENT_THINKS_SAFE for new marks).
VERDICTS = {
    'глав|голав|голов': (
        'SPLIT',
        'Голова и глава отделены от голавля; головня сохраняет чтения горящей головни и болезни растений.',
        'HEAD/CHIEF: голова, глава; FISH: голавль, голавлевый; FIREBRAND: головешка, головня; SMUT: головня, головневый',
    ),
    'крас¹|краш': (
        'SPLIT',
        'Красота и красный цвет — разные современные семьи. красочный и красно оставлены неразмеченными.',
        'BEAUTY: краса, красивый, красота, украсить; RED: красный, краска, красить; UNRESOLVED: красочный, красно',
    ),
    'слав¹|слов¹|слы': (
        'SPLIT',
        'Слава, слово, условие и сословие разошлись. словно оставлено.',
        'GLORY: слава, славить; WORD: слово, словарь; CONDITION: условие, условный; ESTATE: сословие; UNRESOLVED: словно',
    ),
    'плот²|площ²': (
        'SPLIT',
        'Плоть и плотность — разные современные семьи.',
        'FLESH: плоть, плотский, воплотить; DENSE: плотный, плотность, вплоть',
    ),
    'друг|друж¹': (
        'SPLIT',
        'Друг не открывает другой и вдруг.',
        'FRIEND: друг, дружба, дружить; OTHER: другой; SUDDENLY: вдруг',
    ),
    'лебед|лебяж': (
        'SPLIT',
        'Лебеда — растение, лебедь — птица.',
        'ORACH: лебеда; SWAN: лебедь, лебединый, лебедка',
    ),
    'черв': (
        'SPLIT',
        'Червь не открывает карточную масть и червлёный цвет.',
        'WORM: червь, червяк; HEARTS: червовый, червы; CRIMSON: червленый',
    ),
    'колаш|колос|колош¹': (
        'SPLIT',
        'Колос растения не открывает колосник решётки.',
        'GRAIN: колос, колоситься; GRATE: колосник',
    ),
    'сыр': (
        'SPLIT',
        'Сырник не открывает сырость. Поверхность «сыр» всё ещё открывает сырой: это краткая форма прилагательного.',
        'CHEESE: сыр, сырок, сырник; DAMP: сырой, сырость, сыреть',
    ),
    'ворон¹': (
        'SPLIT',
        'Птица, воронение стали и проворонить разделены.',
        'BIRD: ворон, ворона; BLUING: воронение, воронить; OVERLOOK: проворонить',
    ),
    'мар³': (
        'MULTIPLE_SEMANTIC_READINGS',
        'Марь хранит два альтернативных корня, дымку и растение, а не один составной набор.',
        'HAZE: марево, маревый, марь; PLANT: марь; SPECTER: мара',
    ),
    'пек|печ|пещ': (
        'SPLIT',
        'Печь, печаль, опека, пещера и печень разделены. печься оставлено при печи.',
        'BAKE: печь, печка, пекарь; SADNESS: печаль; CARE: опека, обеспечить; CAVE: пещера; LIVER: печень',
    ),
    'мал⁴|мел|мол¹': (
        'SPLIT',
        'Мел, мелкий, мель, молоть и моль — разные современные семьи.',
        'CHALK: мел, мелок; SMALL: мелкий, мелочь; SHOAL: мель; MILL: молоть, мельник; MOTH: моль',
    ),
    'раж¹|раз¹': (
        'SPLIT',
        'Образ, разить и выражать — разные современные семьи. Отражать имеет оба чтения: удар и отражение.',
        'IMAGE: образ, образование; STRIKE: разить, поражать; EXPRESS: выражать; REFLECT: отражатель; OBJECT: возражать; UGLY: безобразный; WIT: соображать; TILE: изразец',
    ),
    'лик¹|лиц|лич': (
        'SPLIT',
        'Личинка, отличие и отличный вынесены. Лицо, облик и улика оставлены одним кластером «лика».',
        'FACE: лицо, лик, облик; LARVA: личинка; DIFFERENCE: отличие, различный; EXCELLENT: отличный',
    ),
    'бер|бир|бор¹|бр¹': (
        'SPLIT',
        'Брак и брань вынесены из брать.',
        'TAKE: брать, выбрать; MARRIAGE: брак, брачный; ABUSE: брань',
    ),
    'мест¹|мещ¹': (
        'SPLIT',
        'Мещанин вынесен. Вместе и вместить остаются одной семьёй места.',
        'PLACE: место, вместе, вместить; BOURGEOIS: мещанин',
    ),
    'род|рож²|рожд': (
        'SPLIT',
        'Родник вынесен. Род, родить и народ остаются одной семьёй.',
        'KIN: род, родить, народ; SPRING: родник',
    ),
    'хаж|ход|хож|хожд': (
        'SPLIT',
        'Необходимый вынесен. Доход и выходить остаются при ходьбе: это приставки.',
        'WALK: ходить, выходить, доход; NECESSITY: необходимый',
    ),
    'ста(j)|сто(j)': (
        'SPLIT',
        'Стоять не открывает остальной, состоять, стать и ставить. Устав, усталость, сустав и достаточный уже были отдельно.',
        'STAND: стоять, простоять; REMAIN: оставить, остальной; CONSIST: состоять, состояние; BECOME: стать, становиться; SET: ставить; SUPPLY: поставить; COMPOSE: состав; +halt/represent/brew/decree/install/…',
    ),
    'сам': (
        'SPLIT',
        'Самец и самка вынесены. Сам и самый остаются.',
        'SELF: сам, самый, самость; MALE: самец, самка',
    ),
    'жал¹': (
        'SPLIT',
        'Жаль и жалеть остаются. Жальник — отдельный курган.',
        'PITY: жаль, жалеть; BARROW: жальник',
    ),
    'так|тат|тач²': (
        'SPLIT',
        'Стачка вынесена. Так и такой остаются одной семьёй.',
        'DEMONSTRATIVE: так, такой; LABOR STRIKE: стачка',
    ),
    'кус²|куш²': (
        'SPLIT',
        'Вкус, искусство и искус разделены.',
        'TASTE: вкус, кушать; ART: искусство, искусный; TEMPT: искус, искусить',
    ),
    'цве|цвес|цвет|цвеч': (
        'SPLIT',
        'Цветок не открывает бесцветный. Лемма цвет оставлена: это и цвет, и цветение.',
        'BLOOM: цветок, цвести; COLOR: бесцветный, цветной; UNRESOLVED: цвет',
    ),
    'важ²|важд|вед²|вес²|вод¹|вож¹|вожд': (
        'SPLIT',
        'Вести не открывает завод, взвод, вождя, сводника, невод, производство и провожать.',
        'CONDUCT: вести, водить, перевод; FACTORY: завод; SQUAD: взвод; CHIEF: вождь; PANDER: сводник; SEINE: невод; PRODUCE: производство; ESCORT: провожать; DIVORCE: развод; +behavior/obsession/institution/argument',
    ),
    'мир¹': (
        'SPLIT',
        'Мирный не открывает мирской и смирный. Леммы мир и мировой хранят оба чтения: покой и свет.',
        'PEACE: мирный, мирить, перемирие; WORLDLY: мирской, мирянин; HUMBLE: смирный, смирить; MULTI: мир, мировой',
    ),
    'прав': (
        'SPLIT',
        'Справка, направление и правительство — разные современные семьи. Править хранит оба чтения: власть и правку.',
        'REFERENCE: справка; DIRECT: направление, отправить; GOVERN: правительство; CORRECT: исправить, правило; TRUTH: правда; LAW: право',
    ),
    'вет¹|веч²|вещ²': (
        'SPLIT',
        'Совет не открывает ответ и ответственность. Привет, завет, вещать и вечный тоже отделены.',
        'ADVICE: совет, советовать, совещание; ANSWER: ответ, ответить; DUTY: ответственность; MATCH: соответствие; GREET: привет; TESTAMENT: завет, завещать; PROCLAIM: вещать; SLANDER: навет; ETERNAL: вечный; VECHE: вече',
    ),
    'вед¹|веж|вежд¹|вест|вещ¹': (
        'SPLIT',
        'Ведать не открывает весть, совесть, ведьму и невесту.',
        'KNOW: ведать, разведка; ADMINISTRATION: ведомость, ведомство, заведовать; ведение/ведомый keep multiple readings; NEWS: весть, известить, повесть; CONSCIENCE: совесть; POLITE: вежливый; WITCH: ведьма, ведун; BRIDE: невеста; CONFESS: исповедь; PREACH: проповедь; COVENANT: заповедь',
    ),
    'сторон|стран': (
        'SPLIT',
        'Сторона не открывает страну, страницу и пространство. Странный уже был вынесен.',
        'SIDE: сторона, устранить; COUNTRY: страна; PAGE: страница; WANDER: странник, странствовать; SPACE: пространство, распространить; STRANGE: странный',
    ),
    'част¹|чащ¹': (
        'SPLIT',
        'Часть не открывает счастье, участок и участие.',
        'PART: часть, частный; PLOT: участок; SHARE: участие, причастие; FATE: участь; LUCK: счастье',
    ),
    'ч³|чес²|чет|чит|чт': (
        'SPLIT',
        'Подсчёт, чтение и почёт/уважение разведены; считать, вычитать и почитать сохраняют альтернативные чтения.',
        'COUNT: считать (число), счёт, расчёт, вычитание; READ: читать, чтение; HONOR: почёт, честь, чтить; MULTI: считать, вычитать, вычитывать, почитать',
    ),
    'слад|слажд|сласт|слащ|солаж|солод|солож|солощ': (
        'SPLIT',
        'Сладкий не открывает солод.',
        'SWEET: сладкий, наслаждение; MALT: солод, солодить',
    ),
    'трав': (
        'SPLIT',
        'Трава не открывает отраву и травить.',
        'GRASS: трава, травяной; POISON: отрава, травить, травля',
    ),
    'клюк¹|ключ¹|клюш': (
        'SPLIT',
        'Ключ не открывает включать и приключение.',
        'KEY: ключ, ключица, клюшка; INCLUDE: включать, исключить; ADVENTURE: приключение',
    ),
    'ключ²': (
        'SPLIT',
        'Ключ-источник отделён от замочного ключа; ключевой и ключик оставлены только в замочной семье.',
        'LOCK KEY: ключ, ключик, ключевой; SPRING: ключ',
    ),
    'гораж|город|горож|град¹|гражд': (
        'SPLIT',
        'Город не открывает гражданина, огород, ограду, награду и град.',
        'CITY: город; CITIZEN: гражданин; GARDEN: огород; FENCE: ограда; REWARD: награда; HAIL: град',
    ),
    'ряд|ряж¹': (
        'SPLIT',
        'Ряд не открывает наряд, подряд как договор, порядок, снаряд, заряд и обряд.',
        'SERIES: ряд, подряд (also contract reading); CONTRACT: подрядчик, подрядиться; OUTFIT: наряд, ряженый; ORDER: порядок, распорядиться; GEAR: снаряд; CHARGE: заряд; RITE: обряд; DETACHMENT: отряд',
    ),
    'каж¹|кажд¹|каз¹': (
        'SPLIT',
        'Сказать не открывает указать, доказать, отказать и наказать.',
        'TELL: сказать; ORDER_CMD: указать, приказ; PROVE: доказать; REFUSE: отказать; PENALTY: наказать; SHOW: показать; EACH: каждый',
    ),
    'де(j)|де': (
        'SPLIT',
        'Дело/делать, действие/действовать и девать/вдевать — разные современные значения; одевание выделено отдельно.',
        'DEED: дело, делать; ACTION: действие, действовать; PUT/INSERT: девать, вдевать; DRESS: одеть, надеть, раздеть',
    ),
    'мат¹|мач¹': (
        'SPLIT',
        'Материнская и матерная лексика связаны; наматывать и обмачивать относятся к отдельным значениям.',
        'MOTHER: мать, материнский, мачеха; PROFANITY: материть, матерный; WIND/SOAK: наматывать, обмачивать',
    ),
    'суд¹|суж|сужд': (
        'SPLIT',
        'Суд не открывает ссуду и рассудок.',
        'COURT: суд, судить; LOAN: ссуда; REASON: рассудок',
    ),
    'кла|клад|клаж|клас': (
        'SPLIT',
        'Класть / вклад отделены от склада и клада.',
        'LAY: класть, вклад; STORE: склад; TREASURE: клад',
    ),
    'сад|саж|сажд|сед¹|сед|сес|сид|сиж¹|сяд': (
        'SPLIT',
        'Сад не открывает сидеть, седло, осаду и досаду.',
        'ORCHARD: сад, сажать; SIT: сидеть; SADDLE: седло; SIEGE: осада; ANNOY: досада',
    ),
    'дер|дерг|держ|дир|дор|дорог³|др': (
        'SPLIT',
        'Разведены держание, рывок, раздирание, дорога/дороговизна, вздор и судорога.',
        'HOLD: держать; YANK: дёргать; TEAR: драть; ROAD/VALUE: дорога, дорогой; NONSENSE: вздор; CRAMP: судорога',
    ),
    'зна(j)': (
        'SPLIT',
        'Глагол знать и знать как сословие разделены; знать и знатный сохраняют оба чтения.',
        'COGNIZE: знать (глаг.), знание; NOBILITY: знать (сущ.), знатность; SIGN: знак, знамя; MEANING: значение; FAME: знаменитый',
    ),
    'вер²|верет²|верт|верч': (
        'SPLIT',
        'Вертеть не открывает вернуть.',
        'TWIRL: вертеть; RETURN: вернуть; SPINDLE: веретено',
    ),
    'тя|тяг|тяж|тяз': (
        'SPLIT',
        'Тяга не открывает тяжёлый и тяжбу.',
        'PULL: тяга, тянуть; HEAVY: тяжёлый; LAWSUIT: тяжба',
    ),
    'мар²|мер²|мер|мир²|мор¹': (
        'SPLIT',
        'Умирать не открывает морить и замирать. Уморительный и морилка тоже отдельно.',
        'DIE: умирать, смерть, мертвый; PLAGUE: мор, морить, вымаривать; STILL: замирать, замереть; FUNNY: уморительный; STAIN: морилка',
    ),
    'кат¹|кач¹': (
        'SPLIT',
        'Катить не открывает качать.',
        'ROLL: катить, катать, прокат; ROCK: качать, качели',
    ),
    'тач¹|тек|теч|ток¹|точ¹': (
        'SPLIT',
        'Течь, швейная тачка/втачка и механическая обработка/заточка разделены; неоднозначные глаголы сохраняют несколько чтений.',
        'FLOW: течь, ток, источник; SHARPEN/MACHINING: точить, растачивать, токарь; EAST: восток; STITCH: тачать, втачивать; IMPRISON: заточение; SQUANDER: расточительный; LEK: токовать; DAILY: суточный',
    ),
    'рв|ров¹|ры': (
        'SPLIT',
        'Рвать / взрыв не открывают рыть и ров.',
        'RIP: рвать, взрыв; DIG: рыть, ров, рыло',
    ),
    'руб': (
        'SPLIT',
        'Рубить не открывает рубль, рубашку, рубеж и рубец.',
        'CHOP: рубить; RUBLE: рубль; SHIRT: рубашка; BORDER: рубеж; SCAR: рубец; PLANE: рубанок; RAG: рубище; BRAN: отруби',
    ),
    'влач|влек|влеч|волак|волок|волоч': (
        'SPLIT',
        'Влечь не открывает волокно, проволоку и волочь.',
        'ATTRACT: влечь, увлечение; DRAG: волочь, наволочка; FIBER: волокно; WIRE: проволока',
    ),
    'бог|бож': (
        'SPLIT',
        'Бог не открывает богатый и убогий.',
        'GOD: бог, божественный; RICH: богатый, богатство; WRETCHED: убогий',
    ),
    'бед|бежд': (
        'SPLIT',
        'Бедный не открывает победу и убеждать.',
        'MISERY: беда, бедный; VICTORY: победа; PERSUADE: убеждать',
    ),
    'сл²|стел|стил|стл|стол': (
        'SPLIT',
        'Стол не открывает постель и сланец.',
        'TABLE: стол, столица, столяр; SPREAD: стелить, постель; SLATE: сланец',
    ),
    'зар|зер|зир|зор|зр': (
        'SPLIT',
        'Зреть не открывает зеркало, зарю, надзор, позор и подозрение.',
        'SEE: зреть, зрение; MIRROR: зеркало; DAWN: заря; SUPERVISE: надзор; SUSPECT: подозрение; SHAME: позор; MISCHIEF: озорник; PATTERN: узор; PUPIL: зрачок; DESPISE: презирать; GHOST: призрак; TRANSPARENT: прозрачный; SHELTER: призрение; GAP: зазор',
    ),
    'мерз|мораж|морож|мороз|мраз': (
        'SPLIT',
        'Мороз не открывает мерзкий.',
        'FROST: мороз, мерзнуть; VILE: мерзкий, мерзость, омерзительный',
    ),
    'вес¹|веш¹': (
        'SPLIT',
        'Вес не открывает вешать и занавес.',
        'WEIGHT: вес, весить, весы; HANG: вешать, занавес, навес',
    ),
    'ле(j)|ли|ло(j)|ль²': (
        'SPLIT',
        'Лить не открывает влияние.',
        'POUR: лить, заливать; INFLUENCE: влияние, влиять',
    ),
    'рас|ращ|рос²|рощ': (
        'SPLIT',
        'Расти не открывает рощу.',
        'GROW: расти, рост, растение; GROVE: роща',
    ),
    'вяж|вяз²': (
        'SPLIT',
        'Вязать не открывает вязкий.',
        'TIE: вязать, связь; VISCOUS: вязкий, вязнуть',
    ),
    'глас|глаш|голос': (
        'SPLIT',
        'Голос не открывает гласный, согласие и пригласить.',
        'VOICE: голос; PUBLIC_VOICE: гласный, гласность; AGREE: согласие; INVITE: пригласить; ANNOUNCE: огласить, возглас',
    ),
    'ступ¹': (
        'SPLIT',
        'Ступить не открывает преступление и проступок.',
        'STEP: ступать, ступень, поступать; CRIME: преступление, проступок',
    ),
    # --- backlog / agent triage ---
    'баб': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (бабища/обабок, бабуся/обабок, бабкин/обабок)',
        'BOLETUS / BUTTERFLY / WOMAN',
    ),
    'блес|блеск|блест|блист': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (блеск/взблеск, блеск/отблеск, блесна/взблеск))',
        'ONE_FAMILY',
    ),
    'бр²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (брить/обрить, брить/сбрить, брить/забрить))',
        'ONE_FAMILY',
    ),
    'буд¹|бужд': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (будить/побудка))',
        'ONE_FAMILY',
    ),
    'веред|вред|вреж|врежд': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (вред/веред, веред/вредный, веред/вредить))',
        'ONE_FAMILY',
    ),
    'вир¹|вр': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (врун/врать, врун/враки, врун/враль))',
        'ONE_FAMILY',
    ),
    'вис': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (провис/висеть, провис/височек, провис/свислый)',
        'ONE_FAMILY',
    ),
    'во(j)²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (воин/война, воин/вояка, воин/войско))',
        'ONE_FAMILY',
    ),
    'вя|вяд': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (вялый/увялый, вялый/вянуть, вялый/увядать))',
        'ONE_FAMILY',
    ),
    'гал|гол': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (голыш/оголить, голыш/догола, голыш/оголеть))',
        'ONE_FAMILY',
    ),
    'глох|глух|глуш': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (глушь/наглухо, глушь/вглухую, глухой/наглухо))',
        'ONE_FAMILY',
    ),
    'грем|гром': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (гром/погром, гром/разгром, гром/греметь))',
        'ONE_FAMILY',
    ),
    'дабр|добр': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (добро/подобру, добром/подобру, добряк/подобру)',
        'ONE_FAMILY',
    ),
    'дал': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (даль/вдаль, даль/вдали, даль/одаль))',
        'ONE_FAMILY',
    ),
    'далб|долб': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (долбеж/надолба, долбня/надолба, долбить/надол)',
        'ONE_FAMILY',
    ),
    'ден|дн¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (поденка/дневка, поденка/денной, поденка/денни)',
        'ONE_FAMILY',
    ),
    'доб': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (сдоба/удобно, сдоба/удобный, сдоба/подобие)',
        'CONVENIENT / DOUGH_ADD / SIMILAR',
    ),
    'дох|дош': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (вдох/вздох, вдох/выдох, вдох/дохлый)',
        'BREATH / DEAD_OF',
    ),
    'жв|жев': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (жевок/жвачка, жевок/сжевать, жевок/жвачный))',
        'ONE_FAMILY',
    ),
    'жир²|жор|жр¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (жор/зажор, жор/нажор, жор/жрать))',
        'ONE_FAMILY',
    ),
    'зд|зид|зижд|зод': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (здание/зодчий, здание/создать, зодчий/создать)',
        'ONE_FAMILY',
    ),
    'и¹|ид|й|йд|ыд': (
        'SPLIT',
        'Современные значения движения, нахождения и события разделены; этимологически или семантически неясные формы оставлены на исходном семействе.',
        'WALKING: идти и приставочные формы; FINDING: найти, найтись, найденыш; HAPPENING: происходить, произойти; UNRESOLVED: наитие, непревзойденный, предыдущий, пройдоха, пройдошничать, пройдошный, соитие',
    ),
    'им²|ым¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (имя/имечко, имя/именины, имя/именной))',
        'ONE_FAMILY',
    ),
    'кап³|коп²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (скоп/копна, скоп/копить, скоп/копнить))',
        'ONE_FAMILY',
    ),
    'кар²|кор²': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (кара/укор, кара/корить, кара/укорять)',
        'PUNISH_KAR / REPROACH / SUBMIT',
    ),
    'клик|клиц|клич': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (клик/оклик, клик/отклик, клик/выклик))',
        'ONE_FAMILY',
    ),
    'крап|кроп¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (крап/кропить, крап/кропило, крапать/кропить))',
        'ONE_FAMILY',
    ),
    'крив': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (кривой/вкривь, кривой/накриво, вкривь/кривун))',
        'ONE_FAMILY',
    ),
    'крик|крич': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (крик/окрик, крик/выкрик, крик/покрик))',
        'ONE_FAMILY',
    ),
    'ку²|куп²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (купель/окунуть, купель/окунать, купать/окунут)',
        'ONE_FAMILY',
    ),
    'лес¹|леш': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (лешак/лесок, лешак/лесочек, лешак/лесовик))',
        'ONE_FAMILY',
    ),
    'лест|лещ²|льст|льщ': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (лесть/льстец, лесть/улещать, лесть/льстить))',
        'ONE_FAMILY',
    ),
    'лип|ль¹': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (липа/льнуть, липа/влипать, липа/слипать)',
        'LINDEN / STICK_TO',
    ),
    'мар¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (смарка/марать, смарка/маркий, смарка/маранье))',
        'ONE_FAMILY',
    ),
    'мг|ми|миг': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (мигом/минуть, мигач/минуть, мигать/минуть)',
        'BLINK / PASS_BY',
    ),
    'мил': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (милка/умилять, милка/умилить, милый/умилять))',
        'ONE_FAMILY',
    ),
    'муж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (мужлан/замужем, мужлан/замуж, мужичий/замужем)',
        'ONE_FAMILY',
    ),
    'ник|ниц|нич|нк|ноч¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (никлый/вникать, никлый/изнанка, никлый/сникат)',
        'ONE_FAMILY',
    ),
    'ок|оч': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (оконщик/заочник, оконщик/окно, оконщик/очки)',
        'EYE / WINDOW',
    ),
    'перед|переж|пред|преж|прежд': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (предок/вперед, предок/впредь, предок/передок)',
        'ANCESTOR / BEFORE_TIME / FORWARD / WARN',
    ),
    'пол⁵': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (пол/исполу, пол/пополам, пола/исполу)',
        'GENDER_SEX / HALF',
    ),
    'пре': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (прель/упреть, прель/сопреть, прель/напреть))',
        'ONE_FAMILY',
    ),
    'пря¹|пряд¹|пряж¹|пряс': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (пряха/спрясть, пряха/впрясть, пряжа/спрясть))',
        'ONE_FAMILY',
    ),
    'пыт': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (опыт/пытка, опыт/пытать, опыт/попытка)',
        'EXPERIENCE / TORTURE',
    ),
    'руж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (ружье/оружие, оружие/ружьецо))',
        'ONE_FAMILY',
    ),
    'серд|серж|серч': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (сердце/усердие, сердяга/усердие, сердить/усердие)',
        'ANGER_HEART / HEART / ZEAL',
    ),
    'серебр|сребр': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (сребро/серебро))',
        'ONE_FAMILY',
    ),
    'снов': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (основа/сновка, основа/сновать)',
        'FOUNDATION / WEAVE_WARP',
    ),
    'сыт¹|сыщ': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (сытый/досыта, сытый/вдосыть, досыта/сытный))',
        'ONE_FAMILY',
    ),
    'та(j)¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (затаить/тайный, затаить/тайком, затаить/утайк)',
        'ONE_FAMILY',
    ),
    'твор²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (затвор/притвор, затвор/створка, затвор/раство)',
        'ONE_FAMILY',
    ),
    'тих|тиш': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (втихую/тихий, втихую/затишек, втихую/утихать))',
        'ONE_FAMILY',
    ),
    'трат|трач': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (трата/утрата, трата/затрата, утрата/затрата))',
        'ONE_FAMILY',
    ),
    'туп': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (тупой/отупеть, тупик/отупеть, тупец/отупеть)',
        'BLUNT / DEADEND',
    ),
    'тух|туш²|тх': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (тушить/тухлый, тушить/стушить, тушить/тухнуть)',
        'EXTINGUISH / ROTTEN',
    ),
    'хит|хич|хищ': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (хищный/ухитить, хищник/ухитить, ухитить/хищни)',
        'ONE_FAMILY',
    ),
    'хлеб²|хлеб': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (хлебок/взахлеб, хлебать/взахлеб, взахлеб/хлеб)',
        'ONE_FAMILY',
    ),
    'худ|хуж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (худо/хужеть, худой/хужеть, худеть/хужеть))',
        'ONE_FAMILY',
    ),
    'чал¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (чал/счал, чал/отчал, чал/причал))',
        'ONE_FAMILY',
    ),
    'чуд¹': (
        'SPLIT',
        'auto≥30: разностеблевые ROOT-пары (чудо/причуда, чудом/причуда, чудак/причуда)',
        'WHIM / WONDER',
    ),
    'шут|шуч': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (шут/сшутить, шутя/сшутить, шутка/сшутить))',
        'ONE_FAMILY',
    ),
    'щуп': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto≥30: разностеблевые ROOT-пары (щуп/ощупь, щуп/ощупью, щуп/ощупать))',
        'ONE_FAMILY',
    ),
    'бадр|бодр': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 32 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'брыз|брызг': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 38 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'вен': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 30 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'глат|глот|глощ': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 33 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'гряз': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 34 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'дом': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 36 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'дроб': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 32 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'кашел|кашл': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 32 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'команд': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 31 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'корен|кореш|корн¹': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 37 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'крахмал': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 31 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'кров¹': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 36 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'мал³': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 38 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'плак|плач²': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 35 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'пласт': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 37 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'плюс|плюск|плющ²': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 35 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'поражн|порожн|пражн|праздн': (
        'AGENT_THINKS_SAFE',
        'auto≥30: мало коротких лемм (n=39); одной семьи достаточно',
        '',
    ),
    'реш': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 37 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'руг': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 38 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'румян': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 31 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'сахар': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 34 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'сверл': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 32 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'свят|свяч|свящ': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 34 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'слаб': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 34 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'снаст|снащ': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 31 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'стекл|стекол': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 33 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'страд|страст¹': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 38 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'стыд|стыж': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 31 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'тверез|трезв': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 30 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'торг²|торж²': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 31 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'трес|треск¹|трещ': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 37 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'утюг|утюж': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 34 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'чекан': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 36 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'шеп': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 35 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'шлиф': (
        'AGENT_THINKS_SAFE',
        'auto≥30: на 32 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'берег¹|береж¹|береч|брег|бреж²|бреч': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (беречь/оберег))',
        'ONE_FAMILY',
    ),
    'бред|брес|брод|брож': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (бред/брод, бред/сброд, бред/вброд)',
        'DELIRIUM / FORD',
    ),
    'вастр|востр|остр|ощр': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (востро/острие, востро/острог, востро/острец)',
        'PRISON / SHARP_EDGE',
    ),
    'выс|выш': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (высь/вышка, высь/ввысь, высь/вышина))',
        'ONE_FAMILY',
    ),
    'гад¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (наугад/гадать))',
        'ONE_FAMILY',
    ),
    'глад²|глод|голод': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (глад/голод))',
        'ONE_FAMILY',
    ),
    'гна|гни|гно(j)': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (гной/гниль, гной/гнилец, гной/гнилье))',
        'ONE_FAMILY',
    ),
    'год|гож|гожд': (
        'SPLIT',
        'Время, пригодность, погода, выгода и угождение разведены по современным значениям.',
        'YEAR: год, годовой; SUITABILITY: годный, пригодный; WEATHER: погода; BENEFIT: выгода; PLEASE: угодить',
    ),
    'да²|до(j)': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (дойный/доить, дойный/надой, дойный/доярка))',
        'ONE_FAMILY',
    ),
    'дв': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (два/двое, два/вдвое, два/двоица))',
        'ONE_FAMILY',
    ),
    'дуб': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (дуб/падуб, дубка/падуб, падуб/дубье))',
        'ONE_FAMILY',
    ),
    'дых|дыш': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (отдых/роздых, отдых/дышать, отдых/одышка))',
        'ONE_FAMILY',
    ),
    'жа²|жин|жн': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (дожать/ужин, дожать/жнивье, дожать/нажать))',
        'ONE_FAMILY',
    ),
    'зл': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (зло/назло, зло/злюка, зло/злить))',
        'ONE_FAMILY',
    ),
    'злат|злащ|золач|золот|золоч': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (злато/золото, златой/золото, золото/златка))',
        'ONE_FAMILY',
    ),
    'ка²|кап¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (капля/кануть, капель/кануть, кануть/капать))',
        'ONE_FAMILY',
    ),
    'каш³|кос²|кош³': (
        'SPLIT',
        'Косой/косить в значении направления отделены от косы-полуострова и омонимичных корней; косуля и косинка выделены отдельно.',
        'OBLIQUE: косой, вкось, перекос; LAND SPIT: коса (multi); HEADSCARF: косинка; ROE DEER: косуля; MULTI: коса, косить, скосить',
    ),
    'кащ|кост|кощ': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (кость/кощей, кощей/костяк))',
        'ONE_FAMILY',
    ),
    'кип¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (кипеть/накипь, кипень/накипь))',
        'ONE_FAMILY',
    ),
    'клан|клон': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (уклон/склон, уклон/наклон, уклон/поклон))',
        'ONE_FAMILY',
    ),
    'клев|клю|клюв': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (клюв/клев, клюв/наклев, клюв/клевок))',
        'ONE_FAMILY',
    ),
    'кол²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (колея/околыш, колесо/околыш, кольцо/околыш))',
        'ONE_FAMILY',
    ),
    'кра¹|кро(j)': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (крой/покрой, крой/закрой, покрой/закрой))',
        'ONE_FAMILY',
    ),
    'лед²|лед|льд': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (лед/наледь, лед/льдина, ледок/наледь))',
        'ONE_FAMILY',
    ),
    'леп¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (лепка/слепок, лепеха/слепок, лепной/слепок))',
        'ONE_FAMILY',
    ),
    'лиз': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (зализ/лизун, зализ/лизать))',
        'ONE_FAMILY',
    ),
    'мал¹': (
        'SPLIT',
        'Малый и маленький относятся к размеру; мальчик, малыш и малец обозначают ребёнка/юношу и разделены как отдельная современная семья.',
        'SMALL: малый, маленький; BOY/CHILD: мальчик, малыш, малец',
    ),
    'малк|малч|молк|молч': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (умолк/молча, умолк/молчун))',
        'ONE_FAMILY',
    ),
    'мащ²|мост|мощ²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (мост/помост, мост/намост, мостки/помост))',
        'ONE_FAMILY',
    ),
    'ма⁴|ман¹': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (обман/манок, обман/мание, обман/манный)',
        'BECKON / DECEIVE',
    ),
    'мер³|мерек|мереч|мерк|мерц|морач|морок|мороч|мрак|мрач': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (мрак/сумрак, мрак/морока, сумрак/морока))',
        'ONE_FAMILY',
    ),
    'мин¹|мн|мя²': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (помин/мнить, помин/мнение, мнить/мнение)',
        'OPINION / REMEMBER',
    ),
    'мк|мок²|моч³|мык|мыч²': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (замок/мочка, замок/умычка, замок/смычка)',
        'JOIN_LINK / LOCK / WANDER_MYK',
    ),
    'млад|молаж|молод|молож': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (смлада/молодь, смлада/младой, молодь/младой))',
        'ONE_FAMILY',
    ),
    'мог|мож|моч²|мощ¹': (
        'SPLIT',
        'Способность, помощь, вымогательство, домогательство, достаток и мощи разведены по современным значениям.',
        'ABILITY: мочь, мощь; AID: помогать, помощь; EXTORT: вымогать; SEEK: домогаться; WEALTH: незаможный; RELIC: мощи',
    ),
    'сут|сущ': (
        'SPLIT',
        'Сущность, существование и наличие/отсутствие разведены; суть и существо сохраняют оба чтения.',
        'ESSENCE: суть, сущность; EXISTENCE: существовать, существо; PRESENCE: присутствие, отсутствие',
    ),
    'молач|молот|молоч¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (молот/умолот))',
        'ONE_FAMILY',
    ),
    'мук¹|муч¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (мука/мучить))',
        'ONE_FAMILY',
    ),
    'мут|муч³|мущ': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (омут/смута, омут/мутить, омут/мутник)',
        'MUDDY / UNREST',
    ),
    'ниж|низ¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (низкий/вниз, низкий/донизу, низкий/нанизу))',
        'ONE_FAMILY',
    ),
    'нюх|нюш': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (понюх/нюхать))',
        'ONE_FAMILY',
    ),
    'пал¹': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (опала/запал, опала/пальба, опала/палить)',
        'BURN_FIRE / DISGRACE / GUNFIRE',
    ),
    'пар²|пор²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (порка/выпор, порка/опорок, порка/спорок))',
        'ONE_FAMILY',
    ),
    'пеш²|пих|пх': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (пешня/пхать, пешня/пхнуть, пхать/пхнуть))',
        'ONE_FAMILY',
    ),
    'пит|пич¹|пищ²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (пища/питать))',
        'ONE_FAMILY',
    ),
    'плат¹|плач¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (плата/оплата, плата/уплата, платеж/оплата))',
        'ONE_FAMILY',
    ),
    'пока¹|поко(j)': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (покои/упокой, покои/спокой, покой/упокой))',
        'ONE_FAMILY',
    ),
    'пуг|пуж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (испуг/пугач, испуг/пугать, испуг/пужать))',
        'ONE_FAMILY',
    ),
    'пуст²|пущ¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (пуща/пустой, пуща/спуста, пуща/пустяк))',
        'ONE_FAMILY',
    ),
    'пят²|пяч': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (пята/опять, пята/вспять, опять/пятка))',
        'ONE_FAMILY',
    ),
    'рух|руш²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (рухлый/рушить, рухлый/рушать, рухлый/поруха))',
        'ONE_FAMILY',
    ),
    'с|соп²|сп|сып²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (спать/снулый, спать/уснуть, спать/спячка))',
        'ONE_FAMILY',
    ),
    'саб|соб': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (особа/способ, особь/способ, способ/особый)',
        'MEANS_AID / OWN_PROPERTY / PARTICULAR',
    ),
    'сва¹|сво(j)': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (сват/свой, сват/свояк, свой/сваха)',
        'MATCHMAKE / OWN',
    ),
    'син': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (синяк/досиня, синяк/иссиня, синец/досиня))',
        'ONE_FAMILY',
    ),
    'сов²|су': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (совок/засов, совок/сунуть, засов/сунуть))',
        'ONE_FAMILY',
    ),
    'спе': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (спех/успех, спех/наспех, спех/успеть))',
        'ONE_FAMILY',
    ),
    'стерег|стереж|стереч|стораж|сторож|страж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (страж/сторож, стража/сторож))',
        'ONE_FAMILY',
    ),
    'студ|стуж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (стужа/остуда))',
        'ONE_FAMILY',
    ),
    'сук³|суч³': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (сукно/сучило, сукно/сучить)',
        'CLOTH_WOOL / TWIST_YARN',
    ),
    'твар|твор¹': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (тварь/творец, тварь/утварь, творец/утварь)',
        'CREATE / CREATURE / DISSOLVE / PRETENCE / UTENSILS',
    ),
    'то|тон²|топ²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (топь/тоня, топь/потоп, топь/затон))',
        'ONE_FAMILY',
    ),
    'том²': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (томить/истома, томный/истома))',
        'ONE_FAMILY',
    ),
    'тук¹|туч³': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (тук/стук, стук/тукать))',
        'ONE_FAMILY',
    ),
    'ух¹|уш¹': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (ухо/ушан, ухо/ушки, ухо/ушко))',
        'ONE_FAMILY',
    ),
    'хвал': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (хвала/чехвал))',
        'ONE_FAMILY',
    ),
    'хот|хоч': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (охота/похоть, охота/хотеть, охотка/похоть))',
        'ONE_FAMILY',
    ),
    'цед|цеж': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (цеж/цедить))',
        'ONE_FAMILY',
    ),
    'цен': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (цена/оценка, цена/уценка, ценить/оценка))',
        'ONE_FAMILY',
    ),
    'ч¹|чин²': (
        'SPLIT',
        'Начало и власть разделены: почин/начать не открывают начальника/начальство.',
        'BEGINNING: начало, зачин, почин; AUTHORITY: начальник, начальство',
    ),
    'черк': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (очерк/почерк))',
        'ONE_FAMILY',
    ),
    'черн': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (чернь/вчерне, черныш/вчерне, черняк/вчерне))',
        'ONE_FAMILY',
    ),
    'чу': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (чуть/чуять, чуть/учуять, чуть/ничуть))',
        'ONE_FAMILY',
    ),
    'ш|ше²|шед': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (пошляк/дошлый, дошлый/пошлый))',
        'ONE_FAMILY',
    ),
    'шиб': (
        'SPLIT',
        'auto: разностеблевые ROOT-пары (ушиб/пошиб, ушиб/отшиб, ушиб/шибкий)',
        'ERR / HIT_STRIKE',
    ),
    'щеп': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (щепа/прищеп, щепа/расщеп, щепка/прищеп))',
        'ONE_FAMILY',
    ),
    'яв': (
        'AGENT_THINKS_SAFE',
        'auto-TO_SPLIT demoted: odd ROOT pairs look like prefix/same-sense noise (auto: разностеблевые ROOT-пары (явка/наяву, явка/въявь, явка/въяве))',
        'ONE_FAMILY',
    ),
    'бал¹|бол¹': (
        'AGENT_THINKS_SAFE',
        'auto: на 42 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'вел¹': (
        'AGENT_THINKS_SAFE',
        'auto: на 42 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'верг|верж': (
        'AGENT_THINKS_SAFE',
        'auto: на 43 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'верст²|верст': (
        'AGENT_THINKS_SAFE',
        'auto: на 48 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'ветер|ветр': (
        'AGENT_THINKS_SAFE',
        'auto: на 42 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'винт|винч': (
        'AGENT_THINKS_SAFE',
        'auto: на 54 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'влад|власт|волост': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'глад¹|глаж': (
        'AGENT_THINKS_SAFE',
        'auto: на 64 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'гран': (
        'AGENT_THINKS_SAFE',
        'auto: на 53 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'грыж|грыз': (
        'AGENT_THINKS_SAFE',
        'auto: на 61 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'двор': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'един': (
        'AGENT_THINKS_SAFE',
        'auto: на 61 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'желт|желч': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'зван|звен²|звон²': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'здорав|здоров|здрав': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'зел': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'зуб': (
        'AGENT_THINKS_SAFE',
        'auto: на 48 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'капч|копот|копт|копч': (
        'AGENT_THINKS_SAFE',
        'auto: на 47 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'квас|кваш': (
        'AGENT_THINKS_SAFE',
        'auto: на 49 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'ковыр': (
        'AGENT_THINKS_SAFE',
        'auto: на 49 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'корач|корот|короч¹|крат|кращ': (
        'AGENT_THINKS_SAFE',
        'auto: на 46 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'крад|краж|крас²': (
        'AGENT_THINKS_SAFE',
        'auto: на 42 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'крест|крещ': (
        'AGENT_THINKS_SAFE',
        'auto: на 60 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'мал²|мол²': (
        'AGENT_THINKS_SAFE',
        'auto: на 41 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'масл': (
        'AGENT_THINKS_SAFE',
        'auto: на 56 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'меж|межд': (
        'AGENT_THINKS_SAFE',
        'auto: на 64 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'мусл|мусол': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'мяг|мяк': (
        'AGENT_THINKS_SAFE',
        'auto: на 46 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'пле|плев¹|плев|плю': (
        'AGENT_THINKS_SAFE',
        'auto: на 51 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'плес²|плеск': (
        'AGENT_THINKS_SAFE',
        'auto: на 57 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'поласк|полос²|полоск': (
        'AGENT_THINKS_SAFE',
        'auto: на 46 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'прыг|прыж': (
        'AGENT_THINKS_SAFE',
        'auto: на 55 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'прыс|прыск|прыщ': (
        'AGENT_THINKS_SAFE',
        'auto: на 48 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'пут²': (
        'AGENT_THINKS_SAFE',
        'auto: на 44 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'пут¹': (
        'AGENT_THINKS_SAFE',
        'auto: на 61 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'пыл²': (
        'AGENT_THINKS_SAFE',
        'auto: на 44 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'рис²': (
        'AGENT_THINKS_SAFE',
        'auto: на 59 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'сал¹': (
        'AGENT_THINKS_SAFE',
        'auto: на 44 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'свист|свищ': (
        'AGENT_THINKS_SAFE',
        'auto: на 50 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'скабл|скобел|скобл': (
        'AGENT_THINKS_SAFE',
        'auto: на 44 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'скреб|скрес': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'сла|сло(j)': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'смал|смол': (
        'AGENT_THINKS_SAFE',
        'auto: на 50 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'сме¹': (
        'AGENT_THINKS_SAFE',
        'auto: на 53 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'страст²|страх|страш|стращ': (
        'AGENT_THINKS_SAFE',
        'auto: на 59 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'страч|строк|строч': (
        'AGENT_THINKS_SAFE',
        'auto: на 49 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'тверд|тверж|твержд': (
        'AGENT_THINKS_SAFE',
        'auto: на 44 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'тепел|тепл': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'тереб|треб': (
        'AGENT_THINKS_SAFE',
        'auto: на 61 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'тесн': (
        'AGENT_THINKS_SAFE',
        'auto: на 48 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'толк²|толоч²': (
        'AGENT_THINKS_SAFE',
        'auto: на 41 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'торг¹|торж¹': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'труд|труж|тружд': (
        'AGENT_THINKS_SAFE',
        'auto: на 43 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'трус³|трух²|труш': (
        'AGENT_THINKS_SAFE',
        'auto: на 41 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'форм': (
        'AGENT_THINKS_SAFE',
        'auto: на 59 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'хлест': (
        'AGENT_THINKS_SAFE',
        'auto: на 64 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'хоран|хорон|хран': (
        'AGENT_THINKS_SAFE',
        'auto: на 48 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'царап': (
        'AGENT_THINKS_SAFE',
        'auto: на 41 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'черп': (
        'AGENT_THINKS_SAFE',
        'auto: на 49 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'четвер|четыр': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'числ': (
        'AGENT_THINKS_SAFE',
        'auto: на 62 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'швыр': (
        'AGENT_THINKS_SAFE',
        'auto: на 45 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'щелк|щелч': (
        'AGENT_THINKS_SAFE',
        'auto: на 40 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'ясн': (
        'AGENT_THINKS_SAFE',
        'auto: на 48 леммах нет явного межсмыслового ROOT-клаша в дымовой пробе',
        '',
    ),
    'гул¹': (
        'AGENT_THINKS_SAFE',
        'гулять/прогулка — одна семья гуляния; гул/гулять CLOSED',
        '',
    ),
    'каш¹|кос¹|кош¹': (
        'SPLIT',
        'Коса-орудие и кошение отделены от косы-волос и косого направления; коса и косить сохраняют несколько значений.',
        'SCYTHE/CUTTING: коса, косарь, косить; MULTI: коса, косить, скосить',
    ),
    'луп': (
        'SPLIT',
        'weird ROOT: лупить/лупа',
        'MAGNIFIER / PEEL_BEAT',
    ),
    'пух|пуш': (
        'SPLIT',
        'weird ROOT: пух/пушка',
        'FLUFF / FOREST_EDGE',
    ),
    'треп': (
        'SPLIT',
        'weird ROOT: трепать/трепет',
        'THRASH / TREMOR',
    ),
    'хлоп': (
        'SPLIT',
        'weird ROOT: хлопать/хлопок',
        'BOTHER / CLAP / COTTON',
    ),
    'балт|болт¹': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (болтать/болтовня, болт/сболтить)',
        '',
    ),
    'ве': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (веять/веялка, веять/веяние)',
        '',
    ),
    'гре': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (греть/согреть, греть/грелка)',
        '',
    ),
    'дав¹': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (давить/давление, давить/подавить)',
        '',
    ),
    'дур': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (дурной/дурь, дурной/дурак)',
        '',
    ),
    'кал¹': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (калить/калёный, калить/раскалять)',
        '',
    ),
    'кис¹': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (кислый/киснуть, кислый/кислота)',
        '',
    ),
    'клеп': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (клепать/заклёпка, клепать/клепка)',
        '',
    ),
    'лав²|лов': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (ловить/лов, ловить/ловля)',
        '',
    ),
    'лад¹|лаж': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (лад/ладить, лад/ладовый)',
        '',
    ),
    'лаз|лез¹|лес²': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (лезть/лазить, лезть/лазейка)',
        '',
    ),
    'мет²|меч¹': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (метать/метание)',
        '',
    ),
    'палз|полз|полоз': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (ползти/ползучий, ползти/полоз)',
        '',
    ),
    'печат': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (печать/печатать, печать/печатный)',
        '',
    ),
    'тис|тиск|тисоч': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (тискать/тиски, тискать/тиснение)',
        '',
    ),
    'хлад|хлажд|холаж|холо|холод|холож': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (холод/холодный, холод/холодеть)',
        '',
    ),
    'черт¹|черч': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (—)',
        '',
    ),
    'щип': (
        'AGENT_THINKS_SAFE',
        'продуктивная семья; спорные пары CLOSED или одна семантика (щипать/щипцы, щипать/ущипнуть)',
        '',
    ),
    'па(j)³|по(j)²': (
        'AGENT_THINKS_SAFE',
        'паять/пайка — одна семья пайки',
        '',
    ),
    'бав': (
        'SPLIT',
        'добавить ≠ забава',
        'ADD / AMUSE / RID',
    ),
    'вид|вист': (
        'SPLIT',
        'Зрение отделено от зависти и ненависти; слова на вид- и свидетель- остаются в ветке зрения.',
        'SEEING: видеть, видение, увидеть, свидетель; ENVY_HATRED: завидеть, зависть, ненавидеть, ненависть',
    ),
    'дум': (
        'SPLIT',
        'думать ≠ дума (вече/собрание) если есть',
        'ASSEMBLY / THINK',
    ),
    'зв|зов|зыв': (
        'SPLIT',
        'Зов и призыв отделены от называния; звание сохраняет оба чтения.',
        'CALL: звать, зов, призыв; DESIGNATION: название, прозвище; TITLE: звание',
    ),
    'крут|круч¹': (
        'SPLIT',
        'крутить ≠ крутой',
        'STEEP / TWIST',
    ),
    'кус¹|куш¹': (
        'AGENT_THINKS_SAFE',
        'кусать/кусок — одна семья кусания (куш CLOSED)',
        '',
    ),
    'мах¹|маш': (
        'AGENT_THINKS_SAFE',
        'махать/взмах — одна семья махания (машина CLOSED)',
        '',
    ),
    'мин²|мя¹': (
        'AGENT_THINKS_SAFE',
        'мять/смять — одна семья мятия (минута CLOSED)',
        '',
    ),
    'п|пин|пон|пя': (
        'AGENT_THINKS_SAFE',
        'пять/пятый — одна семья пятёрки (распять CLOSED)',
        '',
    ),
    'пах²|паш²': (
        'AGENT_THINKS_SAFE',
        'пахать/пашня — одна семья пахоты (пах CLOSED)',
        '',
    ),
    'пе|по³': (
        'AGENT_THINKS_SAFE',
        'петь/песня — одна семья пения (пенка CLOSED)',
        '',
    ),
    'пряг|пряж²|пряч': (
        'AGENT_THINKS_SAFE',
        'прятать — одна семья прятания (пряжка/пряжа CLOSED)',
        '',
    ),
    'сал²|сол¹': (
        'AGENT_THINKS_SAFE',
        'соль/солить — одна семья соли (солнце CLOSED)',
        '',
    ),
    'сл¹|сол²|сыл': (
        'SPLIT',
        'слать/посол ≠ соль',
        'SEND',
    ),
    'слуг|служ': (
        'AGENT_THINKS_SAFE',
        'служить/слуга/служба — одна семья службы (случай CLOSED)',
        '',
    ),
    'слух|слуш|слых|слыш': (
        'AGENT_THINKS_SAFE',
        'слушать/слышать — одна семья слуха (слыть уже CLOSED)',
        '',
    ),
    'стриг|стриж¹|стрич': (
        'AGENT_THINKS_SAFE',
        'стричь/стрижка — одна семья стрижки (стриж CLOSED)',
        '',
    ),
    'тес': (
        'AGENT_THINKS_SAFE',
        'тесный/теснота — одна семья тесноты (тесать CLOSED от неё)',
        '',
    ),
    'ум': (
        'SPLIT',
        'ум/умный ≠ уметь',
        'CAN / MIND',
    ),
    'гля|гляд': (
        'AGENT_THINKS_SAFE',
        'глядеть/взгляд — одна семья взгляда',
        '',
    ),
    'готав|готов': (
        'AGENT_THINKS_SAFE',
        'готовый/готовить — одна семья готовности',
        '',
    ),
    'груж|груз': (
        'AGENT_THINKS_SAFE',
        'грузить/груз/нагрузка — одна семья груза',
        '',
    ),
    'жар': (
        'AGENT_THINKS_SAFE',
        'жара/жарить/жаркий — одна семья жара',
        '',
    ),
    'кан|кон¹': (
        'SPLIT',
        'Закон отделён от окончания: формы на законч-/доконч-/поконч- принадлежат значению конца.',
        'LAW: закон, узаконить; END: конец, кончить, закончить',
    ),
    'колач|колот|колоч': (
        'AGENT_THINKS_SAFE',
        'колоть/расколоть — одна семья раскалывания',
        '',
    ),
    'пил': (
        'AGENT_THINKS_SAFE',
        'пила/пилить/опилки — одна семья пиления',
        '',
    ),
    'скак|скач|скок|скоч': (
        'AGENT_THINKS_SAFE',
        'скакать/скачок — одна семья скачков',
        '',
    ),
    'стег¹|стег|стеж¹|стяж²': (
        'AGENT_THINKS_SAFE',
        'стегать/стежок — одна семья стежки',
        '',
    ),
    'тап²|топ³': (
        'AGENT_THINKS_SAFE',
        'топить (печь/отопление) — одна семья топки; утопить проверить отдельно',
        '',
    ),
    'бв|бы': (
        'SPLIT',
        'быть ≠ быт ≠ забыть',
        'BE / EVERYDAY / FORGET',
    ),
    'бел': (
        'SPLIT',
        'Белый цвет, белка и белок разведены в отдельные игровые семьи.',
        'WHITE: белый; SQUIRREL: белка, белочка; PROTEIN: белок, белковый',
    ),
    'би(j)|бо(j)¹|бь': (
        'SPLIT',
        'бить ≠ битва',
        'BATTLE / BEAT',
    ),
    'вал': (
        'SPLIT',
        'валить ≠ валик',
        'ROLLER / TOPPLE',
    ),
    'вер¹': (
        'SPLIT',
        'верить ≠ проверить',
        'BELIEVE / VERIFY',
    ),
    'ворач|ворот¹|вороч|врат¹|вращ': (
        'SPLIT',
        'ворот ≠ возврат ≠ вращение',
        'COLLAR / GIVEBACK / ROTATE',
    ),
    'г|гб|ги|гиб': (
        'SPLIT',
        'гнуть ≠ гибнуть',
        'BEND / PERISH',
    ),
    'гар|гор²': (
        'SPLIT',
        'Горение, горечь и горе — отдельные современные значения; горький вкус отделён от огня.',
        'BURN: гореть; BITTER_TASTE: горечь, горький, горчица; GRIEF: горе, огорчить; BITTER_PLANT: горечавка, горчак',
    ),
    'говар|говор': (
        'SPLIT',
        'говорить ≠ уговор',
        'DEAL / SPEAK',
    ),
    'да¹|даж': (
        'SPLIT',
        'дать ≠ дар ≠ задача ≠ продать',
        'GIFT / GIVE / SELL / TASK',
    ),
    'дви|двиг|движ|двиз': (
        'SPLIT',
        'двигать ≠ движимость',
        'CHATTEL / MOVE',
    ),
    'дел': (
        'SPLIT',
        'делить ≠ отдел',
        'DEPARTMENT / DIVIDE',
    ),
    'дух|душ¹': (
        'SPLIT',
        'душа ≠ дух',
        'PERFUME / SOUL / SPIRIT',
    ),
    'е¹|ед|ес²|я³|яд': (
        'SPLIT',
        'есть/еда ≠ яд',
        'EAT / VENOM',
    ),
    'ем|им¹|йм|ним|ня|ым²|я¹|∅': (
        'SPLIT',
        'взять ≠ понять ≠ нанять ≠ ёмкость',
        'CAPACITY / HIRE / TAKE / UNDERSTAND',
    ),
    'жи': (
        'SPLIT',
        'Жизнь, животное и живот (часть тела) разделены; у слова «живот» сохранено отдельное чтение «жизнь».',
        'LIVE: жить, жизнь; ANIMAL: животное, животный; ABDOMEN: живот, животик, животишко',
    ),
    'иск|ищ|ыск|ыщ': (
        'SPLIT',
        'искать ≠ иск',
        'CLAIM_SUIT / SEARCH',
    ),
    'кал²|кол¹': (
        'SPLIT',
        'колоть ≠ кол',
        'STAB / STAKE',
    ),
    'ки¹|кид': (
        'SPLIT',
        'кидать ≠ скидка',
        'DISCOUNT / THROW',
    ),
    'люб': (
        'SPLIT',
        'любить ≠ любоваться',
        'ADMIRE / LOVE',
    ),
    'мач²|мок¹|моч¹': (
        'SPLIT',
        'мокрый ≠ моча',
        'URINE / WET',
    ),
    'ме|мес²|мет¹|мет': (
        'SPLIT',
        'мести ≠ метель',
        'BLIZZARD / SWEEP',
    ),
    'мер¹': (
        'SPLIT',
        'мера ≠ пример ≠ намерение',
        'EXAMPLE / INTENT / MEASURE',
    ),
    'мног|множ': (
        'SPLIT',
        'умножение ≠ множество ≠ много',
        'MANY / MULTIPLY / SET_MATH',
    ),
    'мысел|мысл|мышл': (
        'SPLIT',
        'мысль ≠ промышленность',
        'INDUSTRY / THOUGHT',
    ),
    'наш|нес|нос²|нош': (
        'SPLIT',
        'Перенос, отношение, произнесение, донос, высокомерие, понос и изнашивание выделены в разные смыслы.',
        'CARRY / RELATION / SPEECH / DENUNCIATION / ARROGANCE / DIARRHEA / WEAR / INSULT',
    ),
    'нз|низ²|нож¹|ноз': (
        'SPLIT',
        'низ ≠ нож ≠ заноза',
        'BOTTOM / KNIFE / SPLINTER',
    ),
    'па²|пи|по(j)¹|пь': (
        'SPLIT',
        'пить ≠ поить ≠ пьяный',
        'DRINK / DRUNK / WATER_V',
    ),
    'па¹|пад|паж|пас¹|пащ': (
        'SPLIT',
        'падать ≠ пасти',
        'FALL / GRAZE',
    ),
    'пар³|пер¹|пир|пор³|пр': (
        'SPLIT',
        'переть ≠ спор ≠ упор',
        'DISPUTE / SHOVE / SUPPORT',
    ),
    'пла|плав|плов|плы': (
        'SPLIT',
        'плавать ≠ плавить',
        'MELT / SWIM',
    ),
    'полн': (
        'SPLIT',
        'полный ≠ выполнить',
        'FULFILL / FULL',
    ),
    'праш|прос¹|прош': (
        'SPLIT',
        'просить ≠ вопрос',
        'ASK / QUESTION',
    ),
    'пуск|пуст¹|пущ²': (
        'SPLIT',
        'пустить ≠ отпуск',
        'LEAVE / LET',
    ),
    'раб': (
        'SPLIT',
        'работа ≠ раб',
        'SLAVE / WORK',
    ),
    'реж²|рез': (
        'SPLIT',
        'резать ≠ резкий',
        'CUT / SHARP',
    ),
    'рек²|реч²|риц|рок|роч|рош²': (
        'SPLIT',
        'речь ≠ срок',
        'DEADLINE / SPEECH',
    ),
    'свес|свет|свеч|свещ': (
        'SPLIT',
        'Свет-освещение отделён от света-общества; свеча, рассвет и просвещение выделены отдельно.',
        'LIGHT: свет, светить; SOCIETY: свет, светский; CANDLE: свеча; DAWN: рассвет; ENLIGHTENMENT: просвещение',
    ),
    'сил¹': (
        'SPLIT',
        'сила ≠ насилие',
        'STRENGTH / VIOLENCE',
    ),
    'стра|стро(j)': (
        'SPLIT',
        'Строительство отделено от устройства и организации; настройка и эмоциональное расстройство не смешиваются со строительной веткой.',
        'CONSTRUCT: строить, строительство, постройка; ARRANGE_DEVICE: устроить, устройство, устраивать; MOOD: расстраивать, настроение; TUNING: настроить, настройка',
    ),
    'талк|толк¹|толок|толоч¹|толч': (
        'SPLIT',
        'толкать ≠ толочь/толокно',
        'POUND / PUSH',
    ),
    'тап¹|топ¹': (
        'SPLIT',
        'топить/топь ≠ топать',
        'FLOOD_DROWN / STOMP',
    ),
    'тер¹|тер|тир|тор|тр²': (
        'SPLIT',
        'тереть ≠ стирать',
        'LAUNDER / RUB',
    ),
    'тк|ток²|точ²|тык|тыч': (
        'SPLIT',
        'ткать ≠ точка ≠ тыкать',
        'LOOM_WEAVE / PINPOINT / POKE',
    ),
    'ук|уч': (
        'SPLIT',
        'учить ≠ наука',
        'INSTRUCT / SCIENCE',
    ),
    'хват': (
        'SPLIT',
        'хватать/захват ≠ хватит',
        'GRAB / SUFFICE',
    ),
    'цел²': (
        'SPLIT',
        'целый ≠ исцелить; кросс с цель',
        'GOAL / HEAL / KISS / WHOLE',
    ),
    'цел¹': (
        'SPLIT',
        'цель кроссит целый/исцелить',
        'GOAL',
    ),
    'бег|беж': (
        'AGENT_THINKS_SAFE',
        'бежать/бег/побег — одна семья бега',
        '',
    ),
    'брас|брос|брош': (
        'AGENT_THINKS_SAFE',
        'бросить/бросок — одна семья бросания',
        '',
    ),
    'важ⁴|вез|вож³|воз': (
        'AGENT_THINKS_SAFE',
        'везти/воз — одна семья перевозки',
        '',
    ),
    'вар¹': (
        'AGENT_THINKS_SAFE',
        'варить/повар/отвар — одна семья варки',
        '',
    ),
    'вел²|вл|вол¹': (
        'AGENT_THINKS_SAFE',
        'велеть/воля — одна семья воли (вол/волна CLOSED)',
        '',
    ),
    'ви|во(j)¹|вь': (
        'AGENT_THINKS_SAFE',
        'вить/завивать — одна семья витья',
        '',
    ),
    'вод²|вож²': (
        'AGENT_THINKS_SAFE',
        'вода/водный — одна семья воды',
        '',
    ),
    'ган|гн|гон': (
        'AGENT_THINKS_SAFE',
        'гнать/погоня/гонка — одна семья гона',
        '',
    ),
    'ду': (
        'AGENT_THINKS_SAFE',
        'дуть/надувать — одна семья дуновения',
        '',
    ),
    'езд|езж|ех': (
        'AGENT_THINKS_SAFE',
        'ездить/ехать/проезд — одна транспортная семья',
        '',
    ),
    'жа¹|жим|жм|жом': (
        'AGENT_THINKS_SAFE',
        'жать/сжимать — одна семья сжатия (урожай CLOSED)',
        '',
    ),
    'жг|жеч|жж|жиг|жог': (
        'AGENT_THINKS_SAFE',
        'жечь/ожог/сжигать — одна семья жжения',
        '',
    ),
    'зем': (
        'AGENT_THINKS_SAFE',
        'земля/земельный — одна семья земли',
        '',
    ),
    'игор|игр|ыгр': (
        'AGENT_THINKS_SAFE',
        'играть/игра — одна семья игры',
        '',
    ),
    'кап²|коп¹': (
        'AGENT_THINKS_SAFE',
        'копать/окоп — одна семья копания',
        '',
    ),
    'карм|корм¹': (
        'AGENT_THINKS_SAFE',
        'кормить/корм — одна семья кормления',
        '',
    ),
    'кле(j)': (
        'AGENT_THINKS_SAFE',
        'клеить/клей — одна семья клея',
        '',
    ),
    'ков|ку¹': (
        'AGENT_THINKS_SAFE',
        'ковать/подкова — одна семья ковки',
        '',
    ),
    'креп': (
        'AGENT_THINKS_SAFE',
        'крепкий/крепить/крепость — одна семья крепости',
        '',
    ),
    'кров²|кры': (
        'AGENT_THINKS_SAFE',
        'крыть/крыша/покров — одна семья покрытия',
        '',
    ),
    'круг|круж¹': (
        'SPLIT',
        'Круг и вращение оставлены в связанных современных значениях; кружево и производные вынесены в отдельную семью изделия.',
        'CIRCLE: круг, окружать; ROTATION: кружить; LACE: кружево, кружевной, кружевница',
    ),
    'куп¹': (
        'AGENT_THINKS_SAFE',
        'купить/купля — одна торговая семья',
        '',
    ),
    'кур¹': (
        'AGENT_THINKS_SAFE',
        'курить/закурить — одна семья курения (курорт CLOSED)',
        '',
    ),
    'лам|лом': (
        'AGENT_THINKS_SAFE',
        'ломать/перелом — одна семья ломания',
        '',
    ),
    'лек|леч¹': (
        'AGENT_THINKS_SAFE',
        'лечить/лечение — одна семья лечения',
        '',
    ),
    'лет²|лет': (
        'AGENT_THINKS_SAFE',
        'лететь/полёт — одна семья полёта (лето CLOSED)',
        '',
    ),
    'маз': (
        'AGENT_THINKS_SAFE',
        'мазать/смазка/мазь — одна семья мазания',
        '',
    ),
    'мат²|мот': (
        'AGENT_THINKS_SAFE',
        'мотать/моток — одна семья мотания (мат CLOSED)',
        '',
    ),
    'мен¹': (
        'AGENT_THINKS_SAFE',
        'менять/замена/обмен — одна семья обмена',
        '',
    ),
    'мес¹|мех²|меш¹': (
        'AGENT_THINKS_SAFE',
        'мешать/смесь — семья смешивания; мешок уже не открывается',
        '',
    ),
    'мо(j)|мов|мы': (
        'AGENT_THINKS_SAFE',
        'мыть/умывать/мыло — одна семья мытья',
        '',
    ),
    'нов': (
        'AGENT_THINKS_SAFE',
        'новый/обновить/новость — одна семья новизны',
        '',
    ),
    'пар¹': (
        'AGENT_THINKS_SAFE',
        'пар/паровой/парить — одна семья пара',
        '',
    ),
    'пис|пиш': (
        'SPLIT',
        'Писать текст и писать в разговорном значении мочиться — разные современные чтения.',
        'WRITE: писать, письмо; URINATE: писать, писаться',
    ),
    'прям': (
        'AGENT_THINKS_SAFE',
        'прямой/выпрямить — одна семья прямизны',
        '',
    ),
    'сас|сос': (
        'AGENT_THINKS_SAFE',
        'сосать/насос — одна семья сосания',
        '',
    ),
    'се': (
        'AGENT_THINKS_SAFE',
        'сеять/посев — одна семья сева',
        '',
    ),
    'сек|сеч': (
        'AGENT_THINKS_SAFE',
        'сечь/сечение — одна семья сечения (секунда CLOSED)',
        '',
    ),
    'сел': (
        'AGENT_THINKS_SAFE',
        'село/селить/поселение — одна семья поселения',
        '',
    ),
    'сматр|смотр': (
        'AGENT_THINKS_SAFE',
        'смотреть/рассмотрение — одна семья зрения',
        '',
    ),
    'соп³|сып¹': (
        'AGENT_THINKS_SAFE',
        'сыпать/засыпать — одна семья сыпания',
        '',
    ),
    'сох²|сух|суш|сых': (
        'AGENT_THINKS_SAFE',
        'сухой/сушить/засуха — одна семья сухости',
        '',
    ),
    'стар': (
        'AGENT_THINKS_SAFE',
        'старый/старость — одна семья старости',
        '',
    ),
    'страг|строг¹|строж|струг¹|струж¹': (
        'AGENT_THINKS_SAFE',
        'строгий и строгать уже не открывают друг друга; оставляем до отдельного ревью',
        '',
    ),
    'стрел': (
        'AGENT_THINKS_SAFE',
        'стрела/стрелять/выстрел — одна семья стрельбы',
        '',
    ),
    'тем²|тем|тм|тьм': (
        'AGENT_THINKS_SAFE',
        'тёмный/темнота — одна семья темноты',
        '',
    ),
    'таск|тасч|тащ': (
        'AGENT_THINKS_SAFE',
        'тащить/вытаскивать — одна семья таскания',
        '',
    ),
    'тряс|трях': (
        'AGENT_THINKS_SAFE',
        'трясти/тряска — одна семья тряски',
        '',
    ),
    'цеп': (
        'AGENT_THINKS_SAFE',
        'цепь/цеплять/сцепление — одна семья сцепки',
        '',
    ),
    'чес¹|чес|чеш': (
        'AGENT_THINKS_SAFE',
        'чесать/расчёска — одна семья чесания (честный CLOSED)',
        '',
    ),
    'чин¹': (
        'SPLIT',
        'Ремонт, причина, сочинение, подчинение, судебное вчинение, чиновничий порядок, бесчинство и начинка разведены.',
        'REPAIR: починка; CAUSE: причина; COMPOSE: сочинение; SUBORDINATION: подчинение; FILING: вчинять; DISORDER: бесчинство; STUFF: начинка',
    ),
    'чист|чищ': (
        'AGENT_THINKS_SAFE',
        'чистый/чистить/чистота — одна семья чистоты',
        '',
    ),
    'шв|шев|ши|шов': (
        'AGENT_THINKS_SAFE',
        'шить/шов/вышивка — одна семья шитья',
        '',
    ),
    'общ': (
        'SPLIT',
        'Общий не открывает общество, общаться и приобщить.',
        'COMMON: общий, обобщить; SOCIETY: общество, община; COMMUNE: общаться, сообщить; AFFILIATE: приобщить; SOCIABLE: общительный',
    ),
    'один|одн': (
        'SPLIT',
        'Один не открывает одинаковый, одинокий и однако.',
        'ONE: один, однажды; SAME: одинаковый; ALONE: одинокий; HOWEVER: однако',
    ),
    'серед|сред': (
        'SPLIT',
        'Средний не открывает средство, посредника и посредственный.',
        'MIDDLE: средний, середина, среда; MEANS: средство; MEDIATOR: посредник; MEDIOCRE: посредственный',
    ),
    'дл|дол²': (
        'SPLIT',
        'Длина не открывает долину, подлинный, подол и долой.',
        'LONG: длина, длиться; VALLEY: дол, долина; AUTHENTIC: подлинный; HEM: подол; DOWNWARD: долой',
    ),
    'удар': (
        'SPLIT',
        'Удар не открывает ударение.',
        'HIT: удар, ударить; STRESS: ударение, безударный',
    ),
    'рав|ров²': (
        'SPLIT',
        'Равный не открывает ровный, равнину и ровесника.',
        'EQUAL: равный, уровень, уравнение; EVEN: ровный; PLAIN: равнина; PEER: ровесник',
    ),
    'верх|верш¹': (
        'SPLIT',
        'Верх не открывает совершить и завершить.',
        'TOP: верх, вершина; ACCOMPLISH: совершить, совершенство, завершить',
    ),
    'гад²|гаж': (
        'SPLIT',
        'Гад / гадюка не открывают гадкий и гадость.',
        'REPTILE: гад, гадюка; NASTY: гадкий, гадость, гадить',
    ),
    'тр¹': (
        'SPLIT',
        'Три не открывает строить (двойной корень стро/тр убран в construct).',
        'THREE: три, третий, тройка; CONSTRUCT: строить, страивать',
    ),
    'праст|прост|прощ': (
        'SPLIT',
        'Простой, простить, простыня и опростать — разные современные семьи. Это главный источник fan-out.',
        'SIMPLE: простой, упростить; FORGIVE: простить, прощение; SHEET: простыня; EMPTY: опростать',
    ),
    'лаг|лег²|леж|леч²|лог|лож¹': (
        'SPLIT',
        'Полог и пологость вынесены как леммы. Поверхность «полог» всё ещё открывает пологость: это краткая форма пологий. Лежать и вложить остаются одной семьёй.',
        'LAY: лежать, вложить, полагать; CANOPY: полог; SLOPE: пологий, пологость',
    ),
    'рук|руч¹|руш¹': (
        'SPLIT',
        'Обруч вынесен. Рука, ручка и обручение остаются семьёй руки.',
        'HAND: рука, ручка, обручение; HOOP: обруч',
    ),
    'след|слеж': (
        'SPLIT',
        'Следить не открывает следствие, наследство, преследовать и последний.',
        'TRACK: след, следить; INVESTIGATE: следствие, исследовать; FOLLOW: следовать; INHERIT: наследство; PURSUE: преследовать; LATTER: последний; PLACENTA: послед',
    ),
    'пас²': (
        'SPLIT',
        'Запас, опасность, пастух и спасти разделены.',
        'STOCK: запас; DANGER: опасность; HERD: пасти, пастух; SAVE: спасти, спасение',
    ),
    'граб¹|греб|грес|гроб': (
        'SPLIT',
        'Грести, гроб, погреб и погребение разделены. Сугроб и гребень тоже отдельно.',
        'ROW: грести, гребля; COFFIN: гроб; CELLAR: погреб; BURIAL: погребение; ROB: грабить; COMB: гребень; DRIFT: сугроб',
    ),
    'плач³|плес¹|плет|плот¹|плоч': (
        'SPLIT',
        'Плести, плот и плотник разделены. Отдельных лемм на плес- в этой семье нет. Сплетня, плотина и сплотить тоже вынесены.',
        'WEAVE: плести; RAFT: плот; CARPENTER: плотник; GOSSIP: сплетня; DAM: плотина; UNITE: сплотить; WHIP: плеть',
    ),
}


def main() -> int:
    started = time.perf_counter()
    print('inventory', flush=True)
    families, multi, universe = build_inventory()
    print('corpus', flush=True)
    corpus = load_corpus()
    attach_corpus(families, corpus)
    attach_rejects(families)
    score(families)
    print('fuzz', flush=True)
    fuzz = run_fuzz(corpus, families)
    attach_fanout(families, fuzz)
    print('checks', flush=True)
    corpus['service'] = service_audit(corpus)
    corpus['compounds'] = compound_audit()
    corpus['nuclei'] = nucleus_split_candidates(families)
    write_tables(families, multi, universe, fuzz, corpus)
    elapsed = time.perf_counter() - started
    print(f'total_seconds {elapsed:.1f}', flush=True)
    return 0


def _sense_ids_value(raw) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(raw)


def _sense_ids(lemma: str) -> tuple[str, ...]:
    return _sense_ids_value(_SENSE.get(lemma))


def build_inventory():
    families_map = _families(_KUZ_GROUPS)
    morph_family = {}
    for morph, family in families_map.items():
        morph_family[morph] = family
    grouped = {}
    lemma_roots = defaultdict(list)
    numbered = set()
    for line in _lines(_KUZ_LEMMAS):
        raw_lemma, raw_root = line.split('\t', 1)
        lemma = fold(_strip_note(raw_lemma))
        root = _norm(raw_root.strip())
        if not lemma or not root:
            continue
        if any(ch in root for ch in '¹²³⁴⁵⁶⁷⁸⁹⁰'):
            numbered.add(root)
        family = morph_family.get(root, root)
        senses = _sense_ids(lemma)
        games = senses or (_game_id(root, family),)
        for game in games:
            lemma_roots[lemma].append((family, game, root))
        bucket = grouped.setdefault(family, {
            'family': family,
            'alternations': family.split('|'),
            'lemmas': [],
            'games': Counter(),
            'senses': Counter(),
            'corpus_lemmas': set(),
            'corpus_occurrences': 0,
            'rejects': [],
        })
        if lemma not in bucket['lemmas']:
            bucket['lemmas'].append(lemma)
        for game in games:
            bucket['games'][game] += 1
        for sense in senses:
            bucket['senses'][sense] += 1
    multi = []
    repeated = 0
    over2 = 0
    max_roots = 0
    single = 0
    for lemma, structs in _all_structures().items():
        width = max((len(item) for item in structs), default=0)
        max_roots = max(max_roots, width)
        if any(len(item) >= 2 for item in structs):
            multi.append(lemma)
            if any(len(item) != len(set(item)) for item in structs):
                repeated += 1
            if any(len(item) > 2 for item in structs):
                over2 += 1
        elif structs:
            single += 1
    universe = {
        'lemmas_with_root': len(lemma_roots),
        'single_root_lemmas': single,
        'multi_root_lemmas': len(multi),
        'repeated_root_lemmas': repeated,
        'over_two_roots': over2,
        'max_roots': max_roots,
        'numbered_roots': len(numbered),
        'manual_senses': len({item for raw in _SENSE.values() for item in _sense_ids_value(raw)}),
        'manual_sense_lemmas': len(_SENSE),
        'identities': len({game for rows in lemma_roots.values() for _fam, game, _root in rows}),
    }
    return grouped, multi, universe


def _all_structures():
    from games.censorly.lexical.rootbank import _bank
    bank, _seconds = _bank()
    return bank


def load_corpus():
    articles = []
    occ_lemmas = Counter()
    occ_surfaces = Counter()
    rows = []
    for rel, _kind, body in iter_fixtures():
        surfaces = content_surfaces(body)
        articles.append((rel, surfaces))
        for surface in surfaces:
            occ_surfaces[fold(surface)] += 1
            if initially_open(surface):
                continue
            for item in readings_of(surface):
                occ_lemmas[item.lemma] += 1
            rows.append((rel, surface))
    return {
        'articles': articles,
        'lemma_occ': occ_lemmas,
        'rows': rows,
    }


def attach_corpus(families, corpus):
    lemma_family = {}
    for family, bucket in families.items():
        for lemma in bucket['lemmas']:
            lemma_family.setdefault(lemma, set()).add(family)
    for lemma, count in corpus['lemma_occ'].items():
        for family in lemma_family.get(lemma, ()):
            families[family]['corpus_lemmas'].add(lemma)
            families[family]['corpus_occurrences'] += count


def attach_rejects(families):
    lemma_family = defaultdict(set)
    for family, bucket in families.items():
        for lemma in bucket['lemmas']:
            lemma_family[lemma].add(family)
    for pair in REJECT:
        left, right = sorted(pair)
        shared = lemma_family[left] & lemma_family[right]
        for family in shared:
            families[family]['rejects'].append(f'{left}/{right}')


def score(families):
    for bucket in families.values():
        lemmas = bucket['lemmas']
        flags = []
        if len(lemmas) >= 40:
            flags.append('large')
        elif len(lemmas) >= 15:
            flags.append('medium_size')
        if len(bucket['alternations']) >= 4:
            flags.append('many_alternations')
        if bucket['rejects']:
            flags.append('old_reject')
        if len(bucket['senses']) >= 1:
            flags.append('partial_split')
        if len(bucket['games']) >= 3:
            flags.append('many_game_ids')
        prefixes = {prefix for lemma in lemmas for prefix in _PREFIXES if lemma.startswith(prefix)}
        if len(prefixes) >= 6:
            flags.append('many_prefixes')
        endings = Counter(lemma[-3:] for lemma in lemmas if len(lemma) >= 3)
        if len(endings) >= 12 and len(lemmas) >= 15:
            flags.append('many_suffixes')
        if bucket['corpus_occurrences'] >= 20:
            flags.append('corpus_hot')
        elif bucket['corpus_occurrences']:
            flags.append('in_corpus')
        bucket['flags'] = flags
        bucket['risk'] = (
            min(len(lemmas), 180)
            + 40 * len(bucket['rejects'])
            + 25 * len(bucket['senses'])
            + min(bucket['corpus_occurrences'], 80)
            + 15 * len(flags)
        )
        if bucket['senses'] or 'root:' in ''.join(bucket['games']):
            bucket['bucket'] = 'ALREADY_SPLIT'
        elif len(lemmas) <= 8 and not bucket['rejects'] and 'many_alternations' not in flags:
            bucket['bucket'] = 'AUTO_SAFE'
        else:
            bucket['bucket'] = 'REVIEW'
        if bucket['bucket'] == 'REVIEW' and bucket['risk'] >= 80:
            bucket['tier'] = 'high'
        elif bucket['bucket'] == 'REVIEW':
            bucket['tier'] = 'medium'
        else:
            bucket['tier'] = ''
        decision = VERDICTS.get(bucket['family'])
        if decision:
            bucket['verdict'] = decision[0]
            bucket['reason'] = decision[1]
            bucket['proposed'] = decision[2]
        else:
            bucket['verdict'] = 'UNREVIEWED'
            bucket['reason'] = ''
            bucket['proposed'] = ''


def run_fuzz(corpus, families):
    print('walking dictionary', flush=True)
    pymorphy_lemmas, ambiguous = walk_dictionary()
    guesses = set(pymorphy_lemmas)
    guesses.update(ambiguous)
    guesses.update(fold(item) for item in OOV_GUESSES + UNICODE_GUESSES)
    for _rel, surfaces in corpus['articles']:
        guesses.update(fold(item) for item in surfaces)
    for extra in ('строить', 'пара', 'белки', 'прибыли', 'замок', 'лук', 'орла'):
        guesses.add(extra)
    print(f'guesses {len(guesses)}', flush=True)
    index, occs = build_target_index(corpus)
    fanout_occ = []
    fanout_lemmas = []
    fanout_families = []
    ge = Counter()
    root_openings = Counter()
    ambiguous_rows = []
    top = []
    per_article = Counter()
    started = time.perf_counter()
    opened_guesses = 0
    opening_rows = 0
    for nth, guess in enumerate(guesses):
        if nth and nth % 20000 == 0:
            print(f'  {nth}', flush=True)
        if not guess or initially_open(guess):
            continue
        hits, reasons, root_families = lookup(guess, index, occs)
        if not hits:
            continue
        opened_guesses += 1
        lemmas = set()
        articles = set()
        for idx in hits:
            opening_rows += 1
            rel, surface, lemma_set, _family = occs[idx]
            lemmas.update(lemma_set)
            articles.add(rel)
            per_article[rel] += 1
        fanout_occ.append(len(hits))
        fanout_lemmas.append(len(lemmas))
        fanout_families.append(len(root_families))
        for limit in (2, 5, 10, 20, 50):
            if len(lemmas) >= limit:
                ge[limit] += 1
        for family in root_families:
            root_openings[family] += len(hits)
        reads = readings_of(guess)
        root_sets = []
        for item in reads:
            root_sets.append(tuple(structures_of(item.lemma)))
        distinct = {item for item in root_sets if item}
        if len(distinct) > 1 and len(top) < 400:
            ambiguous_rows.append((guess, len(lemmas), len(distinct), reasons))
        if len(lemmas) >= 8:
            top.append((len(lemmas), len(hits), guess, reasons, len(root_families), len(articles)))
    top.sort(reverse=True)
    ambiguous_rows.sort(key=lambda item: -item[1])
    elapsed = time.perf_counter() - started
    return {
        'guesses': len(guesses),
        'pymorphy_lemmas': len(pymorphy_lemmas),
        'ambiguous_forms': len(ambiguous),
        'opened_guesses': opened_guesses,
        'opening_rows': opening_rows,
        'fanout_occ': fanout_occ,
        'fanout_lemmas': fanout_lemmas,
        'fanout_families': fanout_families,
        'ge': ge,
        'root_openings': root_openings,
        'top': top[:100],
        'ambiguous': ambiguous_rows[:80],
        'per_article': per_article,
        'seconds': elapsed,
        'occurrences': len(occs),
    }


def build_target_index(corpus):
    by_lexeme = defaultdict(list)
    by_root = defaultdict(list)
    by_fold = defaultdict(list)
    occs = []
    for rel, surfaces in corpus['articles']:
        for surface in surfaces:
            if initially_open(surface):
                continue
            reads = readings_of(surface)
            lemmas = {item.lemma for item in reads}
            families = set()
            for item in reads:
                for struct in structures_of(item.lemma):
                    if len(struct) == 1:
                        families.add(struct[0])
            idx = len(occs)
            occs.append((rel, surface, lemmas, families))
            by_fold[fold(surface)].append(idx)
            for item in reads:
                by_lexeme[(item.para_id, item.lemma)].append(idx)
            from games.censorly.lexical.semantics import _roots
            for struct in _roots(reads, surface):
                by_root[struct].append(idx)
    return {'lexeme': by_lexeme, 'root': by_root, 'fold': by_fold}, occs


def lookup(guess, index, occs):
    from games.censorly.lexical.semantics import _roots
    hits = set(index['fold'].get(fold(guess), ()))
    reasons = set()
    if hits:
        reasons.add('exact')
    reads = readings_of(guess)
    before = len(hits)
    for item in reads:
        hits.update(index['lexeme'].get((item.para_id, item.lemma), ()))
    if len(hits) > before:
        reasons.add('lexeme')
    before = len(hits)
    families = set()
    for struct in _roots(reads, guess):
        hits.update(index['root'].get(struct, ()))
        if len(struct) == 1:
            families.add(struct[0])
    if len(hits) > before:
        reasons.add('root')
    # Drop the guess's own surface. Exact identity is not a root opening.
    hits = {idx for idx in hits if fold(occs[idx][1]) != fold(guess)}
    return hits, reasons, families


def attach_fanout(families, fuzz):
    for family, bucket in families.items():
        bucket['root_opening_hits'] = 0
    # root_openings is keyed by game id, not dictionary family.
    game_to_family = {}
    for family, bucket in families.items():
        for game in bucket['games']:
            game_to_family.setdefault(game, set()).add(family)
    for game, hits in fuzz['root_openings'].items():
        for family in game_to_family.get(game, ()):
            families[family]['root_opening_hits'] += hits


def service_audit(corpus):
    """Count every word token, including stops that content_surfaces drops."""
    from games.censorly.tokenize import tokenize_text
    contested = Counter()
    opened_anyway = Counter()
    opened = 0
    total = 0
    kept_closed = 0
    for _rel, _kind, body in iter_fixtures():
        for tok in tokenize_text(body):
            if tok.get('kind') not in ('content', 'stop'):
                continue
            surface = tok.get('surface') or ''
            if not surface:
                continue
            total += 1
            reads = readings_of(surface)
            service = [item for item in reads if item.pos in {'PREP', 'CONJ', 'PRCL', 'INTJ'}]
            content = [
                item for item in reads
                if item.pos and item.pos not in {'PREP', 'CONJ', 'PRCL', 'INTJ'}
            ]
            key = fold(surface)
            if initially_open(surface):
                opened += 1
            if service and content:
                contested[key] += 1
                if initially_open(surface):
                    opened_anyway[key] += 1
                else:
                    kept_closed += 1
    return {
        'total_word_tokens': total,
        'opened': opened,
        'contested_surfaces': len(contested),
        'contested_kept_closed': kept_closed,
        'opened_despite_content': len(opened_anyway),
        'top': contested.most_common(100),
        'opened_anyway': opened_anyway.most_common(40),
    }


def compound_audit():
    from games.censorly.lexical.rootbank import _bank
    bank, _seconds = _bank()
    leaks = []
    for lemma, structs in bank.items():
        singles = {item[0] for item in structs if len(item) == 1}
        for item in structs:
            if len(item) >= 2 and singles & set(item):
                leaks.append(lemma)
                break
    return {'leaks': leaks[:20], 'leak_count': len(leaks)}


# Verbal morphology, not modern-sense nuclei. Size alone is also a bad signal:
# дать / нести are productive and usually stay one family.
_NUCLEUS_NOISE = frozenset({
    'вать', 'ться', 'ива', 'ыва', 'ени', 'ение', 'тель', 'ность', 'нный',
    'еск', 'ость', 'ать', 'ять', 'ить', 'еть', 'уть', 'ший', 'вший',
    'еств', 'ован', 'ева', 'анн', 'енн', 'ыва', 'ива',
})


def nucleus_split_candidates(families, *, min_lemmas: int = 40, min_nuclei: int = 3):
    """Fat dictionary families that look like several modern words glued together.

    Prefer several long content stems that each cover a minority of lemmas
    still on the parent game root. Generic verb morphology is ignored.
    """
    from games.censorly.lexical.rootbank import structures_of

    rows = []
    for family, bucket in families.items():
        fam_id = 'fam:' + family
        on_parent = [
            lemma for lemma in bucket['lemmas']
            if any(fam_id in struct for struct in structures_of(lemma))
        ]
        if len(on_parent) < min_lemmas:
            continue
        counts = Counter()
        for lemma in on_parent:
            for length in (7, 6, 5, 4):
                if len(lemma) < length:
                    continue
                for start in range(0, len(lemma) - length + 1):
                    stem = lemma[start:start + length]
                    if not stem.isalpha() or stem in _NUCLEUS_NOISE:
                        continue
                    if any(noise in stem and len(stem) <= len(noise) + 1 for noise in _NUCLEUS_NOISE):
                        continue
                    counts[stem] += 1
        nuclei = []
        covered = set()
        for stem, hit in counts.most_common():
            if hit < 5 or hit > len(on_parent) * 0.75:
                continue
            members = {lemma for lemma in on_parent if stem in lemma}
            novel = members - covered
            if len(novel) < 5:
                continue
            if any(stem in other or other in stem for other, _ in nuclei):
                continue
            nuclei.append((stem, len(members)))
            covered.update(members)
            if len(nuclei) >= 8:
                break
        if len(nuclei) < min_nuclei:
            continue
        rows.append({
            'family': family,
            'on_parent': len(on_parent),
            'lemmas': len(bucket['lemmas']),
            'verdict': bucket.get('verdict', ''),
            'nuclei': nuclei,
            'score': len(nuclei) * 25 + sum(n for _, n in nuclei) + len(covered),
        })
    rows.sort(key=lambda item: (-item['score'], -item['on_parent']))
    return rows


def write_tables(families, multi, universe, fuzz, corpus):
    ordered = sorted(families.values(), key=lambda item: (-item['risk'], -len(item['lemmas'])))
    buckets = Counter(item['bucket'] for item in ordered)
    tiers = Counter(item['tier'] for item in ordered if item['tier'])
    review_path = DATA / 'root_family_review.tsv'
    risk_path = DATA / 'root_family_risk.tsv'
    header = (
        'root_family\talternations\tlemma_count\tlemmas\tpos_distribution\t'
        'corpus_count\tmax_fanout\trisk_flags\tcurrent_split\tverdict\t'
        'proposed_subfamilies\treason\tbucket\trisk'
    )
    lines = [header]
    risk_lines = ['root_family\trisk\tbucket\ttier\tlemma_count\tcorpus_count\tflags\trejects']
    for bucket in ordered:
        lemmas = bucket['lemmas']
        shown = ','.join(lemmas if len(lemmas) <= 80 else lemmas[:80])
        if len(lemmas) > 80:
            shown += f',…(+{len(lemmas) - 80})'
        splits = ','.join(f'{name}:{count}' for name, count in bucket['senses'].most_common())
        reason = bucket.get('reason', '')
        proposed = bucket.get('proposed') or splits
        verdict = bucket['verdict']
        lines.append('\t'.join([
            bucket['family'].replace('\t', ' '),
            '|'.join(bucket['alternations']),
            str(len(lemmas)),
            shown,
            '',
            str(bucket['corpus_occurrences']),
            str(bucket.get('root_opening_hits', 0)),
            ','.join(bucket['flags']),
            splits,
            verdict,
            proposed,
            reason,
            bucket['bucket'],
            str(bucket['risk']),
        ]))
        risk_lines.append('\t'.join([
            bucket['family'].replace('\t', ' '),
            str(bucket['risk']),
            bucket['bucket'],
            bucket['tier'],
            str(len(lemmas)),
            str(bucket['corpus_occurrences']),
            ','.join(bucket['flags']),
            ';'.join(bucket['rejects']),
        ]))
    review_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    risk_path.write_text('\n'.join(risk_lines) + '\n', encoding='utf-8')
    fuzz_lines = ['rank\tunique_lemmas\toccurrences\tguess\treasons\troot_families\tarticles']
    for rank, row in enumerate(fuzz['top'], 1):
        lemmas, occ, guess, reasons, nfam, nart = row
        fuzz_lines.append(f'{rank}\t{lemmas}\t{occ}\t{guess}\t{",".join(sorted(reasons))}\t{nfam}\t{nart}')
    (DATA / 'full_semantics_fuzz.tsv').write_text('\n'.join(fuzz_lines) + '\n', encoding='utf-8')
    nuclei_lines = ['score\ton_parent\tlemmas\tverdict\tfamily\tnuclei']
    for row in corpus.get('nuclei') or []:
        nuclei = ';'.join(f'{stem}:{count}' for stem, count in row['nuclei'])
        nuclei_lines.append(
            f'{row["score"]}\t{row["on_parent"]}\t{row["lemmas"]}\t{row["verdict"]}\t'
            f'{row["family"]}\t{nuclei}'
        )
    (DATA / 'root_family_nuclei.tsv').write_text('\n'.join(nuclei_lines) + '\n', encoding='utf-8')
    report = render_report(ordered, buckets, tiers, multi, universe, fuzz, corpus)
    (DATA / 'full_semantics_report.txt').write_text(report, encoding='utf-8')
    sys.stdout.write(report)


def render_report(ordered, buckets, tiers, multi, universe, fuzz, corpus):
    occs = fuzz['fanout_occ'] or [0]
    lemmas = fuzz['fanout_lemmas'] or [0]
    def pct(values, q):
        ordered_values = sorted(values)
        idx = min(len(ordered_values) - 1, int(q * (len(ordered_values) - 1)))
        return ordered_values[idx]
    checksums = []
    for path in (_KUZ_LEMMAS, _KUZ_GROUPS, _TIKHONOV):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        checksums.append(f'{path.name} {digest}')
    high = [item for item in ordered if item['tier'] == 'high']
    medium = [item for item in ordered if item['tier'] == 'medium']
    verdicts = Counter(item['verdict'] for item in ordered)
    unclear = [item for item in ordered if item['verdict'] == 'UNCLEAR']
    lines = [
        f'lemmas_with_a_kuz_root {universe["lemmas_with_root"]}',
        f'root_identities_after_split {universe["identities"]}',
        f'dictionary_families {len(ordered)}',
        f'single_root_lemmas {universe["single_root_lemmas"]}',
        f'multi_root_lemmas {universe["multi_root_lemmas"]}',
        f'repeated_root_lemmas {universe["repeated_root_lemmas"]}',
        f'lemmas_with_more_than_two_roots {universe["over_two_roots"]}',
        f'max_roots_in_one_structure {universe["max_roots"]}',
        f'numbered_root_spellings {universe["numbered_roots"]}',
        f'manual_sense_ids {universe["manual_senses"]}',
        f'manual_sense_lemmas {universe["manual_sense_lemmas"]}',
        f'AUTO_SAFE {buckets["AUTO_SAFE"]}',
        f'REVIEW {buckets["REVIEW"]}',
        f'REVIEW_high {tiers["high"]}',
        f'REVIEW_medium {tiers["medium"]}',
        f'ALREADY_SPLIT {buckets["ALREADY_SPLIT"]}',
        f'verdict_SPLIT {verdicts["SPLIT"]}',
        f'verdict_SAFE_ONE_FAMILY {verdicts["SAFE_ONE_FAMILY"]}',
        f'verdict_AGENT_THINKS_SAFE {verdicts["AGENT_THINKS_SAFE"]}',
        f'verdict_TO_SPLIT {verdicts["TO_SPLIT"]}',
        f'verdict_UNCLEAR {verdicts["UNCLEAR"]}',
        f'verdict_UNREVIEWED {verdicts["UNREVIEWED"]}',
        f'hand_decisions_still_open {len(unclear)}',
        f'high_risk_unresolved {len(unclear)}',
        f'compound_subset_leaks {corpus["compounds"]["leak_count"]}',
        f'service_occurrences_initially_open {corpus["service"]["opened"]}',
        f'service_contested_surfaces {corpus["service"]["contested_surfaces"]}',
        f'service_contested_kept_closed {corpus["service"]["contested_kept_closed"]}',
        f'service_opened_despite_content_surfaces {corpus["service"]["opened_despite_content"]}',
        f'service_word_tokens {corpus["service"]["total_word_tokens"]}',
        f'high_review_lemmas {sum(len(item["lemmas"]) for item in high)}',
        f'medium_review_lemmas {sum(len(item["lemmas"]) for item in medium)}',
        f'fuzz_guesses {fuzz["guesses"]}',
        f'pymorphy_lemmas {fuzz["pymorphy_lemmas"]}',
        f'ambiguous_forms {fuzz["ambiguous_forms"]}',
        f'fuzz_seconds {fuzz["seconds"]:.1f}',
        f'target_occurrences_indexed {fuzz["occurrences"]}',
        f'guesses_with_opening {fuzz["opened_guesses"]}',
        f'opening_rows {fuzz["opening_rows"]}',
        f'max_occurrence_fanout {max(occs)}',
        f'max_lemma_fanout {max(lemmas)}',
        f'p50_lemma_fanout {pct(lemmas, 0.50)}',
        f'p95_lemma_fanout {pct(lemmas, 0.95)}',
        f'p99_lemma_fanout {pct(lemmas, 0.99)}',
        f'fanout_ge_2 {fuzz["ge"][2]}',
        f'fanout_ge_5 {fuzz["ge"][5]}',
        f'fanout_ge_10 {fuzz["ge"][10]}',
        f'fanout_ge_20 {fuzz["ge"][20]}',
        f'fanout_ge_50 {fuzz["ge"][50]}',
        'checksums ' + ' '.join(checksums),
        'gate_families',
    ]
    gate_ids = {
        'ста(j)|сто(j)', 'раж¹|раз¹', 'пас²',
        'плач³|плес¹|плет|плот¹|плоч', 'мар³', 'граб¹|греб|грес|гроб',
    }
    for item in ordered:
        if item['family'] not in gate_ids:
            continue
        branches = ', '.join(
            f'{name}:{count}' for name, count in item['senses'].most_common()
        )
        lines.append(
            f'  {item["verdict"]} n={len(item["lemmas"])} {item["family"]} | {branches or "parent family"}'
        )
    lines.extend([
        'unclear_blockers',
    ])
    for item in unclear:
        lines.append(
            f'  n={len(item["lemmas"])} corpus={item["corpus_occurrences"]} {item["family"]}'
        )
        lines.append(f'    {item.get("reason", "")}')
        lines.append(f'    {item.get("proposed", "")}')
    lines.append('service_contested_top')
    for surface, count in corpus['service']['top'][:30]:
        lines.append(f'  {count} {surface}')
    lines.append('service_opened_despite_content')
    for surface, count in corpus['service']['opened_anyway'][:25]:
        lines.append(f'  {count} {surface}')
    if corpus['compounds']['leaks']:
        lines.append('compound_leak_sample ' + ', '.join(corpus['compounds']['leaks']))
    lines.append('nucleus_split_candidates')
    for row in (corpus.get('nuclei') or [])[:25]:
        nuclei = ', '.join(f'{stem}:{count}' for stem, count in row['nuclei'])
        lines.append(
            f'  score={row["score"]} on={row["on_parent"]} {row["verdict"] or "UNREVIEWED"} '
            f'{row["family"][:70]} | {nuclei}'
        )
    lines.append('top_review')
    for item in high[:40]:
        sample = ', '.join(item['lemmas'][:18])
        lines.append(
            f'  risk={item["risk"]} n={len(item["lemmas"])} corpus={item["corpus_occurrences"]} '
            f'hits={item.get("root_opening_hits", 0)} {item["family"][:80]} | {sample}'
        )
        if item['rejects']:
            lines.append('    rejects ' + '; '.join(item['rejects'][:8]))
    lines.append('top_guesses')
    for row in fuzz['top'][:25]:
        lines.append(f'  lemmas={row[0]} occ={row[1]} {row[2]} {",".join(sorted(row[3]))} families={row[4]}')
    lines.append('ambiguous_readings')
    for guess, nlem, nroots, reasons in fuzz['ambiguous'][:20]:
        lines.append(f'  {guess} target_lemmas={nlem} root_readings={nroots} {",".join(sorted(reasons))}')
    lines.append('per_article_openings')
    for rel, count in fuzz['per_article'].most_common():
        lines.append(f'  {count} {rel}')
    if unclear:
        lines.append(
            f'verdict ROOT SEMANTICS NEED MORE REVIEW / remaining_blockers {len(unclear)}'
        )
    else:
        lines.append('verdict ROOT REVIEW GATE CLOSED / remaining_blockers 0')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    sys.exit(main())
