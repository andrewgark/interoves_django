"""Database-backed sessions read through the shared Redis cache.

Every signed-in request used to start with a ``django_session`` query; at
the top of each hour RDS log rotation stretched that read to seconds
(``auth_session_load`` 2.3 s on 2026-11-19). The database stays the source
of truth: writes go to both, reads try Redis first.

Unlike Django's ``cached_db`` (which only tolerates cache errors on read),
any cache failure here falls back to the database, so a Redis outage cannot
take sign-in down. Cached copies live at most a day: 700k sessions exist,
~50k are used daily.
"""

import logging

from django.contrib.sessions.backends.cached_db import SessionStore as CachedDBStore

logger = logging.getLogger('games.sessions')

CACHE_TTL_SECONDS = 24 * 60 * 60


class SessionStore(CachedDBStore):
    cache_key_prefix = 'interoves.sessions.cached_db'

    def _cache_ttl(self, expiry_age):
        return max(1, min(int(expiry_age), CACHE_TTL_SECONDS))

    def load(self):
        try:
            data = self._cache.get(self.cache_key)
        except Exception:
            data = None
        if data is not None:
            return data
        session = self._get_session_from_db()
        if not session:
            return {}
        data = self.decode(session.session_data)
        try:
            self._cache.set(
                self.cache_key, data,
                self._cache_ttl(self.get_expiry_age(expiry=session.expire_date)),
            )
        except Exception:
            pass
        return data

    def exists(self, session_key):
        if not session_key:
            return False
        try:
            if (self.cache_key_prefix + session_key) in self._cache:
                return True
        except Exception:
            pass
        return super(CachedDBStore, self).exists(session_key)

    def save(self, must_create=False):
        super(CachedDBStore, self).save(must_create)
        try:
            self._cache.set(self.cache_key, self._session, self._cache_ttl(self.get_expiry_age()))
        except Exception:
            pass

    def delete(self, session_key=None):
        super(CachedDBStore, self).delete(session_key)
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        key = self.cache_key_prefix + session_key
        for _attempt in range(2):
            try:
                self._cache.delete(key)
                return
            except Exception:
                continue
        # The database row is gone. While Redis is unreachable reads fall back
        # to the database too; only a Redis that comes back with this entry
        # could revive it, for at most CACHE_TTL_SECONDS.
        logger.warning('session_cache_delete_failed')
