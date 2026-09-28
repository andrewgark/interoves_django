"""Compatibility alias for the Word Salad daily schedule adapter."""

import sys

from games.daily.catalog import word_salad as _implementation

sys.modules[__name__] = _implementation
