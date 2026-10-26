import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache, caches
from django.test import TestCase
from django.utils import timezone

from games.daily.timing.statistics import (
    _RELEASE_CACHE_LOCK_SCRIPT,
    CACHE_BUILD_LOCK_TIMEOUT,
    CACHE_TIMEOUT,
    REVISION_TIMEOUT,
    _release_build_lock,
    _versioned_cache_key,
    build_attempt_histogram,
    build_daily_statistics,
    cache_key,
    invalidate_daily_statistics,
)
from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    HiddenAnonKey,
    PlayerCompletedGame,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.puzzles.censorly.text.normalize import lemma_of


class DailyStatisticsTests(TestCase):
    def setUp(self):
        cache.clear()
        caches['daily_statistics'].clear()
        self.project, _ = Project.objects.get_or_create(pk='sections')
        self.game = Game.objects.filter(id='alphabetty', project=self.project).first()
        if self.game is None:
            self.game = Game.objects.create(id='alphabetty', name='Алфавитка', project=self.project)
        self.users = [User.objects.create_user('stats{}'.format(i)) for i in range(3)]

    def test_cache_invalidation_waits_for_database_commit(self):
        revision_key = '{}:revision'.format(cache_key(self.game.id, 12345))
        daily_cache = caches['daily_statistics']

        with patch.object(daily_cache, 'add', wraps=daily_cache.add) as cache_add:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                invalidate_daily_statistics(self.game.id, 12345)
                self.assertIsNone(daily_cache.get(revision_key))

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(daily_cache.get(revision_key), 1)
        cache_add.assert_called_once_with(revision_key, 1, timeout=REVISION_TIMEOUT)

        with patch.object(daily_cache, 'touch', wraps=daily_cache.touch) as cache_touch:
            with self.captureOnCommitCallbacks(execute=True):
                invalidate_daily_statistics(self.game.id, 12345)

        self.assertEqual(daily_cache.get(revision_key), 2)
        cache_touch.assert_called_once_with(revision_key, REVISION_TIMEOUT)

    @patch.dict(os.environ, {
        'PROJECTION_REFRESH_EVENTS': '1',
        'PROJECTION_REFRESH_SQS_QUEUE_URL': 'https://example/interoves-background',
    })
    @patch('games.realtime.projection.boto3.client')
    def test_completion_coalesces_a_background_statistics_refresh(self, client):
        from games.realtime.projection import _cache, _daily_statistics_mark_key

        tg = TaskGroup.objects.create(label='statistics refresh event')
        _cache().delete(_daily_statistics_mark_key(self.game.id, tg.id))
        with self.captureOnCommitCallbacks(execute=True):
            invalidate_daily_statistics(self.game.id, tg.id)
            invalidate_daily_statistics(self.game.id, tg.id)

        send = client.return_value.send_message
        self.assertEqual(send.call_count, 1)
        body = json.loads(send.call_args.kwargs['MessageBody'])
        self.assertEqual(body['type'], 'daily_statistics.refresh')
        self.assertEqual(body['payload']['game_id'], self.game.id)
        self.assertEqual(body['payload']['task_group_id'], tg.id)
        # The first event carries the generation that caused it; the worker
        # always reads the latest revision before publishing the snapshot.
        self.assertEqual(body['payload']['revision'], 1)

    def test_worker_refresh_warms_the_current_statistics_generation(self):
        from games.daily.timing.statistics import _cache_revision_key, _versioned_cache_key
        from games.realtime.projection import (
            _cache,
            _daily_statistics_mark_key,
            run_named_daily_statistics_refresh,
        )

        tg = TaskGroup.objects.create(label='statistics worker refresh')
        _cache().set(_daily_statistics_mark_key(self.game.id, tg.id), '1', timeout=60)
        status = run_named_daily_statistics_refresh(
            game_id=self.game.id, task_group_id=tg.id,
        )

        revision = caches['daily_statistics'].get(
            _cache_revision_key(self.game.id, tg.id), 0,
        )
        self.assertEqual(status, 'ok')
        self.assertIsNotNone(caches['daily_statistics'].get(
            _versioned_cache_key(self.game.id, tg.id, revision),
        ))
        self.assertIsNone(_cache().get(_daily_statistics_mark_key(self.game.id, tg.id)))

    def test_cache_revision_churn_without_miss_falls_back_to_recompute(self):
        tg = TaskGroup.objects.create(label='revision churn')
        daily_cache = caches['daily_statistics']
        revisions = iter((0, 1, 1, 2, 2, 3))

        def cache_get(key, default=None):
            if key.endswith(':revision'):
                return next(revisions)
            return {'cached': True}

        with patch.object(daily_cache, 'get', side_effect=cache_get):
            with patch(
                'games.daily.timing.statistics._build_daily_statistics_payload',
                return_value={'fresh': True},
            ) as build_payload:
                result = build_daily_statistics(self.game, tg)

        self.assertEqual(result, {'fresh': True})
        build_payload.assert_called_once_with(self.game, tg, published_at=None)

    def test_prepublication_timing_is_excluded_and_cached_separately(self):
        tg, _task = self._task('alphabetty')
        user = self.users[0]
        self._complete(tg, user)
        published_at = timezone.now()
        timing = DailySolveTiming.objects.create(
            user=user,
            game=self.game,
            task_group=tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=5000,
            frozen_ms=5000,
        )
        DailySolveTiming.objects.filter(pk=timing.pk).update(
            created_at=published_at - timedelta(seconds=1),
        )

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 1)
        self.assertEqual(
            build_daily_statistics(self.game, tg, published_at=published_at)['solved'],
            0,
        )

    def test_prepublication_first_attempt_is_legacy_eligibility_fallback(self):
        tg, task = self._task('alphabetty')
        user = self.users[0]
        self._complete(tg, user)
        published_at = timezone.now()
        attempt = Attempt.manager.create(
            user=user,
            game=self.game,
            task=task,
            text='СЛОВО',
            status='Ok',
            state=json.dumps({'guesses': ['СЛОВО'], 'won': True}),
        )
        Attempt.manager.filter(pk=attempt.pk).update(
            time=published_at - timedelta(seconds=1),
        )

        self.assertEqual(
            build_daily_statistics(self.game, tg, published_at=published_at)['solved'],
            0,
        )

    def test_concurrent_cache_misses_share_one_statistics_build(self):
        tg = TaskGroup.objects.create(label='concurrent cache miss')
        build_started = Event()
        build_count_lock = Lock()
        build_count = 0

        def slow_build(_game, _task_group, *, published_at=None):
            nonlocal build_count
            with build_count_lock:
                build_count += 1
            build_started.set()
            # Longer than the former 100 ms miss wait: a second request must
            # not start another full aggregate while the first is working.
            time.sleep(0.25)
            return {'fresh': True}

        with patch(
            'games.daily.timing.statistics._build_daily_statistics_payload',
            side_effect=slow_build,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(build_daily_statistics, self.game, tg)
                self.assertTrue(build_started.wait(timeout=1))
                second = executor.submit(build_daily_statistics, self.game, tg)
                results = [first.result(timeout=2), second.result(timeout=2)]

        self.assertEqual(build_count, 1)
        self.assertEqual(results, [{'fresh': True}, {'fresh': True}])

    def test_cache_waiter_serves_last_generation_while_refresh_is_running(self):
        tg = TaskGroup.objects.create(label='stale statistics generation')
        daily_cache = caches['daily_statistics']
        revision_key = '{}:revision'.format(cache_key(self.game.id, tg.id))
        daily_cache.set(revision_key, 1, timeout=REVISION_TIMEOUT)
        daily_cache.set(
            _versioned_cache_key(self.game.id, tg.id, 0),
            {'kind': 'alphabet', 'solved': 2},
            timeout=CACHE_TIMEOUT,
        )
        result_key = _versioned_cache_key(self.game.id, tg.id, 1)
        daily_cache.set(
            '{}:build-lock'.format(result_key), 'another-builder',
            timeout=CACHE_BUILD_LOCK_TIMEOUT,
        )

        with patch(
            'games.daily.timing.statistics._build_daily_statistics_payload',
        ) as build_payload:
            result = build_daily_statistics(self.game, tg)

        self.assertEqual(result, {
            'kind': 'alphabet', 'solved': 2, '_stale': True,
        })
        build_payload.assert_not_called()

    def test_redis_build_lock_release_uses_atomic_token_check(self):
        redis_client = Mock()
        backend = SimpleNamespace(
            get_client=Mock(return_value=redis_client),
            _serializer=SimpleNamespace(dumps=Mock(return_value=b'encoded-token')),
        )
        cache_backend = SimpleNamespace(
            _cache=backend,
            make_key=lambda key: 'cache-prefix:{}'.format(key),
        )

        _release_build_lock(cache_backend, 'daily:build-lock', 'token')

        redis_client.eval.assert_called_once_with(
            _RELEASE_CACHE_LOCK_SCRIPT,
            1,
            'cache-prefix:daily:build-lock',
            b'encoded-token',
        )

    def test_hiding_profile_invalidates_cached_daily_statistics(self):
        tg, _task = self._task('alphabetty')
        user = self.users[0]
        profile = Profile.objects.create(user=user, first_name='Player', last_name='One')
        self._complete(tg, user)

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            profile.is_hidden = True
            profile.save(update_fields=['is_hidden'])

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 0)

    def test_creating_hidden_profile_invalidates_cached_daily_statistics(self):
        tg, _task = self._task('alphabetty')
        user = self.users[0]
        self._complete(tg, user)

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            Profile.objects.create(
                user=user,
                first_name='Hidden',
                last_name='Player',
                is_hidden=True,
            )

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 0)

    def test_changing_task_authors_invalidates_cached_daily_statistics(self):
        tg, _task = self._task('alphabetty')
        user = self.users[0]
        profile = Profile.objects.create(user=user, first_name='Author', last_name='Player')
        self._complete(tg, user)

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            tg.authors.add(profile)

        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 0)

    def test_hiding_team_and_anonymous_actor_invalidates_statistics(self):
        tg, _task = self._task('alphabetty')
        team = Team.objects.create(name='hidden-stats-team')
        PlayerCompletedGame.objects.create(
            team=team, game=self.game, task_group=tg, game_kind='alphabet',
            game_instance_id='alphabetty:{}:team'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 1)

        with self.captureOnCommitCallbacks(execute=True):
            team.is_hidden = True
            team.save(update_fields=['is_hidden'])
        self.assertEqual(build_daily_statistics(self.game, tg)['solved'], 0)

        tg2, _task2 = self._task('alphabetty')
        anon_key = 'daily-stats-hidden-anon'
        PlayerCompletedGame.objects.create(
            anon_key=anon_key, game=self.game, task_group=tg2, game_kind='alphabet',
            game_instance_id='alphabetty:{}:anon'.format(tg2.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        self.assertEqual(build_daily_statistics(self.game, tg2)['solved'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            HiddenAnonKey.objects.create(anon_key=anon_key)
        self.assertEqual(build_daily_statistics(self.game, tg2)['solved'], 0)

    def _task(self, task_type, answer='СЛОВО', checker_data=None):
        tg = TaskGroup.objects.create(label='stats')
        GameTaskGroup.objects.create(game=self.game, task_group=tg, number=str(tg.pk), name='stats')
        checker, _ = CheckerType.objects.get_or_create(pk=task_type)
        task = Task.objects.create(
            task_group=tg, number='1', task_type=task_type,
            checker=checker, answer=answer, checker_data=checker_data,
        )
        return tg, task

    def _complete(self, tg, user):
        PlayerCompletedGame.objects.create(
            user=user, game=self.game, task_group=tg, game_kind='alphabet',
            game_instance_id='alphabetty:{}'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

    def test_alphabet_distribution_and_unique_popular_guesses(self):
        tg, task = self._task('alphabetty')
        for user, guesses in zip(self.users, (['А', 'Б', 'СЛОВО'], ['А', 'СЛОВО'], ['В', 'Г', 'Д', 'СЛОВО'])):
            for index, guess in enumerate(guesses):
                Attempt.manager.create(
                    user=user, game=self.game, task=task, text=guess,
                    status='Ok' if guess == 'СЛОВО' else 'Partial',
                    state=json.dumps({'guesses': guesses, 'won': guess == 'СЛОВО'}),
                )
            self._complete(tg, user)
        for user, frozen_ms in zip(self.users, (2000, 5000, 9000)):
            DailySolveTiming.objects.create(
                user=user, game=self.game, task_group=tg,
                status=DailySolveTiming.STATUS_COMPLETED, frozen_ms=frozen_ms,
            )
        data = build_daily_statistics(self.game, tg)
        self.assertEqual(data['summary']['median_attempts'], 3)
        self.assertEqual(data['summary']['median_time_seconds'], 5.0)
        self.assertEqual(len(data['distribution']), 30)
        self.assertEqual([row['count'] for row in data['distribution'][:4]], [0, 1, 1, 1])
        self.assertEqual(data['distribution'][-1]['label'], '30+')
        self.assertEqual(data['guesses'], [])
        self.assertNotIn('СЛОВО', {row['word'] for row in data['guesses']})
        self.assertEqual(data['summary']['without_hints_percent'], 100.0)

    def test_alphabet_bound_words_aggregate_nearest_ladder_bounds(self):
        tg, task = self._task('alphabetty')
        users = [User.objects.create_user('bound{}'.format(i)) for i in range(5)]
        for user in users:
            for guess in ('Б', 'СЛОВО'):
                Attempt.manager.create(
                    user=user, game=self.game, task=task, text=guess,
                    status='Ok' if guess == 'СЛОВО' else 'Partial',
                    state=json.dumps({'guesses': ['Б', 'СЛОВО'], 'won': guess == 'СЛОВО'}),
                )
            self._complete(tg, user)
        extra = [User.objects.create_user('bound_below{}'.format(i)) for i in range(5)]
        for user in extra:
            for guess in ('Я', 'СЛОВО'):
                Attempt.manager.create(
                    user=user, game=self.game, task=task, text=guess,
                    status='Ok' if guess == 'СЛОВО' else 'Partial',
                    state=json.dumps({'guesses': ['Я', 'СЛОВО'], 'won': guess == 'СЛОВО'}),
                )
            self._complete(tg, user)
        data = build_daily_statistics(self.game, tg)
        self.assertEqual(data['above_words'], [{'word': 'Б', 'players': 5}])
        self.assertEqual(data['below_words'], [{'word': 'Я', 'players': 5}])

    def test_alphabet_without_hints_percent_counts_completed_players(self):
        tg, task = self._task('alphabetty')
        for index, user in enumerate(self.users):
            guesses = ['А', 'СЛОВО']
            for guess_index, guess in enumerate(guesses):
                Attempt.manager.create(
                    user=user, game=self.game, task=task, text=guess,
                    status='Ok' if guess == 'СЛОВО' else 'Partial',
                    state=json.dumps({
                        'guesses': guesses, 'won': guess == 'СЛОВО',
                        'hints_taken': 1 if index == 0 else 0,
                    }),
                )
            self._complete(tg, user)
        data = build_daily_statistics(self.game, tg)
        self.assertEqual(data['summary']['without_hints_percent'], 66.7)

    def test_alphabet_histogram_always_keeps_1_to_30_plus_scale(self):
        data = build_attempt_histogram([2, 3, 5])
        self.assertEqual(len(data), 30)
        self.assertEqual([row['label'] for row in data[:5]], ['1', '2', '3', '4', '5'])
        self.assertEqual([row['count'] for row in data[:5]], [0, 1, 1, 0, 1])
        self.assertEqual(data[-1]['label'], '30+')
        self.assertEqual(data[-1]['count'], 0)

    def test_alphabet_histogram_keeps_empty_edges_and_internal_zeros(self):
        data = build_attempt_histogram(range(1, 9))
        self.assertEqual(len(data), 30)
        self.assertEqual(data[0]['count'], 1)
        self.assertEqual(data[28]['count'], 0)
        self.assertEqual(data[29]['count'], 0)

    def test_alphabet_histogram_keeps_granularity_and_folds_rare_tail(self):
        values = [1] + [2] * 5 + [3] * 18 + [4] * 34 + [5] * 29 + [6] * 16 + [7] * 8 + [8] * 4 + [9] * 2 + [10, 14]
        data = build_attempt_histogram(values)
        self.assertEqual(len(data), 30)
        self.assertEqual(data[-1]['label'], '30+')
        self.assertEqual(data[-1]['count'], 0)
        self.assertEqual(sum(row['percent'] for row in data), 100.0)

        outlier_data = build_attempt_histogram(values + [70])
        self.assertEqual(outlier_data[-1]['label'], '30+')
        self.assertEqual(outlier_data[-1]['count'], 1)
        self.assertEqual(outlier_data[-1]['to'], None)

    def test_alphabet_histogram_aggregates_the_30_plus_tail(self):
        data = build_attempt_histogram([1] * 10 + list(range(2, 51)))
        self.assertEqual(len(data), 30)
        self.assertEqual(data[-1]['label'], '30+')
        self.assertEqual(data[-1]['count'], 21)

    def test_alphabet_histogram_starts_at_one_even_when_minimum_is_high(self):
        data = build_attempt_histogram([9, 10, 12])
        self.assertEqual([row['label'] for row in data[:12]], [str(value) for value in range(1, 13)])

    def test_alphabet_histogram_handles_empty_single_and_deterministic_input(self):
        self.assertEqual(build_attempt_histogram([]), [])
        data = build_attempt_histogram([4])
        self.assertEqual(len(data), 30)
        self.assertEqual(data[3]['count'], 1)
        self.assertEqual(data[3]['percent'], 100.0)
        self.assertEqual(data[3]['bar_percent'], 100.0)
        values = [1, 2, 2, 3, 9, 20]
        self.assertEqual(build_attempt_histogram(values), build_attempt_histogram(reversed(values)))

    def test_population_is_completed_players_and_salad_hint_rate(self):
        game = Game.objects.filter(id='salad', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='salad', name='Салатик', project=self.project)
        tg = TaskGroup.objects.create(label='salad stats')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='salad')
        checker, _ = CheckerType.objects.get_or_create(pk='word_salad')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='word_salad', checker=checker,
            checker_data=json.dumps({
                'grid': list('ABCDEFGHIJKLMNOP'), 'words': ['ABCD', 'EFGH'],
                'longest_missing_words': ['ДЛИННОЕ', 'СЛЕДУЮЩЕЕ'],
            }),
        )
        for user, state in zip(self.users[:2], (
            {'solved_indices': [0, 1], 'hint_counts': {}, 'found_extra': [], 'found_rare_words': []},
            {'solved_indices': [0, 1], 'hint_counts': {'1': 1}, 'found_extra': ['ДЛИННОЕ'], 'found_rare_words': []},
        )):
            Attempt.manager.create(
                user=user, game=game, task=task, text=json.dumps({'action': 'solve', 'path': [0]}),
                status='Ok', state=json.dumps(state), active_time_ms=1000,
            )
            PlayerCompletedGame.objects.create(
                user=user, game=game, task_group=tg, game_kind='salad',
                game_instance_id='salad:{}'.format(tg.pk), result=PlayerCompletedGame.RESULT_SOLVED,
            )
        data = build_daily_statistics(game, tg)
        self.assertEqual(data['solved'], 2)
        self.assertEqual(data['words'][0]['average_order'], 1.0)
        self.assertEqual(data['words'][1]['average_order'], 2.0)
        self.assertEqual(data['words'][1]['hint_percent'], 50.0)
        self.assertEqual(data['off_topic'][0]['players'], 1)
        self.assertEqual(data['popular_findings'], [])
        self.assertEqual({row['kind'] for row in data['long_found']}, {'answer', 'extra'})
        self.assertEqual([row['word'] for row in data['long_missing']], ['СЛЕДУЮЩЕЕ'])
        self.assertEqual(
            [row['word'] for row in data['long_words']],
            ['СЛЕДУЮЩЕЕ', 'ДЛИННОЕ', 'ABCD', 'EFGH'],
        )

    def test_censorly_popular_words_include_single_player_words(self):
        game = Game.objects.filter(id='censorly', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='censorly', name='Цензурка', project=self.project)
        tg = TaskGroup.objects.create(label='censorly stats')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='censorly')
        checker, _ = CheckerType.objects.get_or_create(pk='censorly')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='censorly', checker=checker,
            checker_data=json.dumps({
                'title': 'Тест',
                'text': 'Текст статьи.',
                'guesses': [],
            }),
        )
        user = self.users[0]
        Attempt.manager.create(
            user=user, game=game, task=task, text='слово', status='Ok',
            state=json.dumps({'guesses': [{'lemma': 'слово'}], 'won': True}),
        )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='censorly',
            game_instance_id='censorly:{}'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

        data = build_daily_statistics(game, tg)

        self.assertEqual(data['popular_words'], [{'word': 'слово', 'players': 1}])

    def _censorly_task(self, label):
        game = Game.objects.filter(id='censorly', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='censorly', name='Цензурка', project=self.project)
        tg = TaskGroup.objects.create(label=label)
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='censorly')
        checker, _ = CheckerType.objects.get_or_create(pk='censorly')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='censorly', checker=checker,
            checker_data=json.dumps({'title': 'Тест', 'text': 'Текст статьи.', 'guesses': []}),
        )
        return game, tg, task

    def _censorly_solve(self, game, tg, task, user, rows):
        for text, status, state in rows:
            Attempt.manager.create(
                user=user, game=game, task=task, text=text, status=status, state=state,
            )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='censorly',
            game_instance_id='censorly:{}'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

    def test_censorly_lemmas_come_from_attempt_state_without_loading_it(self):
        from games.daily.timing import statistics as daily_statistics

        game, tg, task = self._censorly_task('censorly state lemmas')
        history = [{'lemma': 'кошка'}]
        self._censorly_solve(game, tg, task, self.users[0], [
            # The resolver lemma wins over the typed surface.
            ('кошек', 'Wrong', json.dumps({'guesses': history})),
            # Unreadable states fall back to the typed word.
            ('собаки', 'Wrong', '{not json'),
            ('столы', 'Wrong', json.dumps({'guesses': []})),
            ('окна', 'Partial', json.dumps({'guesses': [{'lemma': None}]})),
            ('реки', 'Ok', json.dumps({'guesses': history + [{'lemma': 'река'}], 'won': True})),
        ])
        ChainTaskState.objects.create(
            user=self.users[0], game=game, task=task, game_mode='general',
            state=json.dumps({'guesses': history, 'won': True}),
        )
        loaded = []
        grouped = daily_statistics._grouped_by_actor

        def spy(rows):
            rows = list(rows)
            loaded.extend(rows)
            return grouped(rows)

        with patch.object(daily_statistics, '_grouped_by_actor', side_effect=spy):
            data = build_daily_statistics(game, tg)

        self.assertTrue(loaded)
        self.assertTrue(all('state' in row.get_deferred_fields() for row in loaded))
        words = {row['word'] for row in data['popular_words']}
        self.assertTrue({'кошка', 'река'} <= words)
        self.assertNotIn('кошек', words)
        self.assertEqual(data['last_words'], [{'word': lemma_of('окна'), 'players': 1}])

    def test_censorly_actor_without_chain_state_uses_latest_attempt_state(self):
        from games.puzzles.censorly.play import dump_state

        game, tg, task = self._censorly_task('censorly no chain state')
        self._censorly_solve(game, tg, task, self.users[0], [
            ('#hint:кошка', 'Partial', dump_state({'guesses': [], 'hints_taken': 1})),
            ('кошка', 'Ok', dump_state({'guesses': [{'lemma': 'кошка'}], 'hints_taken': 1, 'won': True})),
        ])
        self.assertFalse(ChainTaskState.objects.filter(task=task).exists())

        data = build_daily_statistics(game, tg)

        self.assertEqual(data['summary']['without_hints_percent'], 0.0)

    def test_censorly_attempt_distribution_includes_wrong_guesses(self):
        game = Game.objects.filter(id='censorly', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='censorly', name='Цензурка', project=self.project)
        tg = TaskGroup.objects.create(label='censorly nine attempts')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='censorly')
        checker, _ = CheckerType.objects.get_or_create(pk='censorly')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='censorly', checker=checker,
            checker_data=json.dumps({'title': 'Тест', 'text': 'Текст статьи.', 'guesses': []}),
        )
        user = self.users[0]
        for index in range(9):
            Attempt.manager.create(
                user=user, game=game, task=task, text='слово{}'.format(index),
                status='Ok' if index == 8 else 'Wrong',
                state=json.dumps({'guesses': [{'lemma': 'слово{}'.format(index)}], 'won': index == 8}),
            )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='censorly',
            game_instance_id='censorly:{}'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

        data = build_daily_statistics(game, tg)

        self.assertEqual(len(data['distribution']), 31)
        self.assertEqual(data['distribution'][0]['label'], '1–5')
        self.assertEqual(data['distribution'][1]['from'], 6)
        self.assertEqual(data['distribution'][1]['to'], 10)
        self.assertEqual(data['distribution'][1]['count'], 1)
        self.assertEqual(data['distribution'][-1]['label'], '151+')

    def test_ladder_uses_success_order_and_active_intervals(self):
        game = Game.objects.filter(id='ladder', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='ladder', name='Лесенка', project=self.project)
        tg = TaskGroup.objects.create(label='ladder stats')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='ladder')
        checker, _ = CheckerType.objects.get_or_create(pk='raddle')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='raddle', checker=checker,
            checker_data=json.dumps({'lengths': [1, 1, 1, 1], 'hints': ['a', 'b', 'c'], 'words': ['А', 'Б', 'В', 'Г']}),
        )
        user = self.users[0]
        for solved, elapsed in (([0, 1], 1000), ([0, 1, 2], 3000), ([0, 1, 2, 3], 7000)):
            Attempt.manager.create(
                user=user, game=game, task=task, text=json.dumps({'word_index': solved[-1], 'word': 'x'}),
                status='Partial' if len(solved) < 4 else 'Ok',
                state=json.dumps({'solved_indices': solved, 'assist_tier': {}}),
                active_time_ms=elapsed,
            )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='ladder',
            game_instance_id='ladder:{}'.format(tg.pk), result=PlayerCompletedGame.RESULT_SOLVED,
        )
        DailySolveTiming.objects.create(
            user=user, game=game, task_group=tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            frozen_ms=4000,
        )
        data = build_daily_statistics(game, tg)
        self.assertEqual(data['summary']['median_time_seconds'], 4.0)
        self.assertTrue(data['word_stats_available'])
        self.assertEqual([row['median_time_seconds'] for row in data['words'][1:3]], [1.0, 2.0])
        self.assertEqual([row['hint_percent'] for row in data['words']], [0, 0, 0, 0])
        self.assertEqual([row['last_percent'] for row in data['words']], [0, 0, 100.0, 0])

    def test_ladder_hides_word_stats_when_one_word_has_no_active_timing(self):
        game = Game.objects.filter(id='ladder', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='ladder', name='Лесенка', project=self.project)
        tg = TaskGroup.objects.create(label='ladder missing timing')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='ladder')
        checker, _ = CheckerType.objects.get_or_create(pk='raddle')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='raddle', checker=checker,
            checker_data=json.dumps({'lengths': [1, 1, 1, 1], 'hints': ['a', 'b', 'c'], 'words': ['А', 'Б', 'В', 'Г']}),
        )
        user = self.users[0]
        for solved, elapsed in (([0, 1], 1000), ([0, 1, 2], None), ([0, 1, 2, 3], 7000)):
            Attempt.manager.create(
                user=user, game=game, task=task, text=json.dumps({'word_index': solved[-1], 'word': 'x'}),
                status='Partial' if len(solved) < 4 else 'Ok',
                state=json.dumps({'solved_indices': solved, 'assist_tier': {}}),
                active_time_ms=elapsed,
            )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='ladder',
            game_instance_id='ladder:{}'.format(tg.pk), result=PlayerCompletedGame.RESULT_SOLVED,
        )
        data = build_daily_statistics(game, tg)
        self.assertFalse(data['word_stats_available'])

    def test_ladder_accepts_integer_assist_tier_keys(self):
        game = Game.objects.filter(id='ladder', project=self.project).first()
        if game is None:
            game = Game.objects.create(id='ladder', name='Лесенка', project=self.project)
        tg = TaskGroup.objects.create(label='ladder integer tiers')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='ladder')
        checker, _ = CheckerType.objects.get_or_create(pk='raddle')
        task = Task.objects.create(
            task_group=tg, number='1', task_type='raddle', checker=checker,
            checker_data=json.dumps({'lengths': [1, 1, 1], 'hints': ['a', 'b'], 'words': ['А', 'Б', 'В']}),
        )
        user = self.users[0]
        for solved, elapsed, state in (
            ([0, 1], 1000, {'solved_indices': [0, 1], 'assist_tier': {1: 0}}),
            ([0, 1, 2], 3000, {'solved_indices': [0, 1, 2], 'assist_tier': {1: 0}}),
        ):
            Attempt.manager.create(
                user=user, game=game, task=task,
                text=json.dumps({'word_index': solved[-1], 'word': 'x'}),
                status='Ok' if len(solved) == 3 else 'Partial', state=json.dumps(state),
                active_time_ms=elapsed,
            )
        PlayerCompletedGame.objects.create(
            user=user, game=game, task_group=tg, game_kind='ladder',
            game_instance_id='ladder:{}'.format(tg.pk), result=PlayerCompletedGame.RESULT_SOLVED,
        )
        data = build_daily_statistics(game, tg)
        self.assertEqual(data['words'][1]['median_time_seconds'], 1.0)

    def test_hidden_profiles_and_task_authors_are_excluded(self):
        tg, task = self._task('alphabetty')
        visible = self.users[0]
        hidden_user = self.users[1]
        author = self.users[2]
        Profile.objects.create(user=visible, first_name='Visible', last_name='Player')
        Profile.objects.create(user=hidden_user, first_name='Hidden', last_name='Player', is_hidden=True)
        author_profile = Profile.objects.create(user=author, first_name='Author', last_name='Player')
        tg.authors.add(author_profile)
        author_team = Team.objects.create(name='author-team')
        author_profile.add_team_membership(team=author_team, make_primary=True)
        for user in (visible, hidden_user, author):
            Attempt.manager.create(
                user=user, game=self.game, task=task, text='СЛОВО', status='Ok',
                state=json.dumps({'guesses': ['СЛОВО'], 'won': True}),
            )
            self._complete(tg, user)
        Attempt.manager.create(
            team=author_team, game=self.game, task=task, text='СЛОВО', status='Ok',
            state=json.dumps({'guesses': ['СЛОВО'], 'won': True}),
        )
        PlayerCompletedGame.objects.create(
            team=author_team, game=self.game, task_group=tg, game_kind='alphabet',
            game_instance_id='alphabetty:{}:team'.format(tg.pk),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        data = build_daily_statistics(self.game, tg)
        self.assertEqual(data['summary']['solved'], 1)
        self.assertEqual(data['summary']['median_attempts'], 1)

    @patch('games.club.access.archive.reject_if_club_archive_blocked', return_value=None)
    @patch('games.site.pages.results.pages.scheduled_number_is_public', return_value=True)
    def test_results_and_statistics_explain_why_the_viewer_is_omitted(self, _public, _club):
        tg, task = self._task('alphabetty')
        author = self.users[0]
        profile = Profile.objects.create(user=author, first_name='Author', last_name='Player')
        tg.authors.add(profile)
        self._complete(tg, author)
        Attempt.manager.create(
            user=author, game=self.game, task=task, text='СЛОВО', status='Ok',
            state=json.dumps({'guesses': ['СЛОВО'], 'won': True}),
        )
        self.client.force_login(author)
        results = self.client.get('/alphabetty/{}/results/'.format(tg.pk))
        self.assertContains(
            results, 'Вы не видны в результатах, потому что вы автор этого задания.',
        )
        stats = self.client.get('/daily-statistics/alphabetty/{}/'.format(tg.pk))
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(
            stats.json()['exclusion_notice'],
            'Вы не учтены в статистике, потому что вы автор этого задания.',
        )
