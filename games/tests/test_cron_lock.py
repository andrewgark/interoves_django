from unittest.mock import patch

from django.test import SimpleTestCase


class _FakeRedis:
    def __init__(self, result=True):
        self.result = result
        self.set_calls = []
        self.eval_calls = []

    def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))
        return self.result

    def eval(self, *args):
        self.eval_calls.append(args)
        return 1


class DistributedCronLockTests(SimpleTestCase):
    def test_acquired_lease_uses_ttl_and_compare_delete(self):
        from games.cron_lock import distributed_cron_lock

        redis = _FakeRedis()
        with patch('games.cron_lock._redis_client_and_key', return_value=(redis, 'k')):
            with distributed_cron_lock('job', ttl_seconds=123) as acquired:
                self.assertTrue(acquired)

        self.assertEqual(redis.set_calls[0][0][0], 'k')
        self.assertEqual(redis.set_calls[0][1], {'nx': True, 'ex': 123})
        self.assertEqual(len(redis.eval_calls), 1)
        self.assertEqual(redis.eval_calls[0][1], 1)

    def test_held_lease_skips_and_does_not_release(self):
        from games.cron_lock import distributed_cron_lock

        redis = _FakeRedis(result=False)
        with patch('games.cron_lock._redis_client_and_key', return_value=(redis, 'k')):
            with distributed_cron_lock('job', ttl_seconds=123) as acquired:
                self.assertFalse(acquired)

        self.assertEqual(redis.eval_calls, [])

    def test_exception_releases_owned_lease(self):
        from games.cron_lock import distributed_cron_lock

        redis = _FakeRedis()
        with patch('games.cron_lock._redis_client_and_key', return_value=(redis, 'k')):
            with self.assertRaises(RuntimeError):
                with distributed_cron_lock('job', ttl_seconds=123):
                    raise RuntimeError('boom')

        self.assertEqual(len(redis.eval_calls), 1)

    def test_non_mysql_without_redis_keeps_local_test_behaviour(self):
        from games.cron_lock import distributed_cron_lock

        with (
            patch('games.cron_lock._redis_client_and_key', return_value=(None, None)),
            patch('games.cron_lock.connection.vendor', 'sqlite'),
        ):
            with distributed_cron_lock('job', ttl_seconds=123) as acquired:
                self.assertTrue(acquired)
