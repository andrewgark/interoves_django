"""Compatibility alias. Implementation lives in games.puzzles.censorly.models."""

from games.offer_bootstrap import install_puzzles_models_facade

install_puzzles_models_facade(
    facade_module=__name__,
    impl_module='games.puzzles.censorly.models',
    impl_relpath='puzzles/censorly/models.py',
)
