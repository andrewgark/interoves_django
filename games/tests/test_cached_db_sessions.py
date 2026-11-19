from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.test import TestCase

from games.site.session.cached_db_store import CACHE_TTL_SECONDS, SessionStore


class CachedDbSessionStoreTests(TestCase):
    def setUp(self):
        caches['shared'].clear()
        self.addCleanup(caches['shared'].clear)

    def _saved(self):
        store = SessionStore()
        store['k'] = 'v'
        store.save()
        return store.session_key

    def test_reads_come_from_the_cache_and_writes_reach_the_database(self):
        key = self._saved()
        self.assertTrue(Session.objects.filter(session_key=key).exists())
        with self.assertNumQueries(0):
            self.assertEqual(SessionStore(session_key=key)['k'], 'v')

    def test_a_broken_cache_falls_back_to_the_database(self):
        key = self._saved()
        cache = caches['shared']
        with patch.object(cache, 'get', side_effect=ConnectionError), \
                patch.object(cache, 'set', side_effect=ConnectionError), \
                patch.object(cache, 'delete', side_effect=ConnectionError), \
                patch.object(cache, 'has_key', side_effect=ConnectionError):
            store = SessionStore(session_key=key)
            self.assertEqual(store['k'], 'v')
            store['k'] = 'w'
            store.save()
            self.assertTrue(store.exists(key))
            store.delete()
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_logout_removes_the_cached_copy(self):
        key = self._saved()
        SessionStore(session_key=key).delete()
        self.assertEqual(SessionStore(session_key=key).load(), {})

    def test_cached_copies_live_at_most_a_day(self):
        store = SessionStore()
        self.assertEqual(store._cache_ttl(90 * 24 * 3600), CACHE_TTL_SECONDS)
        self.assertEqual(store._cache_ttl(60), 60)
