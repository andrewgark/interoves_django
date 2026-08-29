import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from games.difficulty import (
    _public_context,
    calculate_observed_metrics,
    difficulty_refresh_interval,
    get_cached_game_difficulties,
    get_game_difficulty,
    historical_norm,
    is_difficulty_refresh_due,
    mark_game_difficulty_changed,
    persist_difficulty_snapshot,
    rate_difficulty_metrics,
    refresh_due_daily_difficulties,
    refresh_historical_norm,
    refresh_interval_for_age,
    retry_delay_for_fail_count,
)
from games.difficulty_refresh import (
    ClaimedDifficultyRefresh,
    claim_due_daily_difficulties,
    refresh_claimed_difficulty,
)
from games.models import (
    Attempt,
    CheckerType,
    DailyGameDifficulty,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
)


def _write_snapshot(placement, **defaults):
    DailyGameDifficulty.objects.update_or_create(placement=placement, defaults=defaults)
    return DailyGameDifficulty.objects.get(placement=placement)


def _metrics(**overrides):
    values = {
        'n': 100,
        'median_time': 100.0,
        'median_errors': 3.0,
        'help_rate': 0.20,
        'unfinished_rate': 0.20,
        'completed_n': 90,
        'unfinished_denominator': 100,
        'error_available': True,
    }
    values.update(overrides)
    return values


def _norm(**overrides):
    values = {
        'median_time': 100.0,
        'median_errors': 3.0,
        'help_rate': 0.20,
        'unfinished_rate': 0.20,
        'sources': {},
    }
    values.update(overrides)
    return values


class DifficultyScoringTests(SimpleTestCase):
    def test_n_below_five_is_not_public(self):
        rating = rate_difficulty_metrics('ladder', _metrics(n=4), _norm())
        result = {'n': 4, **rating}
        self.assertFalse(result['is_visible'])
        self.assertIsNone(_public_context(result))

    def test_n_five_shrinks_extreme_rating_to_three(self):
        rating = rate_difficulty_metrics(
            'ladder',
            _metrics(n=5, median_time=1000, median_errors=100, help_rate=1, unfinished_rate=1),
            _norm(),
        )
        self.assertEqual(rating['raw_rating'], 5)
        self.assertEqual(rating['adjusted_rating'], 4)
        self.assertTrue(rating['is_preliminary'])
        self.assertEqual(rating['stars'], 4)

    def test_fast_simple_game_gets_low_rating(self):
        rating = rate_difficulty_metrics(
            'ladder',
            _metrics(median_time=10, median_errors=0, help_rate=0, unfinished_rate=0),
            _norm(median_errors=5),
        )
        self.assertLess(rating['adjusted_rating'], 1.2)
        self.assertEqual(rating['stars'], 1)

    def test_slow_error_prone_game_gets_high_rating(self):
        rating = rate_difficulty_metrics(
            'alphabetty',
            _metrics(median_time=500, median_errors=20, help_rate=0.8, unfinished_rate=0.8),
            _norm(),
        )
        self.assertGreater(rating['adjusted_rating'], 4.8)
        self.assertEqual(rating['stars'], 5)

    def test_missing_component_is_removed_and_weights_are_renormalized(self):
        metrics = _metrics(median_errors=None, error_available=False)
        rating = rate_difficulty_metrics('salad', metrics, _norm(median_errors=None))
        self.assertNotIn('errors', rating['weights'])
        self.assertAlmostEqual(sum(rating['weights'].values()), 1.0)
        self.assertEqual(set(rating['weights']), {'time', 'help', 'unfinished'})

    def test_preliminary_status_ends_at_ten(self):
        self.assertTrue(rate_difficulty_metrics('ladder', _metrics(n=9), _norm())['is_preliminary'])
        self.assertFalse(rate_difficulty_metrics('ladder', _metrics(n=10), _norm())['is_preliminary'])

    def test_adjusted_value_is_always_in_range(self):
        for n in (0, 1, 5, 10, 100000):
            rating = rate_difficulty_metrics('ladder', _metrics(n=n), _norm())
            self.assertGreaterEqual(rating['adjusted_rating'], 1)
            self.assertLessEqual(rating['adjusted_rating'], 5)
            self.assertGreaterEqual(rating['stars'], 1)
            self.assertLessEqual(rating['stars'], 5)

    def test_public_context_uses_ui_label_and_five_brain_icons(self):
        context = _public_context({
            'n': 20,
            'stars': 2,
            'is_visible': True,
            'is_preliminary': False,
        })

        self.assertEqual(context['label'], 'Простая')
        self.assertEqual(
            context['tooltip'],
            'Сложность: Простая. Рассчитана по результатам 20 игроков.',
        )
        self.assertEqual(context['aria_label'], 'Сложность: 2 из 5 — простая.')
        self.assertEqual(context['star_slots'], [True, True, False, False, False])

        html = render_to_string(
            'new/partials/difficulty_badge.html',
            {'difficulty': context},
        )
        self.assertIn(
            'title="Сложность: Простая. Рассчитана по результатам 20 игроков."',
            html,
        )
        self.assertIn('aria-label="Сложность: 2 из 5 — простая."', html)
        self.assertIn(
            '<span class="new-difficulty__brains" aria-hidden="true">', html,
        )
        self.assertEqual(html.count('class="ph-fill ph-brain"'), 2)
        self.assertEqual(
            html.count('class="ph ph-brain new-difficulty__brain--empty"'), 3,
        )
        self.assertEqual(html.count('ph-brain'), 5)

    def test_preliminary_brain_difficulty_keeps_state_and_player_tooltip(self):
        context = _public_context({
            'n': 7,
            'stars': 4,
            'is_visible': True,
            'is_preliminary': True,
        })

        html = render_to_string(
            'new/partials/difficulty_badge.html',
            {'difficulty': context},
        )

        self.assertIn('и может измениться.', context['tooltip'])
        self.assertEqual(
            context['aria_label'],
            'Сложность: 4 из 5 — сложная. '
            'Предварительная оценка по результатам 7 игроков.',
        )
        self.assertIn('·</span> предварительно', html)
        self.assertEqual(html.count('ph-brain'), 5)


class DifficultyObservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project, _ = Project.objects.get_or_create(id='difficulty-tests')
        cls.checker, _ = CheckerType.objects.get_or_create(id='raddle')
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Difficulty ladder',
                'author': 'test',
                'project': cls.project,
                'is_tournament': False,
                'requires_ticket': False,
            },
        )
        cls.group = TaskGroup.objects.create(label='difficulty ladder')
        cls.placement = GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.group,
            number='9991',
            name='Difficulty ladder #1',
        )
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.group,
                number='1',
                task_type='raddle',
                checker=cls.checker,
                checker_data=json.dumps({
                    'lengths': [3, 3, 3],
                    'hints': ['a', 'b'],
                    'words': ['AAA', 'BBB', 'CCC'],
                }),
            )

    def _attempt(self, anon_key, when, word, status):
        attempt = Attempt.manager.create(
            anon_key=anon_key,
            task=self.task,
            game=self.game,
            task_revision=self.task.attempt_revision,
            text=json.dumps({'word_index': 1, 'word': word}),
            status=status,
            points=0,
            state=json.dumps({'solved_indices': [0, 1, 2] if status == 'Ok' else [0, 2]}),
        )
        Attempt.manager.filter(pk=attempt.pk).update(time=when)
        attempt.time = when
        return attempt

    def test_many_attempts_from_one_actor_are_one_observation(self):
        base = timezone.now() - timedelta(hours=2)
        for index in range(5):
            self._attempt('same-player', base + timedelta(seconds=index), 'XXX', 'Wrong')

        metrics = calculate_observed_metrics(self.placement, now=timezone.now())

        self.assertEqual(metrics['n'], 1)
        self.assertEqual(metrics['median_errors'], 5)

    def test_long_pause_is_capped_and_does_not_break_median(self):
        base = timezone.now() - timedelta(days=1)
        for actor, seconds in (('p1', 10), ('p2', 20), ('p3', 4 * 60 * 60)):
            self._attempt(actor, base, 'XXX', 'Wrong')
            self._attempt(actor, base + timedelta(seconds=seconds), 'BBB', 'Ok')

        metrics = calculate_observed_metrics(self.placement, now=timezone.now())

        self.assertEqual(metrics['n'], 3)
        self.assertEqual(metrics['median_time'], 20)
        self.assertEqual(metrics['median_errors'], 1)

    def test_cached_contexts_include_only_visible_difficulties(self):
        visible = {
            'n': 12,
            'stars': 4,
            'is_visible': True,
            'is_preliminary': False,
        }
        _write_snapshot(
            self.placement,
            n=12,
            stars=4,
            payload=visible,
        )

        contexts = get_cached_game_difficulties([self.placement])

        self.assertEqual(
            contexts[self.placement.pk]['tooltip'],
            'Сложность: Сложная. Рассчитана по результатам 12 игроков.',
        )

    def test_cached_contexts_use_stars_column_when_payload_omits_visibility(self):
        _write_snapshot(
            self.placement,
            n=12,
            stars=4,
            payload={'n': 12, 'metrics': {}},
        )

        contexts = get_cached_game_difficulties([self.placement])

        self.assertEqual(
            contexts[self.placement.pk]['tooltip'],
            'Сложность: Сложная. Рассчитана по результатам 12 игроков.',
        )
        self.assertEqual(contexts[self.placement.pk]['star_slots'], [True, True, True, True, False])

    def test_page_reads_do_not_calculate_missing_snapshots(self):
        DailyGameDifficulty.objects.filter(placement=self.placement).delete()
        with patch('games.difficulty.calculate_game_difficulty') as calc:
            self.assertEqual(get_cached_game_difficulties([self.placement]), {})
            self.assertIsNone(get_game_difficulty(self.placement))
        calc.assert_not_called()

    def test_refresh_interval_decays_with_age(self):
        self.assertEqual(refresh_interval_for_age(timedelta(hours=1)), timedelta(minutes=5))
        self.assertEqual(refresh_interval_for_age(timedelta(hours=10)), timedelta(minutes=15))
        self.assertEqual(refresh_interval_for_age(timedelta(days=2)), timedelta(hours=1))
        self.assertEqual(refresh_interval_for_age(timedelta(days=5)), timedelta(hours=6))
        self.assertEqual(refresh_interval_for_age(timedelta(days=10)), timedelta(hours=24))
        self.assertEqual(refresh_interval_for_age(timedelta(days=40)), timedelta(days=7))
        now = timezone.now()
        self.assertEqual(
            difficulty_refresh_interval(now - timedelta(hours=1), now),
            timedelta(minutes=5),
        )
        self.assertEqual(difficulty_refresh_interval(None, now), timedelta(days=7))

    def test_fresh_snapshot_is_not_due(self):
        now = timezone.now()
        snapshot = _write_snapshot(
            self.placement,
            n=12,
            stars=3,
            payload={'n': 12, 'stars': 3, 'is_visible': True},
            calculated_at=now,
            dirty=False,
            data_revision=1,
            calculated_revision=1,
            refresh_not_before=now + timedelta(minutes=5),
        )
        self.assertFalse(is_difficulty_refresh_due(self.placement, snapshot, now=now))

    def test_missing_snapshot_is_not_due(self):
        self.assertFalse(is_difficulty_refresh_due(self.placement, None, now=timezone.now()))

    def test_refresh_due_rebuilds_dirty_snapshot(self):
        now = timezone.now()
        _write_snapshot(
            self.placement,
            n=0,
            dirty=True,
            data_revision=1,
            calculated_revision=0,
            refresh_not_before=now,
            published_at=now - timedelta(hours=1),
        )
        visible = {
            'game_id': 'ladder',
            'placement_id': self.placement.pk,
            'number': self.placement.number,
            'n': 20,
            'stars': 2,
            'is_visible': True,
            'is_preliminary': False,
            'metrics': {},
        }
        with patch(
            'games.difficulty_refresh.calculate_game_difficulty', return_value=visible,
        ) as calc:
            results = refresh_due_daily_difficulties(
                now=now,
                limit=10,
                game_ids=('ladder',),
            )
        calc.assert_called()
        self.assertTrue(any(row['placement_id'] == self.placement.pk for row in results))

    def test_live_task_card_keeps_difficulty_badge(self):
        _write_snapshot(
            self.placement,
            n=12,
            stars=4,
            payload={
                'n': 12,
                'stars': 4,
                'is_visible': True,
                'is_preliminary': False,
            },
        )
        HTMLPage.objects.get_or_create(name='Правила Десяточки', defaults={'html': ''})
        HTMLPage.objects.get_or_create(name='Правила турнирного режима', defaults={'html': ''})
        HTMLPage.objects.get_or_create(name='Правила тренировочного режима', defaults={'html': ''})

        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        from games.views.render_task import render_new_ui_task_card_html
        html = render_new_ui_task_card_html(
            request, self.task, None, 'general', anon_key='anon_test', game=self.game,
        )

        self.assertIn('new-difficulty', html)
        self.assertIn(
            'title="Сложность: Сложная. Рассчитана по результатам 12 игроков."',
            html,
        )
        self.assertEqual(html.count('class="ph-fill ph-brain"'), 4)
        self.assertEqual(html.count('ph-brain'), 5)
        aside_at = html.find('new-proportions-compact-bar__aside')
        difficulty_at = html.find('new-difficulty')
        likes_at = html.find('new-like-dislike--compact-bar')
        self.assertNotEqual(aside_at, -1)
        self.assertLess(aside_at, difficulty_at)
        if likes_at != -1:
            self.assertLess(difficulty_at, likes_at)


