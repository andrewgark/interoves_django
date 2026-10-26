"""Compatibility alias. Implementation lives in games.puzzles.alphabetty.models."""

from games.offer_bootstrap import install_puzzles_models_facade

install_puzzles_models_facade(
    facade_module=__name__,
    impl_module='games.puzzles.alphabetty.models',
    impl_relpath='puzzles/alphabetty/models.py',
)
