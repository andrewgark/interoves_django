"""Compatibility alias. Implementation lives in games.site.views.workers.word_salad."""
import sys

from games.site.views.workers import word_salad as _implementation

sys.modules[__name__] = _implementation
