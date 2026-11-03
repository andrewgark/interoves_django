"""SQLite backend whose transactions take the write lock up front.

Django's SQLite backend opens ``atomic()`` with a deferred ``BEGIN``: the
transaction reads under a shared lock and upgrades on its first write. When
another connection already holds the write lock, SQLite refuses that upgrade
at once with "database is locked" instead of waiting out the busy timeout
(waiting could deadlock). The live server handles requests on several
threads, so two concurrent writes (an attempt and a timing ping) could fail
a browser test with a 500. ``BEGIN IMMEDIATE`` waits for the lock instead.

Only for the live-browser test settings; production runs MySQL.
"""
from django.db.backends.sqlite3 import base


class DatabaseWrapper(base.DatabaseWrapper):
    def _start_transaction_under_autocommit(self):
        self.cursor().execute('BEGIN IMMEDIATE')
