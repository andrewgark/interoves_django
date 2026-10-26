"""Compatibility alias. Implementation lives in games.ops.social.views."""
import sys

from games.ops.social import views as _implementation

sys.modules[__name__] = _implementation
