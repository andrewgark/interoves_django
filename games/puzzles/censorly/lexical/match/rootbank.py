"""Root structures of lemmas: names used by gameplay, tools and tests.

The bank is built by ``lexical.deciders.rootbank.RootBank``.
"""

from __future__ import annotations

from games.puzzles.censorly.lexical.deciders.rootbank import ROOT_BANK

structures_of = ROOT_BANK.structures_of
families_of = ROOT_BANK.families_of
assignment_of = ROOT_BANK.assignment_of
provenance = ROOT_BANK.provenance
tikhonov_roots_of = ROOT_BANK.tikhonov_roots_of
component_audit = ROOT_BANK.component_audit
load_seconds = ROOT_BANK.load_seconds
reset_cache = ROOT_BANK.reset


def root_components(structures) -> tuple[tuple[str, ...], ...]:
    """The non-sense components of complete structures."""
    return tuple(
        tuple(part for part in structure if not part.startswith('sense:'))
        for structure in structures
    )


def sense_components(structures) -> tuple[tuple[str, ...], ...]:
    """The sense components of complete structures."""
    return tuple(
        tuple(part for part in structure if part.startswith('sense:'))
        for structure in structures
    )
