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

# Never reach real Telegram from tests (see test_isolation.py).
from .test_isolation import *  # noqa: E402,F403,F401

# Test users get small ids: the first one must not inherit every unauthored
# daily release. Tests of that fallback turn it on with override_settings.
DAILY_DEFAULT_AUTHOR_USER_ID = None

# The hub's process-local link cache would carry placements across tests that
# reuse game ids; its own tests enable it with override_settings.
HUB_LINK_CACHE_SECONDS = 0

# The site author's "credited elsewhere" set would outlive each test's tasks.
CREDITED_ELSEWHERE_CACHE_SECONDS = 0

# Process-level SocialApp lookups would leak OAuth apps between tests.
SOCIAL_APPS_CACHE_SECONDS = 0
