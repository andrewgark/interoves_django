"""Section score materialization.

Implementation lives in ``games.daily.projection.materialize``. This package
re-exports that module so existing ``from games.daily.projection import ...``
imports keep working.
"""

from games.daily.projection import materialize as _materialize

for _name, _value in vars(_materialize).items():
    if _name.startswith('__'):
        continue
    globals()[_name] = _value

del _name, _value, _materialize
