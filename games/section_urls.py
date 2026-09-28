"""Compatibility alias for section URL pattern builders."""

import sys

from games.sections import urls as _implementation

sys.modules[__name__] = _implementation
