"""Main UI views — legacy import path; implementation in site.views.pages.ui_exports."""

from games.site.views.pages import ui_exports as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))
