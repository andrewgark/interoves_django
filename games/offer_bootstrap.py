"""Load puzzle offer model modules while ``games.models`` is still initializing.

Offer ORM classes live under ``games.puzzles.*.models``. Importing that path
normally loads ``games.puzzles`` (and puzzle play code that imports
``games.models``). Register module objects in ``sys.modules`` under short
``games.{alphabetty,censorly,salad,ladder}.models`` names before importing them
from ``games.models``.
"""

import importlib.util
import sys
from pathlib import Path

_OFFER_MODEL_SPECS = (
    ('games.alphabetty.models', 'games.puzzles.alphabetty.models', 'puzzles/alphabetty/models.py'),
    ('games.censorly.models', 'games.puzzles.censorly.models', 'puzzles/censorly/models.py'),
    ('games.ladder.models', 'games.puzzles.raddle.models', 'puzzles/raddle/models.py'),
    ('games.salad.models', 'games.puzzles.salad.models', 'puzzles/salad/models.py'),
)


def install_puzzles_models_facade(*, facade_module: str, impl_module: str, impl_relpath: str) -> None:
    """Register facade and implementation modules, then execute model code."""
    root = Path(__file__).resolve().parent
    impl_path = root / impl_relpath
    spec = importlib.util.spec_from_file_location(impl_module, impl_path)
    implementation = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[impl_module] = implementation
    sys.modules[facade_module] = implementation
    spec.loader.exec_module(implementation)


def install_all_offer_model_facades() -> None:
    for facade_module, impl_module, impl_relpath in _OFFER_MODEL_SPECS:
        if facade_module in sys.modules:
            continue
        install_puzzles_models_facade(
            facade_module=facade_module,
            impl_module=impl_module,
            impl_relpath=impl_relpath,
        )
