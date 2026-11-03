from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from games.community import friends
from games.community.hub import friends_today
from games.models import (
    Game,
    GameTaskGroup,
    PersonalResultsParticipant,
    PlayerCompletedGame,
    Profile,
    Project,
    TaskGroup,
    Team,
)
from games.ops.telegram.bot import user_notices
from games.results.table.presentation import paginate_results_rows


class FriendsExtrasFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.anna = User.objects.create_user('fx_anna')
        cls.vera = User.objects.create_user('fx_vera')
        cls.boris = User.objects.create_user('fx_boris')
        cls.anna_p = Profile.objects.create(user=cls.anna, first_name='Анна', last_name='Петрова')
        cls.vera_p = Profile.objects.create(
            user=cls.vera, first_name='Вера', last_name='Зорина',
            telegram_user_id=111, telegram_verified=True,
        )
        cls.boris_p = Profile.objects.create(user=cls.boris, first_name='Борис', last_name='Лапин')

    def setUp(self):
        cache.clear()

    def befriend(self, a, b):
        friends.request_friendship(a, b.pk)
        friends.accept_request(b, a.pk)


class FriendRequestNoticeTests(FriendsExtrasFixture):
    def test_request_enqueues_once_per_pair_after_commit(self):
        with patch.object(user_notices, 'enqueue_integrations_message', return_value=True) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                friends.request_friendship(self.anna, self.vera.pk)
            friends.remove(self.anna, self.vera.pk)
            with self.captureOnCommitCallbacks(execute=True):
                friends.request_friendship(self.anna, self.vera.pk)
        enqueue.assert_called_once()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs['message_type'], 'telegram.user_notice')
        self.assertEqual(kwargs['payload'], {'kind': 'friend_request', 'from_user_id': self.anna.pk, 'to_user_id': self.vera.pk})

    def test_no_notice_without_verified_telegram(self):
        with patch.object(user_notices, 'enqueue_integrations_message', return_value=True) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                friends.request_friendship(self.anna, self.boris.pk)
        enqueue.assert_not_called()

    def test_enqueue_failure_does_not_break_request(self):
        with patch.object(user_notices, 'enqueue_integrations_message', side_effect=RuntimeError('sqs down')):
            with self.captureOnCommitCallbacks(execute=True):
                result = friends.request_friendship(self.anna, self.vera.pk)
        self.assertEqual(result, friends.RELATION_OUTGOING)

    @override_settings(SITE_BASE_URL='https://interoves.com')
    def test_worker_sends_only_while_request_is_pending(self):
        friends.request_friendship(self.anna, self.vera.pk)
        payload = {'kind': 'friend_request', 'from_user_id': self.anna.pk, 'to_user_id': self.vera.pk}
        with patch('games.ops.telegram.bot.notify.send_user_telegram_message', return_value=True) as send:
            self.assertEqual(user_notices.run_user_notice_live(payload=payload), {'sent': True, 'reason': ''})
        user, text = send.call_args.args
        self.assertEqual(user.pk, self.vera.pk)
        self.assertIn('Анна Петрова', text)
        self.assertIn('https://interoves.com/u/{}/'.format(self.anna_p.public_id), text)
        friends.accept_request(self.vera, self.anna.pk)
        with patch('games.ops.telegram.bot.notify.send_user_telegram_message') as send:
            self.assertEqual(user_notices.run_user_notice_live(payload=payload)['reason'], 'not_pending')
        send.assert_not_called()

    def test_sender_name_is_escaped(self):
        Profile.objects.filter(pk=self.anna_p.pk).update(first_name='<b>x</b>')
        self.anna_p.refresh_from_db()
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', user_notices.format_friend_request_notice(self.anna_p))


class FriendsTodayTests(FriendsExtrasFixture):
    def test_lists_friends_who_played_todays_release_personally(self):
        self.befriend(self.anna, self.vera)
        self.befriend(self.anna, self.boris)
        game, _ = Game.objects.get_or_create(
            id='walls', defaults={'name': 'Стенки', 'author': 't', 'author_extra': '', 'project_id': 'main', 'is_ready': True},
        )
        today, yesterday = TaskGroup.objects.create(label='fx_today'), TaskGroup.objects.create(label='fx_old')
        GameTaskGroup.objects.create(game=game, task_group=today, number='7', name='7')
        GameTaskGroup.objects.create(game=game, task_group=yesterday, number='6', name='6')
        PlayerCompletedGame.objects.create(user=self.vera, game=game, task_group=today, game_kind='w', game_instance_id='a')
        PlayerCompletedGame.objects.create(user=self.boris, game=game, task_group=yesterday, game_kind='w', game_instance_id='b')
        team = Team.objects.create(name='fx_team')
        PlayerCompletedGame.objects.create(team=team, game=game, task_group=today, game_kind='w', game_instance_id='c')

        rows = friends_today(self.anna, [{'game': game, 'cta_number': '7', 'title': 'Стенки'}])
        self.assertEqual(len(rows), 1)
        self.assertEqual([f.name for f in rows[0]['friends']], ['Вера Зорина'])
        self.assertTrue(rows[0]['results_url'].endswith('/7/results/?scope=friends'))
        html = render_to_string('site/partials/hub_friends_today.html', {'rows': rows})
        self.assertIn('Вера Зорина', html)
        self.assertIn('Сравнить', html)

    def test_nothing_for_players_without_friends(self):
        with self.assertNumQueries(1):
            self.assertEqual(friends_today(self.anna, [{'game': object(), 'cta_number': '1'}]), [])


class FriendMarkTests(FriendsExtrasFixture):
    def test_friend_rows_get_a_mark(self):
        self.befriend(self.anna, self.vera)
        request = RequestFactory().get('/ladder/1/results/')
        request.user = self.anna
        actors = [PersonalResultsParticipant(user_id=self.vera.pk), PersonalResultsParticipant(user_id=self.boris.pk)]
        out = paginate_results_rows(request, {'teams_sorted': actors, 'team_to_place': {a: 1 for a in actors}})
        self.assertEqual(out['team_to_is_friend'], {actors[0]: True})
        html = render_to_string('site/partials/results_rows.html', out)
        self.assertEqual(html.count('results-friend-mark'), 1)


class WorkerDispatchTests(TestCase):
    def test_contract_lists_user_notice(self):
        from games.ops.workers.contract import WORKER_REGISTRY

        self.assertIn('telegram.user_notice', WORKER_REGISTRY.get('integrations').message_types)

    def test_stale_after_six_hours(self):
        from games.ops.workers.handlers.integrations import _is_stale

        now = timezone.now()
        message = {'type': 'telegram.user_notice', 'scheduled_for': now - timedelta(hours=7)}
        self.assertTrue(_is_stale(message, now=now))
        message['scheduled_for'] = now - timedelta(hours=1)
        self.assertFalse(_is_stale(message, now=now))
