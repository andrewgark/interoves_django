"""Compatibility alias. Implementation lives in games.ops.social.ads.schema.fields."""
import sys

from games.ops.social.ads.schema import fields as _implementation

sys.modules[__name__] = _implementation
