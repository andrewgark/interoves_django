"""Settings for ChannelsLiveServerTestCase + Playwright browser tests."""

import copy
import os
import tempfile

from .settings import *  # noqa: F403,F401

DATABASES = copy.deepcopy(DATABASES)  # noqa: F405
DATABASES['default'].setdefault('TEST', {})['NAME'] = os.path.join(
    tempfile.gettempdir(),
    f'interoves-live-tests-{os.getpid()}.sqlite3',
)
INTEROVES_LIVE_BROWSER_TESTS = True

# Never reach real Telegram from tests (see test_isolation.py).
from .test_isolation import *  # noqa: E402,F403,F401
