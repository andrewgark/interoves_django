"""Our dictionaries: every hand-reviewed table the gameplay matcher reads.

One ``ReviewedTable`` per file under ``lexical/data``. Nothing else in the
matcher opens a data file. Reports, review queues and tool memory that live
next to these files are not listed here and do not affect play.

Still code, not tables (see docs/censorly-matching-refactor-plan.md, Ш9):
sense clusters in ``lexical/splits.py`` and proper-name clusters and pair
relations in ``lexical/match/proper_names.py``.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical.reviewed.table import (
    AnchorTable,
    ComponentTable,
    GroupTable,
    PairSet,
    ReviewedTable,
    SpellingSet,
    StructureTable,
)

# ── Question 1: which tokens are open from the start ───────────────────────

# word → never_open | always_open | pronoun_adjective
VISIBILITY = ReviewedTable('visibility.tsv', ('word', 'rule', 'note'))

# ── Question 2: which readings a spelling has ──────────────────────────────

# (surface, lemma, POS) readings that are wrong everywhere.
REJECTED_READINGS = ReviewedTable('rejected_readings.tsv', ('surface', 'lemma', 'pos', 'note'))
# (surface, lemma chosen by the tokenizer) → lemmas of that occurrence.
# mode ``filter`` keeps only those of the spelling's readings;
# ``substitute`` reads the occurrence as the lemma itself (есть → быть);
# ``closed`` filters and hides a function-word spelling (в кому).
TARGET_READINGS = ReviewedTable(
    'target_readings.tsv', ('surface', 'article_lemma', 'allowed', 'mode', 'note'),
)
# spelling → the lemma it always reads as (лет → год). A ``capitalized`` row
# applies only to a capitalized spelling, in a guess and in the article (Троя).
FIXED_READINGS = ReviewedTable('fixed_readings.tsv', ('surface', 'lemma', 'capitalized', 'note'))
# label/short spelling → full lemma: stubs (фр), compound halves (юго),
# homographic language labels (кит.).
LABELS = ReviewedTable('labels.tsv', ('label', 'lemma', 'kind', 'note'))

# ── Question 3: which root structures a lemma has ──────────────────────────

# Tikhonov morph spellings that become their own root (tikh:<morph>).
OPAQUE_ROOTS = SpellingSet('reviewed_opaque_roots.tsv', ('root_spelling', 'note'), 'root_spelling')
# lemma → complete structures written by hand; several rows are alternatives.
DERIVED_ROOTS = StructureTable('reviewed_derived_roots.tsv', alternatives=True)
# lemma → one existing dictionary root the dictionaries failed to authorize.
EXISTING_ROOTS = StructureTable('reviewed_existing_root_assignments.tsv', alternatives=False)
# Tikhonov morph → root id it takes only as a part of a compound (лог → root:logy).
COMPOUND_COMPONENTS = ComponentTable('compound_components.tsv', ('morph', 'root', 'note'))
# (Tikhonov morph, game root) spelling matches that are wrong.
REJECTED_FALLBACK = PairSet('rejected_fallback.tsv', ('morph', 'root', 'reason', 'reviewed_at'))
# digit → cardinal numeral it spells.
DIGIT_CARDINALS = ReviewedTable('digit_cardinals.tsv', ('digit', 'cardinal'))

# Lemmas that copy the structures of another lemma, in application order.
NEGATIONS = AnchorTable(
    'negation_pairs.tsv', ('negated', 'base'),
    source='NEGATION_OF_BASE', replace='bare_subset',
    check=lambda lemma, base: '' if lemma == 'не' + base else f'not a не- pair: {lemma} / {base}',
)
NEST_ANCHORS = AnchorTable(
    'reviewed_nest_anchors.tsv', ('lemma', 'anchor', 'nest', 'note'),
    source='REVIEWED_NEST_ANCHOR', replace='rootless',
)
ADVERB_BASES = AnchorTable(
    'adverb_bases.tsv', ('adverb', 'adjective', 'evidence'),
    source='ADVERB_OF_ADJECTIVE', replace='rootless',
)
HALF_COMPOUNDS = AnchorTable(
    'half_compounds.tsv', ('compound', 'base'),
    source='HALF_COMPOUND', replace='always', allow_empty_anchor=True,
    check=lambda lemma, _base: '' if 'пол' in lemma else f'not a пол- compound: {lemma}',
)

# ── Question 4: which pairs are linked directly ────────────────────────────

# full form → its abbreviations (кг, км). Abbreviations are matched by
# spelling, the full form by lemma. Labels that are ordinary words (кит,
# тур, порт) are not aliases: they read as a language only in label
# position (``labels.tsv``, kind language_label).
ALIASES = ReviewedTable('aliases.tsv', ('full', 'short', 'note'))
# Suppletive and ordinal groups (два/второй, хороший/лучше).
GRAMMAR_GROUPS = GroupTable('grammar_groups.tsv', ('words', 'note'), 'words')

__all__ = [
    'ADVERB_BASES',
    'ALIASES',
    'COMPOUND_COMPONENTS',
    'DERIVED_ROOTS',
    'DIGIT_CARDINALS',
    'EXISTING_ROOTS',
    'FIXED_READINGS',
    'GRAMMAR_GROUPS',
    'HALF_COMPOUNDS',
    'LABELS',
    'NEGATIONS',
    'NEST_ANCHORS',
    'OPAQUE_ROOTS',
    'REJECTED_FALLBACK',
    'REJECTED_READINGS',
    'TARGET_READINGS',
    'VISIBILITY',
    'AnchorTable',
    'ReviewedTable',
]
