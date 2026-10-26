"""Compatibility alias. Implementation lives in games.ops.social.ads.schema.constants."""
import sys

from games.ops.social.ads.schema import constants as _implementation

sys.modules[__name__] = _implementation
