"""Compatibility alias for section hub builders."""

import sys

from games.sections import hub as _implementation

sys.modules[__name__] = _implementation