class DifficultyHistoricalNormTests(TestCase):
    def _placement(self, game, number):
        group = TaskGroup.objects.create(label='{}-{}'.format(game.pk, number))
        return GameTaskGroup.objects.create(
            game=game, task_group=group, number=str(number), name=str(number),
        )

    def test_norm_does_not_mix_game_types(self):
        # Use production ids in snapshots because the service intentionally keys
        # the norm by exact daily game id.
        sections, _ = Project.objects.get_or_create(id='sections')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name)
        ladder, _ = Game.objects.get_or_create(
            id='ladder', defaults={'name': 'Ladder', 'author': 'test', 'project': sections},
        )
        alphabetty, _ = Game.objects.get_or_create(
            id='alphabetty', defaults={'name': 'Alphabetty', 'author': 'test', 'project': sections},
        )
        current = self._placement(ladder, 9901)
        ladder_history = self._placement(ladder, 9902)
        alphabet_history = self._placement(alphabetty, 9901)
        _write_snapshot(
            ladder_history,
            n=30,
            payload={'metrics': {'median_time': 100}},
        )
        _write_snapshot(
            alphabet_history,
            n=30,
            payload={'metrics': {'median_time': 1000}},
        )

        norm = historical_norm(current, _metrics(median_time=500))

        self.assertEqual(norm['median_time'], 100)

    def test_cached_norm_is_not_rescanned_on_each_rating(self):
        ladder, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Ladder',
                'author': 'test',
                'project': Project.objects.get_or_create(id='sections')[0],
            },
        )
        current = self._placement(ladder, 9910)
        history = self._placement(ladder, 9911)
        _write_snapshot(history, n=30, payload={'metrics': {'median_time': 42}})
        refresh_historical_norm('ladder')
        with patch('games.difficulty._norm_values_from_snapshots') as scan:
            norm = historical_norm(current, _metrics(median_time=10))
        scan.assert_not_called()
        self.assertEqual(norm['median_time'], 42)


