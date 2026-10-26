"""Active solving time for one actor and one task group.

Implementation lives in ``games.daily.timing.session``. This package re-exports
that module so existing ``from games.daily.timing import ...`` imports and
patches of ``games.daily.timing.<name>`` keep working. The logger name remains
``games.daily_timing``.
"""

from games.daily.timing import session as _session

for _name, _value in vars(_session).items():
    if _name.startswith('__'):
        continue
    globals()[_name] = _value

del _name, _value, _session
