"""Load puzzle offer model modules while ``games.models`` is still initializing."""

import importlib.util
import sys
from pathlib import Path


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
