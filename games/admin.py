"""Django admin autodiscover entry. Registrations live in games.site.admin."""
import sys

from games.site import admin as _implementation

sys.modules[__name__] = _implementation
