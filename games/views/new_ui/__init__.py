"""Main UI views — legacy import path via games.views.new_ui.legacy_exports."""

from games.views.new_ui import legacy_exports as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))
