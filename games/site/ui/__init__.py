"""Compatibility alias. Implementation lives in games.site.pages."""

from games.site.pages import shared as _shared

for _name, _value in vars(_shared).items():
    if _name.startswith('__'):
        continue
    globals()[_name] = _value

del _name, _value, _shared
