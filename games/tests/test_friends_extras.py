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
    """The notice itself is covered in test_notifications; here: the friends side."""

    def test_request_notifies_recipient_after_commit(self):
        with patch('games.notifications.service.notify') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                friends.request_friendship(self.anna, self.vera.pk)
        notify.assert_called_once_with(
            self.vera.pk, 'friend.request', actor=self.anna.pk, from_user_id=self.anna.pk,
        )

    def test_notify_failure_does_not_break_request(self):
        with patch('games.notifications.service.notify', side_effect=RuntimeError('db down')):
            with self.captureOnCommitCallbacks(execute=True):
                result = friends.request_friendship(self.anna, self.vera.pk)
        self.assertEqual(result, friends.RELATION_OUTGOING)

    @override_settings(SITE_BASE_URL='https://interoves.com')
    def test_legacy_queued_payload_still_sends_while_pending(self):
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


class ReviewRoundTests(FriendsExtrasFixture):
    def test_friends_scope_drops_the_handshake_marks(self):
        self.befriend(self.anna, self.vera)
        request = RequestFactory().get('/ladder/1/results/?scope=friends')
        request.user = self.anna
        actors = [PersonalResultsParticipant(user_id=self.vera.pk), PersonalResultsParticipant(user_id=self.anna.pk)]
        out = paginate_results_rows(request, {'teams_sorted': actors, 'team_to_place': {a: 1 for a in actors}})
        self.assertEqual(len(out['teams_sorted']), 2)
        self.assertEqual(out['team_to_is_friend'], {})

    def test_scope_chips_render_in_tournament_mode(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(provider=provider, defaults={'name': provider, 'client_id': 't', 'secret': 't'})
            app.sites.add(site)
        request = RequestFactory().get('/games/fx_tournament/tournament-results/')
        request.user = self.anna
        html = render_to_string('site/results.html', request=request, context={
            'user_timezone': 'Europe/Moscow',
            'mode': 'tournament',
            'game': Game(id='fx_tournament', name='Турнир', author='t', author_extra='', project_id='main'),
            'results_scope_options': [{'label': 'Все', 'icon': 'ph-globe-simple', 'value': 'all', 'active': True, 'url': '/x/'}],
        })
        self.assertIn('data-results-scope="all"', html)

    def test_anonymous_sees_login_prompts(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from django.test import Client

        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(provider=provider, defaults={'name': provider, 'client_id': 't', 'secret': 't'})
            app.sites.add(site)
        body = Client().get('/u/{}/'.format(self.anna_p.public_id)).content.decode()
        self.assertIn('Войти, чтобы добавить в друзья', body)
        team = Team.objects.create(name='fx_login_team')
        body = Client().get('/t/{}/'.format(team.public_id)).content.decode()
        self.assertIn('Войти, чтобы попроситься', body)


class PreviewAndStatsRoundTests(FriendsExtrasFixture):
    def setUp(self):
        super().setUp()
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(provider=provider, defaults={'name': provider, 'client_id': 't', 'secret': 't'})
            app.sites.add(site)

    def test_invite_link_preview_names_the_inviter(self):
        from django.test import Client

        Profile.objects.filter(pk=self.anna_p.pk).update(avatar_url='https://evil.example/track.png')
        invite = friends.new_invite(self.anna)
        body = Client().get('/friends/invite/{}/'.format(invite.token)).content.decode()
        self.assertIn('<meta property="og:title" content="Анна Петрова зовёт вас в друзья">', body)
        self.assertIn('og-1200x630.png', body)
        self.assertNotIn('evil.example', body)

    def test_player_preview_uses_provider_avatar(self):
        from django.test import Client

        Profile.objects.filter(pk=self.anna_p.pk).update(avatar_url='https://lh3.googleusercontent.com/a.png')
        body = Client().get('/u/{}/'.format(self.anna_p.public_id)).content.decode()
        self.assertIn('<meta property="og:image" content="https://lh3.googleusercontent.com/a.png">', body)
        self.assertIn('Игрок Inter Oves', body)

    def test_head_to_head_ignores_prepublication_plays(self):
        from games.community.stats import head_to_head
        from games.models import DailyResultProjection

        game, _ = Game.objects.get_or_create(
            id='walls', defaults={'name': 'Стенки', 'author': 't', 'author_extra': '', 'project_id': 'main', 'is_ready': True},
        )
        group = TaskGroup.objects.create(label='fx_pre')
        for user, score, pre in ((self.anna, 5, True), (self.vera, 3, False)):
            DailyResultProjection.objects.create(
                game=game, task_group=group, actor_type='user', actor_key=str(user.pk),
                user=user, score=score, is_prepublication=pre,
            )
        self.assertEqual(head_to_head(self.anna, self.vera).common, 0)

    def test_stats_labels_agree_with_numbers(self):
        from games.community.stats import PlayerStats

        stats = PlayerStats(total_played=1, active_days=1, current_day_streak=2, best_day_streak=5)
        html = render_to_string('site/partials/daily_stats.html', {'stats': stats})
        for text in ('игра сыграна', 'день в игре', 'дня подряд', 'дней — рекорд'):
            self.assertIn(text, html)


class ShareUrlTests(TestCase):
    def test_links_are_https_on_the_visitors_host(self):
        from games.site.pages.shared import share_url

        request = RequestFactory().get('/friends/', HTTP_HOST='ru.interoves.com')
        with override_settings(DEBUG=False, ALLOWED_HOSTS=['ru.interoves.com']):
            self.assertEqual(share_url(request, '/friends/invite/x/'), 'https://ru.interoves.com/friends/invite/x/')
        with override_settings(DEBUG=True, ALLOWED_HOSTS=['ru.interoves.com']):
            self.assertEqual(share_url(request, '/x/'), 'http://ru.interoves.com/x/')


class ConsistencyRoundTests(FriendsExtrasFixture):
    def test_requests_from_hidden_or_inactive_accounts_do_not_light_the_badge(self):
        friends.request_friendship(self.anna, self.vera.pk)
        friends.request_friendship(self.boris, self.vera.pk)
        Profile.objects.filter(pk=self.anna_p.pk).update(is_hidden=True)
        User.objects.filter(pk=self.boris.pk).update(is_active=False)
        self.assertEqual(friends.incoming_count(self.vera.pk), 0)
        self.assertFalse(friends.incoming_requests(self.vera.pk).exists())

    def test_friend_ids_skip_hidden_accounts(self):
        self.befriend(self.anna, self.vera)
        self.befriend(self.boris, self.vera)
        Profile.objects.filter(pk=self.boris_p.pk).update(is_hidden=True)
        self.assertEqual(friends.friend_user_ids(self.vera.pk), {self.anna.pk})

    def test_owner_preview_explains_untrusted_photo(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from django.test import Client

        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(provider=provider, defaults={'name': provider, 'client_id': 't', 'secret': 't'})
            app.sites.add(site)
        Profile.objects.filter(pk=self.anna_p.pk).update(avatar_url='https://my.site/me.png')
        client = Client()
        client.force_login(self.anna)
        body = client.get('/u/{}/'.format(self.anna_p.public_id)).content.decode()
        self.assertNotIn('my.site/me.png', body)
        self.assertIn('подойдёт фото из VK', body)
