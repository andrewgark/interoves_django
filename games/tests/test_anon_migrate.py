from datetime import timedelta
import json

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from unittest.mock import patch
from django.utils import timezone

from games.models import (
    AnonAccountClaim,
    AlphabettyDictSuggestion,
    Attempt,
    BugReport,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    Hint,
    HintAttempt,
    HTMLPage,
    Like,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    Project,
    StatisticsEvent,
    Task,
    TaskGroup,
    AnonymousMergeJob,
)
from games.ops.analytics.identity import attach_anon_cookie
from games.club.merge.anon import (
    heal_orphaned_likes_from_migrate_events,
    migrate_anon_chain_task_states,
)
from games.club.merge.anonymous import (
    claim_next_merge_job,
    process_merge_job,
    serialize_merge_job,
)


class AnonMigrateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals')
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        with patch('games.realtime.track_compat.track_task_change'):
            cls.game = Game.objects.create(
                id='anon_migrate_test',
                name='Anon migrate',
                author='test',
                author_extra='',
                project_id='main',
                is_ready=True,
            )
            cls.tg = TaskGroup.objects.create(label='anon_migrate_tg')
            GameTaskGroup.objects.create(
                game=cls.game, task_group=cls.tg, number=1, name='G1',
            )
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                checker=CheckerType.objects.get(pk='equals'),
                points=1,
                answer='ok',
            )
            cls.hint = Hint.objects.create(
                task=cls.task,
                number='1',
                points_penalty=0,
            )
        cls.user = User.objects.create_user('migrate_user', 'migrate@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='M', last_name='U')
        cls.anon_key = 'anon-migrate-key-1'

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='migrate_user', password='secret'))
        attach_anon_cookie(self.client, self.anon_key)
        with patch('games.realtime.track_compat.track_task_change'):
            Attempt.manager.create(
                anon_key=self.anon_key,
                task=self.task,
                game=self.game,
                text='a',
                status='Wrong',
            )
            Attempt.manager.create(
                anon_key=self.anon_key,
                task=self.task,
                game=self.game,
                text='ok',
                status='Ok',
            )
            HintAttempt.objects.create(
                anon_key=self.anon_key,
                hint=self.hint,
            )
        raw_post = self.client.post

        def post_and_drain(*args, **kwargs):
            response = raw_post(*args, **kwargs)
            if response.status_code != 202:
                return response
            payload = response.json()
            claimed = claim_next_merge_job(worker='test')
            self.assertIsNotNone(claimed)
            job, token = claimed
            process_merge_job(job, token, max_operations=100)
            job.refresh_from_db()
            self.assertEqual(job.status, AnonymousMergeJob.STATUS_COMPLETED, job.last_error)
            final_payload = {'status': 'ok', 'done': True, **serialize_merge_job(job)}
            response.status_code = 200
            response.content = json.dumps(final_payload).encode('utf-8')
            return response

        self.client.post = post_and_drain

    def test_migrate_moves_attempts_and_records_statistics_event(self):
        PlayerStartedGame.objects.create(
            anon_key=self.anon_key,
            game=self.game,
            task_group=self.tg,
            game_kind=self.game.id,
            game_instance_id='{}:{}'.format(self.game.id, self.tg.id),
            public_game_id='1',
        )
        PlayerCompletedGame.objects.create(
            anon_key=self.anon_key,
            game=self.game,
            task_group=self.tg,
            game_kind=self.game.id,
            game_instance_id='{}:{}'.format(self.game.id, self.tg.id),
            public_game_id='1',
        )
        PlayerAnalyticsState.objects.create(
            anon_key=self.anon_key,
            activated_at=timezone.now(),
        )
        url = reverse('new_migrate_anon_attempts')
        resp = self.client.post(url, {'anon_key': self.anon_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        data = StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload
        self.assertEqual(data['moved'], 2)
        self.assertEqual(data['moved_hints'], 1)
        self.assertEqual(data['moved_starts'], 1)
        self.assertEqual(data['moved_completions'], 1)
        self.assertEqual(data['moved_analytics_state'], 1)

        self.assertEqual(
            Attempt.manager.filter(user=self.user, task=self.task, anon_key__isnull=True).count(),
            2,
        )
        self.assertEqual(
            Attempt.manager.filter(anon_key=self.anon_key).count(),
            0,
        )
        self.assertEqual(
            HintAttempt.objects.filter(user=self.user, hint=self.hint, anon_key__isnull=True).count(),
            1,
        )
        self.assertEqual(
            PlayerStartedGame.objects.filter(user=self.user, anon_key__isnull=True).count(),
            1,
        )
        self.assertEqual(
            PlayerCompletedGame.objects.filter(user=self.user, anon_key__isnull=True).count(),
            1,
        )
        self.assertIsNotNone(PlayerAnalyticsState.objects.get(user=self.user).activated_at)

        events = StatisticsEvent.objects.filter(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            user=self.user,
        )
        self.assertEqual(events.count(), 1)
        payload = events.get().payload
        self.assertEqual(payload['anon_key'], self.anon_key)
        self.assertEqual(payload['moved'], 2)
        self.assertEqual(payload['moved_hints'], 1)
        self.assertEqual(payload['moved_likes'], 0)

    def test_migrate_moves_anonymous_like_to_user(self):
        Like.manager.create(
            anon_key=self.anon_key,
            task=self.task,
            value=1,
        )

        resp = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_likes'], 1)
        self.assertFalse(Like.manager.filter(anon_key=self.anon_key).exists())
        self.assertEqual(
            Like.manager.filter(user=self.user, task=self.task, value=1).count(),
            1,
        )
        payload = StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            user=self.user,
        ).payload
        self.assertEqual(payload['moved_likes'], 1)

    def test_migrate_collapses_same_anonymous_and_user_reaction(self):
        Like.manager.create(user=self.user, task=self.task, value=1)
        Like.manager.create(anon_key=self.anon_key, task=self.task, value=1)

        resp = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )

        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_likes'], 1)
        self.assertFalse(Like.manager.filter(anon_key=self.anon_key).exists())
        self.assertEqual(
            Like.manager.filter(user=self.user, task=self.task).count(),
            1,
        )
        self.assertEqual(
            Like.manager.get(user=self.user, task=self.task).value,
            1,
        )
        self.assertEqual(Like.manager.get_total_likes(self.task), 1)

    def test_migrate_keeps_existing_user_reaction_on_conflict(self):
        Like.manager.create(user=self.user, task=self.task, value=-1)
        Like.manager.create(anon_key=self.anon_key, task=self.task, value=1)

        resp = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )

        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_likes'], 1)
        self.assertFalse(Like.manager.filter(anon_key=self.anon_key).exists())
        user_reactions = Like.manager.filter(user=self.user, task=self.task)
        self.assertEqual(user_reactions.count(), 1)
        self.assertEqual(user_reactions.get().value, -1)
        self.assertEqual(Like.manager.get_total_likes(self.task), 0)
        self.assertEqual(Like.manager.get_total_dislikes(self.task), 1)

    def test_heal_moves_likes_from_previous_migrate_event(self):
        Like.manager.create(anon_key=self.anon_key, task=self.task, value=1)
        StatisticsEvent.record(
            StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            user=self.user,
            anon_key=self.anon_key,
            moved=2,
            moved_hints=1,
        )

        events, rows = heal_orphaned_likes_from_migrate_events()

        self.assertEqual((events, rows), (1, 1))
        self.assertFalse(Like.manager.filter(anon_key=self.anon_key).exists())
        self.assertEqual(
            Like.manager.filter(user=self.user, task=self.task, value=1).count(),
            1,
        )

    def test_migrate_moves_chain_task_state(self):
        """После логина CTS должен переехать на user — иначе raddle чекер сбрасывает прогресс."""
        state_json = '{"solved_indices": [0, 1, 2, 12], "used_hints": [0, 1], "total": 0.5}'
        ChainTaskState.objects.create(
            anon_key=self.anon_key,
            task=self.task,
            game=self.game,
            game_mode='general',
            state=state_json,
        )
        url = reverse('new_migrate_anon_attempts')
        resp = self.client.post(url, {'anon_key': self.anon_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_states'], 1)

        self.assertFalse(
            ChainTaskState.objects.filter(anon_key=self.anon_key).exists(),
        )
        row = ChainTaskState.objects.get(
            user=self.user, task=self.task, game=self.game, game_mode='general',
        )
        self.assertIsNone(row.anon_key)
        self.assertEqual(row.state, state_json)
        payload = StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            user=self.user,
        ).payload
        self.assertEqual(payload['moved_states'], 1)

    def test_migrate_merges_richer_anon_state_over_user(self):
        ChainTaskState.objects.create(
            user=self.user,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{"solved_indices": [0, 12], "total": 0}',
        )
        ChainTaskState.objects.create(
            anon_key=self.anon_key,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{"solved_indices": [0, 1, 2, 12], "total": 0.5}',
        )
        resp = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )
        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_states'], 1)
        row = ChainTaskState.objects.get(
            user=self.user, task=self.task, game=self.game, game_mode='general',
        )
        import json
        self.assertEqual(json.loads(row.state)['solved_indices'], [0, 1, 2, 12])
        self.assertFalse(ChainTaskState.objects.filter(anon_key=self.anon_key).exists())

    def test_migrate_merges_alphabetty_guesses_over_empty_user_state(self):
        """Пустой user CTS (открыл страницу) не должен затирать anon-прогресс Алфавитки."""
        import json
        ChainTaskState.objects.create(
            user=self.user,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{"guesses": [], "won": false}',
        )
        anon_state = '{"guesses": ["ГОД", "ЯБЛОКО"], "won": false}'
        ChainTaskState.objects.create(
            anon_key=self.anon_key,
            task=self.task,
            game=self.game,
            game_mode='general',
            state=anon_state,
        )
        resp = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )
        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_states'], 1)
        row = ChainTaskState.objects.get(
            user=self.user, task=self.task, game=self.game, game_mode='general',
        )
        self.assertEqual(json.loads(row.state)['guesses'], ['ГОД', 'ЯБЛОКО'])

    def test_migrate_unions_disjoint_alphabetty_guesses(self):
        import json
        ChainTaskState.objects.create(
            user=self.user,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{"guesses": ["АРБУЗ"], "won": false}',
        )
        ChainTaskState.objects.create(
            anon_key=self.anon_key,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{"guesses": ["ЯБЛОКО"], "won": false}',
        )
        resp = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )
        self.assertEqual(StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload['moved_states'], 1)
        row = ChainTaskState.objects.get(
            user=self.user, task=self.task, game=self.game, game_mode='general',
        )
        guesses = json.loads(row.state)['guesses']
        self.assertEqual(set(guesses), {'АРБУЗ', 'ЯБЛОКО'})

    def test_alphabetty_migrate_rebuilds_guess_order_from_attempts(self):
        import json
        checker = CheckerType.objects.get_or_create(pk='alphabetty')[0]
        tg = TaskGroup.objects.create(label='anon_migrate_alphabetty', checker=checker, points=10)
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg, number='8', name='Алфавитка #8',
        )
        task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data='ДЮЙМ',
            answer='ДЮЙМ',
            points=10,
        )
        anon_guesses = [
            'КАВАРДАК', 'БАОБАБ', 'ЗЕЛЕНЬ', 'ДАНТИСТ', 'ЖЕЗЛ', 'ЗАЧЕТКА',
            'ЕЛКА', 'ДЕКОР', 'ДУНОВЕНИЕ', 'ЕВАНГЕЛИЕ', 'ДУРИАН', 'ЕБАНИНА', 'ДЯТЕЛ',
        ]
        user_guesses = ['ДЯГЕЛЬ', 'ДЮБЕЛЬ', 'ДЮЖИНА', 'ДЮШЕС', 'ДЮЙМ']
        base = timezone.now()

        for i, word in enumerate(anon_guesses):
            Attempt.manager.create(
                anon_key=self.anon_key,
                task=task,
                game=self.game,
                text=word,
                status='Partial',
                time=base + timedelta(seconds=i),
            )
        for i, word in enumerate(user_guesses):
            Attempt.manager.create(
                user=self.user,
                task=task,
                game=self.game,
                text=word,
                status='Ok' if word == 'ДЮЙМ' else 'Partial',
                time=base + timedelta(seconds=100 + i),
            )

        ChainTaskState.objects.create(
            anon_key=self.anon_key,
            task=task,
            game=self.game,
            game_mode='general',
            state=json.dumps({'guesses': anon_guesses, 'won': False}, ensure_ascii=False),
        )
        ChainTaskState.objects.create(
            user=self.user,
            task=task,
            game=self.game,
            game_mode='general',
            state=json.dumps({'guesses': user_guesses, 'won': True}, ensure_ascii=False),
        )

        Attempt.manager.filter(
            anon_key=self.anon_key,
            user__isnull=True,
            team__isnull=True,
            task=task,
            game=self.game,
        ).update(user=self.user, anon_key=None)
        moved = migrate_anon_chain_task_states(self.user, self.anon_key)
        self.assertEqual(moved, 1)

        row = ChainTaskState.objects.get(
            user=self.user, task=task, game=self.game, game_mode='general',
        )
        self.assertEqual(
            json.loads(row.state)['guesses'],
            anon_guesses + user_guesses,
        )

    def test_word_salad_migrate_replays_guest_attempts_after_user_solve(self):
        checker = CheckerType.objects.get_or_create(pk='word_salad')[0]
        tg = TaskGroup.objects.create(label='anon_migrate_salad', checker=checker, points=1)
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg, number='9', name='Салатик #9',
        )
        task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='word_salad',
            checker=checker,
            checker_data=json.dumps({
                'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
                'words': ['ABCD', 'EFGH'],
            }),
            points=2,
        )
        base = timezone.now() - timedelta(hours=1)

        def attempt(path, *, offset, **actor):
            created = Attempt.manager.create(
                task=task,
                game=self.game,
                text=json.dumps({'action': 'solve', 'path': path}),
                status='Partial',
                points=0,
                **actor,
            )
            Attempt.manager.filter(pk=created.pk).update(time=base + timedelta(seconds=offset))
            return created

        attempt([0, 1, 2, 3], offset=0, user=self.user)
        attempt([7, 6, 5, 4], offset=1, user=self.user)
        guest = attempt([0, 1, 2, 3], offset=100, anon_key=self.anon_key)
        with patch('games.ops.workers.jobs.recheck.track_actor_task_change'):
            from games.ops.workers.jobs.recheck import recheck_word_salad_actor
            recheck_word_salad_actor(task, user=self.user, game=self.game, notify=False)
            recheck_word_salad_actor(task, anon_key=self.anon_key, game=self.game, notify=False)
        guest.refresh_from_db()
        self.assertEqual(json.loads(guest.state)['solved_indices'], [0])

        Attempt.manager.filter(pk=guest.pk).update(user=self.user, anon_key=None)
        with patch('games.ops.workers.jobs.recheck.track_actor_task_change'):
            moved = migrate_anon_chain_task_states(self.user, self.anon_key)
        self.assertTrue(moved)

        guest.refresh_from_db()
        self.assertEqual(sorted(json.loads(guest.state)['solved_indices']), [0, 1])
        self.assertEqual(float(guest.points), 2.0)
        row = ChainTaskState.objects.get(
            user=self.user, task=task, game=self.game, game_mode='general',
        )
        self.assertEqual(row.last_attempt_id, guest.pk)
        self.assertEqual(sorted(json.loads(row.state)['solved_indices']), [0, 1])

    def test_migrate_count_endpoint(self):
        url = reverse('new_anon_migrate_count')
        resp = self.client.get(url, {'anon_key': self.anon_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['attempts'], 2)
        # Even a small amount of real progress is now offered for transfer;
        # "Позже" prevents repeated prompts within the browser session.
        self.assertTrue(data['show_prompt'])
        # Пример-ссылка ведёт на круг (task group), а не на задание.
        expected_url = reverse('new_task_group', kwargs={
            'game_id': self.game.id,
            'task_group_number': '1',
        })
        self.assertEqual(data['example_url'], expected_url)
        self.assertEqual(data['example_label'], 'G1')

    def test_migrate_count_does_not_prompt_for_key_with_merge_job(self):
        AnonAccountClaim.objects.create(anon_key=self.anon_key, user=self.user)
        job = AnonymousMergeJob.objects.create(anon_key=self.anon_key, user=self.user)
        url = reverse('new_anon_migrate_count')
        for status, expected in (
            (AnonymousMergeJob.STATUS_PENDING, 'merge_in_progress'),
            (AnonymousMergeJob.STATUS_FAILED, 'merge_in_progress'),
            (AnonymousMergeJob.STATUS_COMPLETED, 'already_merged'),
        ):
            AnonymousMergeJob.objects.filter(pk=job.pk).update(status=status)
            data = self.client.get(url, {'anon_key': self.anon_key}).json()
            self.assertEqual(data['status'], expected)
            self.assertFalse(data['show_prompt'])

    def test_migrate_count_does_not_load_puzzle_or_attempt_payloads(self):
        Task.objects.filter(pk=self.task.pk).update(tags={'large_puzzle': 'x' * 1000})
        Attempt.manager.bulk_create([
            Attempt(
                anon_key=self.anon_key, task=self.task, game=self.game,
                text='long guess', state='large history state', status='Wrong',
            )
            for _ in range(3)
        ])

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                reverse('new_anon_migrate_count'), {'anon_key': self.anon_key},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['attempts'], 5)
        self.assertEqual(response.json()['example_label'], 'G1')
        quote = connection.ops.quote_name
        history_queries = [
            row['sql'] for row in queries
            if 'FROM {}'.format(quote('games_attempt')) in row['sql']
            and 'JOIN {}'.format(quote('games_task')) in row['sql']
        ]
        self.assertEqual(len(history_queries), 1)
        for table, field in (
            ('games_task', 'tags'), ('games_task', 'checker_data'),
            ('games_task', 'text'), ('games_attempt', 'state'),
            ('games_attempt', 'text'),
        ):
            self.assertNotIn('{}.{}'.format(quote(table), quote(field)), history_queries[0])

    def test_migrate_count_example_resolves_legacy_attempt_without_game(self):
        Attempt.manager.filter(anon_key=self.anon_key).update(game=None)
        response = self.client.get(
            reverse('new_anon_migrate_count'), {'anon_key': self.anon_key},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['attempts'], 2)
        self.assertEqual(response.json()['example_label'], 'G1')

    def test_migrate_count_finds_example_after_unlinked_task(self):
        with patch('games.realtime.track_compat.track_task_change'):
            group = TaskGroup.objects.create(label='unlinked migration history')
            task = Task.objects.create(task_group=group, number='1', checker_id='equals')
            attempt = Attempt.manager.create(
                anon_key=self.anon_key, task=task, status='Wrong', text='unlinked',
            )
        Attempt.manager.filter(pk=attempt.pk).update(time=timezone.now() - timedelta(days=1))
        response = self.client.get(
            reverse('new_anon_migrate_count'), {'anon_key': self.anon_key},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['attempts'], 3)
        self.assertEqual(response.json()['example_label'], 'G1')

    def test_club_archive_offer_last_shown_is_saved_in_profile(self):
        before = timezone.now() - timedelta(seconds=1)
        response = self.client.post(
            reverse('new_club_archive_offer_action'), {'action': 'shown'},
        )
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertGreater(self.user.profile.club_archive_offer_last_shown_at, before)

    def test_club_archive_offer_can_be_disabled_forever(self):
        response = self.client.post(
            reverse('new_club_archive_offer_action'), {'action': 'never'},
        )
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.club_archive_offer_never)

    def test_club_archive_offer_can_be_deferred(self):
        before = timezone.now() + timedelta(days=13, hours=23)
        response = self.client.post(
            reverse('new_club_archive_offer_action'), {'action': 'defer'},
        )
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertGreater(self.user.profile.club_archive_offer_deferred_until, before)
        self.assertLess(
            self.user.profile.club_archive_offer_deferred_until,
            timezone.now() + timedelta(days=14, seconds=2),
        )

    def test_show_prompt_with_enough_unsolved_attempts(self):
        # Ещё 8 анонимных посылок (в setUp уже есть 2) → всего 10.
        with patch('games.realtime.track_compat.track_task_change'):
            for _ in range(8):
                Attempt.manager.create(
                    anon_key=self.anon_key,
                    task=self.task,
                    game=self.game,
                    text='x',
                    status='Wrong',
                )
        url = reverse('new_anon_migrate_count')
        data = self.client.get(url, {'anon_key': self.anon_key}).json()
        self.assertEqual(data['attempts'], 10)
        self.assertTrue(data['show_prompt'])
        self.assertIn('example_url', data)

    def test_solved_task_is_not_counted(self):
        # Пользователь уже сдал это задание на OK в личном режиме.
        with patch('games.realtime.track_compat.track_task_change'):
            Attempt.manager.create(
                user=self.user,
                task=self.task,
                game=self.game,
                text='ok',
                status='Ok',
            )
            for _ in range(8):
                Attempt.manager.create(
                    anon_key=self.anon_key,
                    task=self.task,
                    game=self.game,
                    text='x',
                    status='Wrong',
                )
        url = reverse('new_anon_migrate_count')
        data = self.client.get(url, {'anon_key': self.anon_key}).json()
        # Все анонимные посылки на уже сданное задание не учитываются.
        self.assertEqual(data['attempts'], 0)
        self.assertFalse(data['show_prompt'])
        self.assertNotIn('example_url', data)

    def test_empty_migrate_does_not_record_event(self):
        empty_key = 'no-such-anon'
        attach_anon_cookie(self.client, empty_key)
        url = reverse('new_migrate_anon_attempts')
        resp = self.client.post(url, {'anon_key': empty_key})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'empty')
        self.assertEqual(
            StatisticsEvent.objects.filter(kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED).count(),
            0,
        )

    def test_migrate_claims_key_and_moves_guest_attributions(self):
        report = BugReport.objects.create(
            anon_key=self.anon_key,
            task=self.task,
            game=self.game,
            text='нашёл опечатку',
        )
        suggestion = AlphabettyDictSuggestion.objects.create(
            anon_key=self.anon_key,
            word='ТЕСТОВОЕСЛОВО',
        )

        data = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        ).json()

        data = StatisticsEvent.objects.get(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED, user=self.user,
        ).payload
        self.assertEqual(data['moved_bug_reports'], 1)
        self.assertEqual(data['moved_dict_suggestions'], 1)
        self.assertEqual(AnonAccountClaim.objects.get(anon_key=self.anon_key).user, self.user)
        self.assertEqual(BugReport.objects.get(pk=report.pk).user, self.user)
        self.assertIsNone(BugReport.objects.get(pk=report.pk).anon_key)
        self.assertEqual(AlphabettyDictSuggestion.objects.get(pk=suggestion.pk).user, self.user)
        self.assertIsNone(AlphabettyDictSuggestion.objects.get(pk=suggestion.pk).anon_key)

    def test_claimed_key_cannot_be_moved_to_another_user(self):
        other = User.objects.create_user('other-claim-user', 'other@example.com', 'secret')
        Profile.objects.create(user=other, first_name='Other', last_name='User')
        AnonAccountClaim.objects.create(anon_key=self.anon_key, user=other)

        response = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'claimed_elsewhere')
        self.assertEqual(Attempt.manager.filter(anon_key=self.anon_key).count(), 2)

    def test_same_user_can_retry_an_existing_guest_claim(self):
        AnonAccountClaim.objects.create(anon_key=self.anon_key, user=self.user)

        response = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertFalse(Attempt.manager.filter(anon_key=self.anon_key).exists())

    def test_hint_only_guest_progress_is_offered(self):
        key = 'hint-only-anon-key'
        HintAttempt.objects.create(anon_key=key, hint=self.hint)
        attach_anon_cookie(self.client, key)

        data = self.client.get(
            reverse('new_anon_migrate_count'), {'anon_key': key},
        ).json()

        self.assertTrue(data['show_prompt'])
        self.assertEqual(data['counts']['hints'], 1)

    def test_invalid_anon_key_is_rejected(self):
        response = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': 'x' * 65},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'invalid_anon_key')

    def test_cookie_prevents_claiming_a_different_guest_key(self):
        attach_anon_cookie(self.client, 'different-browser-key')
        response = self.client.post(
            reverse('new_migrate_anon_attempts'), {'anon_key': self.anon_key},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'anon_key_mismatch')
        self.assertEqual(Attempt.manager.filter(anon_key=self.anon_key).count(), 2)
