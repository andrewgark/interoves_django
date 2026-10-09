"""Local test settings with a persistent SQLite test database.

The default Django SQLite test database is in-memory, so ``--keepdb`` cannot
reuse it between commands.  These settings keep the normal application
configuration and only change the SQLite test database path.
"""

import copy
import os
import tempfile

from .settings import *  # noqa: F403,F401


DATABASES = copy.deepcopy(DATABASES)  # noqa: F405
if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default'].setdefault('TEST', {})['NAME'] = os.environ.get(
        'INTEROVES_TEST_DB_PATH',
        os.path.join(tempfile.gettempdir(), 'interoves-django-test.sqlite3'),
    )
    # Fail clearly if another local test run owns the SQLite write lock.
    DATABASES['default'].setdefault('OPTIONS', {})['timeout'] = 5
