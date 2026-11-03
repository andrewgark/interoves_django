from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from games.models import Game, GameTaskGroup, Profile, ProfileTeamMembership, Registration, TaskGroup, Team
from games.notifications import game_events, service
from games.notifications.models import DailyReminder, Notification
from games.notifications.prefs import channels, set_channels


class GameNotificationFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        from games.tests.test_feedback import _ensure_reference_rows

        _ensure_reference_rows()
        cls.team = Team.objects.create(name='ng_team', visible_name='Овцы')
        cls.users = []
        for index, name in enumerate(('Вера', 'Борис', 'Анна')):
            user = User.objects.create_user('ng_{}'.format(index))
            profile = Profile.objects.create(user=user, first_name=name, last_name='Т')
            ProfileTeamMembership.objects.create(profile=profile, team=cls.team)
            cls.users.append(user)
        cls.vera, cls.boris, cls.anna = cls.users
        now = timezone.now()
        cls.game = Game.objects.create(
            id='ng_game', name='Осенний кубок', author='a', author_extra='', project_id='main',
            is_ready=True, is_tournament=True, start_time=now + timedelta(minutes=30), end_time=now + timedelta(hours=3),
        )
        Registration.objects.create(team=cls.team, game=cls.game)

    def setUp(self):
        cache.clear()
        # Vera and Boris opt in; Anna keeps the default.
        set_channels(self.vera.pk, 'games', site=True, telegram=False)
        set_channels(self.boris.pk, 'games', site=True, telegram=True)

    def kinds(self, user):
        return [item['kind'] for item in service.inbox(user)]


class GamesGroupTests(GameNotificationFixture):
    def test_games_are_off_by_default(self):
        self.assertEqual(channels(self.anna.pk, 'games'), (False, False))
        self.assertIsNone(service.notify(self.anna, 'game.starts_soon', game_id=self.game.id))

    def test_registration_tells_the_teammates(self):
        game_events.notify_team_registered(self.team, self.game, actor=self.vera)
        self.assertEqual(self.kinds(self.vera), [])  # she registered
        self.assertEqual(self.kinds(self.anna), [])  # opted out
        item = service.inbox(self.boris)[0]
        self.assertEqual(item['title'], 'Команда «Овцы» зарегистрирована на «Осенний кубок»')
        self.assertTrue(item['body'].startswith('Старт '))

    def test_starts_soon_once(self):
        game_events.sweep()
        cache.clear()  # even without the marker it does not repeat
        game_events.sweep()
        self.assertEqual(self.kinds(self.vera), ['game.starts_soon'])
        self.assertEqual(Notification.objects.filter(user=self.boris, kind='game.starts_soon').count(), 1)
        self.assertEqual(service.inbox(self.vera)[0]['title'], 'Скоро начнётся «Осенний кубок»')

    def test_not_for_games_far_away(self):
        Game.objects.filter(pk=self.game.pk).update(start_time=timezone.now() + timedelta(hours=5))
        game_events.sweep()
        self.assertEqual(self.kinds(self.vera), [])

    def test_results_with_place(self):
        now = timezone.now()
        Game.objects.filter(pk=self.game.pk).update(start_time=now - timedelta(hours=3), end_time=now - timedelta(minutes=10))
        with patch.object(game_events, '_team_places', return_value={self.team.pk: (2, 14)}):
            game_events.sweep(now=now)
            game_events.sweep(now=now)
        item = service.inbox(self.vera)[0]
        self.assertEqual(item['title'], 'Итоги «Осенний кубок»')
        self.assertEqual(item['body'], '«Овцы» — 2 место из 14.')
        self.assertEqual(Notification.objects.filter(kind='game.results').count(), 2)

    def test_results_wait_for_pending_checks(self):
        now = timezone.now()
        Game.objects.filter(pk=self.game.pk).update(start_time=now - timedelta(hours=3), end_time=now - timedelta(minutes=10))
        with patch('games.ops.telegram.channels.scheduling._game_has_pending_attempts', return_value=True), \
                patch.object(game_events, '_team_places') as places:
            game_events.sweep(now=now)
        places.assert_not_called()
        self.assertEqual(self.kinds(self.vera), [])

    def test_broadcast_lands_in_the_inbox(self):
        from games.ops.telegram.bot.commands import _cmd_broadcast

        with patch('games.realtime.track_compat._broadcast_game_track_event_commit'):
            reply = _cmd_broadcast([self.game.id, 'Задание', '5', 'исправлено'])
            _cmd_broadcast([self.game.id, 'Ещё', 'одно'])
        self.assertIn('во входящие: 2', reply)
        bodies = [item['body'] for item in service.inbox(self.vera)]
        self.assertEqual(bodies, ['Ещё одно', 'Задание 5 исправлено'])


@override_settings(SITE_BASE_URL='https://interoves.com')
class OfferPublishedTests(TestCase):
    def test_author_hears_when_the_edition_goes_live(self):
        from games.puzzles.salad.models import WordSaladOffer
        from games.tests.test_feedback import _ensure_reference_rows

        _ensure_reference_rows()
        author = User.objects.create_user('np_author')
        Profile.objects.create(user=author, first_name='А', last_name='Б')
        game, _ = Game.objects.get_or_create(
            id='salad', defaults={'name': 'Салатик', 'author': 't', 'author_extra': '', 'project_id': 'main', 'is_ready': True},
        )
        today = timezone.now().astimezone(timezone.get_default_timezone()).date()
        game.tags = {'word_salad_publish_start': (today - timedelta(days=2)).isoformat()}
        game.save()
        link = GameTaskGroup.objects.create(game=game, task_group=TaskGroup.objects.create(label='np'), number='3', name='3')
        WordSaladOffer.objects.create(
            user=author, kind=WordSaladOffer.KIND_FULL, status=WordSaladOffer.STATUS_ACCEPTED, accepted_link=link,
        )
        game_events.sweep()
        game_events.sweep()
        items = service.inbox(author)
        self.assertEqual([item['title'] for item in items], ['Сегодня в выпуске №3 — ваш салатик'])
        self.assertEqual(items[0]['url'], '/salad/3/')


class ReminderPromptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('nr_user')
        cls.profile = Profile.objects.create(
            user=cls.user, first_name='В', last_name='З', telegram_user_id=55, telegram_verified=True,
        )

    def render(self, game_id='ladder'):
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get('/ladder/3/')
        request.user = self.user
        html = Template('{% include "site/partials/daily_reminder_prompt.html" %}').render(
            Context({'request': request, 'game': type('G', (), {'id': game_id})()}),
        )
        return html

    def test_offered_until_enabled(self):
        html = self.render()
        self.assertIn('Напомнить о следующей лесенке?', html)
        self.assertIn('в 16:30', html)
        self.assertIn('Напомнить о следующем салатике?', self.render('salad'))
        self.client.force_login(self.user)
        response = self.client.post(reverse('new_daily_reminder_enable'), {'game_id': 'ladder'})
        self.assertEqual(response.json(), {'ok': True})
        self.assertEqual(DailyReminder.objects.get(user=self.user).mode, 'channel')
        self.assertNotIn('data-daily-reminder-prompt', self.render())

    def test_not_without_telegram_or_for_other_games(self):
        self.assertNotIn('data-daily-reminder-prompt', self.render('walls'))
        Profile.objects.filter(pk=self.profile.pk).update(telegram_verified=False)
        self.user.refresh_from_db()
        self.assertNotIn('data-daily-reminder-prompt', self.render())
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('new_daily_reminder_enable'), {'game_id': 'ladder'}).status_code, 404)