def _rating_result(placement, **overrides):
    result = {
        'game_id': placement.game_id,
        'placement_id': placement.pk,
        'number': placement.number,
        'n': 20,
        'stars': 3,
        'is_visible': True,
        'is_preliminary': False,
        'metrics': _metrics(n=20),
        'typical': _norm(),
        'norm_version': 1,
    }
    result.update(overrides)
    return result


class DifficultySchedulerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project, _ = Project.objects.get_or_create(id='difficulty-tests')
        cls.checker, _ = CheckerType.objects.get_or_create(id='raddle')
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Difficulty ladder',
                'author': 'test',
                'project': cls.project,
                'is_tournament': False,
                'requires_ticket': False,
            },
        )

    def _placement(self, number):
        group = TaskGroup.objects.create(label='sched-{}'.format(number))
        placement = GameTaskGroup.objects.create(
            game=self.game,
            task_group=group,
            number=str(number),
            name='Sched {}'.format(number),
        )
        with patch('games.views.track.track_task_change'):
            Task.objects.create(
                task_group=group,
                number='1',
                task_type='raddle',
                checker=self.checker,
                checker_data=json.dumps({
                    'lengths': [3, 3, 3],
                    'hints': ['a', 'b'],
                    'words': ['AAA', 'BBB', 'CCC'],
                }),
            )
        return placement

    def _due_snapshot(self, placement, *, now, revision=1, published_at=None, **extra):
        published_at = published_at or (now - timedelta(hours=1))
        defaults = {
            'dirty': True,
            'data_revision': revision,
            'calculated_revision': 0,
            'refresh_not_before': now,
            'published_at': published_at,
            'refresh_claim_token': None,
            'refresh_claimed_until': None,
            'refresh_fail_count': 0,
        }
        defaults.update(extra)
        return _write_snapshot(placement, **defaults)

    def test_new_published_game_is_eligible_and_snapshot_clears_due(self):
        now = timezone.now()
        placement = self._placement(8801)
        snapshot = self._due_snapshot(placement, now=now)
        self.assertTrue(is_difficulty_refresh_due(placement, snapshot, now=now))

        visible = _rating_result(placement)
        with patch('games.difficulty_refresh.calculate_game_difficulty', return_value=visible):
            results = refresh_due_daily_difficulties(now=now, limit=10, game_ids=('ladder',))
        self.assertEqual(len(results), 1)
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertFalse(snapshot.dirty)
        self.assertEqual(snapshot.calculated_revision, 1)
        self.assertGreater(snapshot.refresh_not_before, now)
        self.assertFalse(is_difficulty_refresh_due(placement, snapshot, now=now))

    def test_new_attempt_bumps_revision_but_waits_for_throttle(self):
        now = timezone.now()
        placement = self._placement(8802)
        snapshot = self._due_snapshot(placement, now=now)
        visible = _rating_result(placement)
        with patch('games.difficulty_refresh.calculate_game_difficulty', return_value=visible):
            refresh_due_daily_difficulties(now=now, limit=10, game_ids=('ladder',))

        mark_game_difficulty_changed(placement_id=placement.pk)
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertEqual(snapshot.data_revision, 2)
        self.assertTrue(snapshot.dirty)
        self.assertFalse(is_difficulty_refresh_due(placement, snapshot, now=now))

        later = snapshot.refresh_not_before
        self.assertTrue(is_difficulty_refresh_due(placement, snapshot, now=later))
        with patch('games.difficulty_refresh.calculate_game_difficulty', return_value=visible) as calc:
            results = refresh_due_daily_difficulties(now=later, limit=10, game_ids=('ladder',))
        calc.assert_called()
        self.assertEqual(results[0]['placement_id'], placement.pk)
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertEqual(snapshot.calculated_revision, 2)
        self.assertFalse(snapshot.dirty)

    def test_two_workers_claim_distinct_due_rows(self):
        now = timezone.now()
        first = self._placement(8803)
        second = self._placement(8804)
        self._due_snapshot(first, now=now, published_at=now - timedelta(hours=1))
        self._due_snapshot(second, now=now, published_at=now - timedelta(hours=2))

        claimed_a = claim_due_daily_difficulties(now=now, limit=1, game_ids=('ladder',))
        claimed_b = claim_due_daily_difficulties(now=now, limit=1, game_ids=('ladder',))
        self.assertEqual(len(claimed_a), 1)
        self.assertEqual(len(claimed_b), 1)
        self.assertNotEqual(claimed_a[0].snapshot_id, claimed_b[0].snapshot_id)
        claimed_ids = {claimed_a[0].placement.pk, claimed_b[0].placement.pk}
        self.assertEqual(claimed_ids, {first.pk, second.pk})

        claimed_c = claim_due_daily_difficulties(now=now, limit=10, game_ids=('ladder',))
        self.assertEqual(claimed_c, [])

    def test_revision_race_during_calculation_keeps_row_dirty(self):
        now = timezone.now()
        placement = self._placement(8805)
        self._due_snapshot(placement, now=now, revision=10)
        claimed = claim_due_daily_difficulties(now=now, limit=1, game_ids=('ladder',))
        self.assertEqual(claimed[0].claimed_revision, 10)

        mark_game_difficulty_changed(placement_id=placement.pk)
        written = persist_difficulty_snapshot(
            placement,
            _rating_result(placement),
            now=now,
            claimed_revision=10,
            claim_token=claimed[0].token,
        )
        self.assertEqual(written, 1)
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertEqual(snapshot.calculated_revision, 10)
        self.assertEqual(snapshot.data_revision, 11)
        self.assertTrue(snapshot.dirty)
        self.assertTrue(is_difficulty_refresh_due(
            placement, snapshot, now=snapshot.refresh_not_before,
        ))

    def test_active_lease_blocks_other_worker_until_expiry(self):
        now = timezone.now()
        placement = self._placement(8806)
        self._due_snapshot(placement, now=now)
        claimed = claim_due_daily_difficulties(now=now, limit=1, game_ids=('ladder',))
        self.assertEqual(len(claimed), 1)
        self.assertEqual(
            claim_due_daily_difficulties(now=now + timedelta(minutes=1), limit=1, game_ids=('ladder',)),
            [],
        )
        later = now + timedelta(minutes=6)
        reclaimed = claim_due_daily_difficulties(now=later, limit=1, game_ids=('ladder',))
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].placement.pk, placement.pk)
        self.assertNotEqual(reclaimed[0].token, claimed[0].token)

    def test_stale_worker_cannot_overwrite_newer_claim(self):
        now = timezone.now()
        placement = self._placement(8807)
        self._due_snapshot(placement, now=now, revision=1)
        worker_a = claim_due_daily_difficulties(now=now, limit=1, game_ids=('ladder',))[0]

        later = now + timedelta(minutes=6)
        worker_b = claim_due_daily_difficulties(now=later, limit=1, game_ids=('ladder',))[0]
        b_result = _rating_result(placement, stars=5, n=40)
        self.assertEqual(
            persist_difficulty_snapshot(
                placement,
                b_result,
                now=later,
                claimed_revision=worker_b.claimed_revision,
                claim_token=worker_b.token,
            ),
            1,
        )
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertEqual(snapshot.stars, 5)
        self.assertEqual(snapshot.n, 40)

        a_result = _rating_result(placement, stars=1, n=6)
        self.assertEqual(
            persist_difficulty_snapshot(
                placement,
                a_result,
                now=later + timedelta(minutes=1),
                claimed_revision=worker_a.claimed_revision,
                claim_token=worker_a.token,
            ),
            0,
        )
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertEqual(snapshot.stars, 5)
        self.assertEqual(snapshot.n, 40)

    def test_calculation_error_keeps_dirty_and_applies_backoff(self):
        now = timezone.now()
        placement = self._placement(8808)
        self._due_snapshot(placement, now=now, revision=4)
        claimed = claim_due_daily_difficulties(now=now, limit=1, game_ids=('ladder',))[0]
        with patch('games.difficulty_refresh.logger'), patch(
            'games.difficulty_refresh.calculate_game_difficulty',
            side_effect=RuntimeError('boom'),
        ):
            self.assertIsNone(refresh_claimed_difficulty(claimed, now=now))
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertTrue(snapshot.dirty)
        self.assertEqual(snapshot.calculated_revision, 0)
        self.assertEqual(snapshot.data_revision, 4)
        self.assertEqual(snapshot.refresh_fail_count, 1)
        self.assertEqual(snapshot.refresh_not_before, now + retry_delay_for_fail_count(1))
        self.assertIn('boom', snapshot.refresh_last_error)
        self.assertIsNone(snapshot.refresh_claim_token)

        later = snapshot.refresh_not_before
        claimed_again = claim_due_daily_difficulties(now=later, limit=1, game_ids=('ladder',))[0]
        visible = _rating_result(placement)
        with patch('games.difficulty_refresh.calculate_game_difficulty', return_value=visible):
            self.assertIsNotNone(refresh_claimed_difficulty(claimed_again, now=later))
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertEqual(snapshot.refresh_fail_count, 0)
        self.assertEqual(snapshot.refresh_last_error, '')
        self.assertFalse(snapshot.dirty)

    def test_retry_backoff_grows_then_caps(self):
        self.assertEqual(retry_delay_for_fail_count(1), timedelta(minutes=5))
        self.assertEqual(retry_delay_for_fail_count(2), timedelta(minutes=15))
        self.assertEqual(retry_delay_for_fail_count(3), timedelta(hours=1))
        self.assertEqual(retry_delay_for_fail_count(4), timedelta(hours=6))
        self.assertEqual(retry_delay_for_fail_count(9), timedelta(hours=6))

    def test_scheduler_skips_clean_and_old_unplayed_games(self):
        now = timezone.now()
        clean = self._placement(8809)
        old = self._placement(8810)
        _write_snapshot(
            clean,
            dirty=False,
            data_revision=1,
            calculated_revision=1,
            refresh_not_before=now - timedelta(days=1),
            published_at=now - timedelta(days=40),
        )
        _write_snapshot(
            old,
            dirty=False,
            data_revision=3,
            calculated_revision=3,
            refresh_not_before=now - timedelta(days=8),
            published_at=now - timedelta(days=40),
        )
        with patch('games.difficulty_refresh.calculate_game_difficulty') as calc:
            results = refresh_due_daily_difficulties(now=now, limit=10, game_ids=('ladder',))
        calc.assert_not_called()
        self.assertEqual(results, [])

        mark_game_difficulty_changed(placement_id=old.pk)
        snapshot = DailyGameDifficulty.objects.get(placement=old)
        self.assertTrue(snapshot.dirty)
        self.assertTrue(is_difficulty_refresh_due(placement=old, snapshot=snapshot, now=now))
        visible = _rating_result(old)
        with patch('games.difficulty_refresh.calculate_game_difficulty', return_value=visible) as calc:
            results = refresh_due_daily_difficulties(now=now, limit=10, game_ids=('ladder',))
        calc.assert_called()
        self.assertEqual(results[0]['placement_id'], old.pk)

    def test_page_open_does_not_calculate(self):
        placement = self._placement(8811)
        DailyGameDifficulty.objects.filter(placement=placement).delete()
        with patch('games.difficulty.calculate_game_difficulty') as calc:
            self.assertIsNone(get_game_difficulty(placement))
            self.assertEqual(get_cached_game_difficulties([placement]), {})
        calc.assert_not_called()

    def test_stale_claim_token_object_still_rejected(self):
        now = timezone.now()
        placement = self._placement(8812)
        snapshot = self._due_snapshot(placement, now=now)
        stale = ClaimedDifficultyRefresh(
            snapshot_id=snapshot.pk,
            placement=placement,
            token=uuid.uuid4(),
            claimed_revision=1,
        )
        self.assertEqual(
            persist_difficulty_snapshot(
                placement,
                _rating_result(placement),
                now=now,
                claimed_revision=1,
                claim_token=stale.token,
            ),
            0,
        )
        snapshot = DailyGameDifficulty.objects.get(placement=placement)
        self.assertTrue(snapshot.dirty)
        self.assertEqual(snapshot.calculated_revision, 0)
