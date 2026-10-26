"""Compatibility alias. Implementation lives in games.ops.workers.handlers.word_salad."""
import sys

import games.ops.workers.handlers.word_salad as _implementation

sys.modules[__name__] = _implementation
