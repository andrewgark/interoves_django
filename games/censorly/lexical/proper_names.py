"""Popular proper-name stems missing from Kuznetsova, with obvious cognates.

These are not dictionary-family cuts. Each cluster is a synthetic game root
so the place/person opens its adjective and demonym (рим → римской).

Do not list lemmas that already have a real Kuznetsova family (немец, русский,
грек, земля, …): overriding them would detach the common-word nest.
"""

from __future__ import annotations


def install(sense, set_readings) -> None:
    for name, lemmas in CLUSTERS:
        sense(lemmas, name)
    for lemma, names in MULTI_READINGS:
        set_readings(lemma, names)


# Nominative lemmas only; inflected surfaces open via lexeme → lemma → sense.
CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('sense:rome', (
        'рим', 'римский', 'римлянин', 'римлянка',
    )),
    ('sense:paris', (
        'париж', 'парижский', 'парижанин', 'парижанка',
    )),
    ('sense:moscow', (
        'москва', 'московский', 'москвич', 'москвичка',
    )),
    ('sense:london', (
        'лондон', 'лондонский', 'лондонец', 'лондонка',
    )),
    ('sense:berlin', (
        'берлин', 'берлинский', 'берлинец', 'берлинка',
    )),
    ('sense:petersburg', (
        'петербург', 'петербургский', 'петербуржец', 'петербурженка',
        'ленинград', 'ленинградский', 'ленинградец', 'ленинградка',
    )),
    ('sense:kyiv', (
        'киев', 'киевский', 'киевлянин', 'киевлянка',
    )),
    ('sense:china', (
        'китай', 'китайский', 'китаец', 'китаянка',
    )),
    ('sense:france', (
        'франция', 'французский', 'француз', 'француженка',
    )),
    # немец / немецкий stay on fam:нем
    ('sense:germany', (
        'германия', 'германский',
    )),
    ('sense:england', (
        'англия', 'английский', 'англичанин', 'англичанка',
    )),
    ('sense:italy', (
        'италия', 'итальянский', 'итальянец', 'итальянка',
    )),
    ('sense:spain', (
        'испания', 'испанский', 'испанец', 'испанка',
    )),
    ('sense:japan', (
        'япония', 'японский', 'японец', 'японка',
    )),
    ('sense:egypt', (
        'египет', 'египетский', 'египтянин', 'египтянка',
    )),
    ('sense:india', (
        'индия', 'индийский', 'индиец', 'индианка',
    )),
    ('sense:america', (
        'америка', 'американский', 'американец', 'американка',
    )),
    # русский stays on fam:рус²
    ('sense:russia', (
        'россия', 'российский',
    )),
    ('sense:europe', (
        'европа', 'европейский', 'европеец', 'европейка',
    )),
    ('sense:asia', (
        'азия', 'азиатский', 'азиат', 'азиатка',
    )),
    ('sense:baikal', (
        'байкал', 'байкальский',
    )),
    ('sense:volga', (
        'волга', 'волжский',
    )),
    ('sense:pushkin', (
        'пушкин', 'пушкинский',
    )),
    # толстовский otherwise falls into fam:толст|толщ (thick)
    ('sense:tolstoy', (
        'толстой', 'толстовский',
    )),
    ('sense:saturn', (
        'сатурн',
    )),
    ('sense:jupiter', (
        'юпитер',
    )),
)

MULTI_READINGS: tuple[tuple[str, tuple[str, ...]], ...] = ()
