from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import AnonymousUser, User
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from games.daily.activity import active_days
from games.models import ClubEntitlement, Game, PlayerCompletedGame, Profile, TaskGroup
from games.site.http.context import club_archive_ui, daily_player, signup_prompt

MOSCOW = ZoneInfo('Europe/Moscow')


class EngagementPromptTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = Game.objects.get(id='ladder')
        self.group = TaskGroup.objects.create(label='ladder:engagement')
        self.user = User.objects.create_user('regular')
        Profile.objects.get_or_create(user=self.user)
        self._seq = 0

    def _complete(self, when, *, user=None, anon_key=None):
        self._seq += 1
        row = PlayerCompletedGame.objects.create(
            game=self.game, task_group=self.group, game_kind='ladder',
            game_instance_id='ladder-{}'.format(self._seq), user=user, anon_key=anon_key,
        )
        PlayerCompletedGame.objects.filter(pk=row.pk).update(completed_at=when)

    def _play_days(self, days, **actor):
        start = timezone.now() - timedelta(days=days + 1)
        for day in range(days):
            self._complete(start + timedelta(days=day), **actor)

    def _request(self, user, path='/'):
        request = RequestFactory().get(path)
        request.user = user
        return request

    def test_active_days_are_distinct_moscow_dates(self):
        # 22:30 UTC is already the next day in Moscow.
        self._complete(datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc), user=self.user)
        self._complete(datetime(2026, 10, 1, 12, 0, tzinfo=dt_timezone.utc), user=self.user)
        self._complete(datetime(2026, 10, 1, 22, 30, tzinfo=dt_timezone.utc), user=self.user)
        self._complete(datetime(2026, 10, 3, 9, 0, tzinfo=MOSCOW), anon_key='someone-else')
        self.assertEqual(active_days(user=self.user), 2)
        self.assertEqual(active_days(anon_key='someone-else'), 1)

    @override_settings(CLUB_ARCHIVE_GATING_ENABLED=True)
    def test_club_offer_goes_to_regulars_without_access(self):
        self._play_days(6, user=self.user)
        self.assertFalse(club_archive_ui(self._request(self.user))['club_archive_offer_available'])
        cache.clear()
        self._complete(timezone.now(), user=self.user)
        context = club_archive_ui(self._request(self.user))
        self.assertTrue(context['club_archive_offer_available'])
        self.assertEqual(context['club_offer_active_days'], 7)
        self.assertEqual(context['club_offer_member_count'], 0)
        self.assertFalse(club_archive_ui(self._request(self.user, '/subscription/'))['club_archive_offer_available'])

    @override_settings(CLUB_ARCHIVE_GATING_ENABLED=True)
    def test_club_offer_respects_never_later_cooldown_and_membership(self):
        self._play_days(10, user=self.user)
        profile = self.user.profile
        for field, value in (
            ('club_archive_offer_never', True),
            ('club_archive_offer_deferred_until', timezone.now() + timedelta(days=3)),
            ('club_archive_offer_last_shown_at', timezone.now() - timedelta(days=13)),
        ):
            Profile.objects.filter(pk=profile.pk).update(**{field: value})
            self.user.refresh_from_db()
            self.assertFalse(
                club_archive_ui(self._request(self.user))['club_archive_offer_available'], field,
            )
            Profile.objects.filter(pk=profile.pk).update(
                club_archive_offer_never=False, club_archive_offer_deferred_until=None,
                club_archive_offer_last_shown_at=None,
            )
        self.user.refresh_from_db()
        self.assertTrue(club_archive_ui(self._request(self.user))['club_archive_offer_available'])
        ClubEntitlement.objects.create(
            user=self.user, kind=ClubEntitlement.KIND_MANUAL, starts_at=timezone.now() - timedelta(hours=1),
        )
        self.assertFalse(club_archive_ui(self._request(self.user))['club_archive_offer_available'])

    @override_settings(CLUB_ARCHIVE_GATING_ENABLED=True)
    def test_club_offer_shows_days_and_member_count(self):
        member = User.objects.create_user('member')
        ClubEntitlement.objects.create(
            user=member, kind=ClubEntitlement.KIND_MANUAL, starts_at=timezone.now() - timedelta(days=1),
        )
        self._play_days(12, user=self.user)
        self.client.force_login(self.user)
        html = self.client.get('/').content.decode()
        self.assertIn('Вы играете с нами уже 12 дней', html)
        self.assertIn('В клубе уже 1 игрок.', html)
        self.assertIn('club_offer_shown', html)

    def test_signup_prompt_needs_three_days_of_anonymous_play(self):
        request = self._request(AnonymousUser())
        with patch('games.ops.analytics.identity.gameplay_anon_key', return_value='anon-regular'):
            self._play_days(2, anon_key='anon-regular')
            self.assertFalse(signup_prompt(request)['signup_prompt']['show'])
            cache.clear()
            self._complete(timezone.now(), anon_key='anon-regular')
            prompt = signup_prompt(request)['signup_prompt']
            self.assertTrue(prompt['show'])
            self.assertEqual(prompt['days'], 3)
        self.assertFalse(signup_prompt(self._request(self.user))['signup_prompt']['show'])

    def test_signup_prompt_card_renders_only_when_shown(self):
        html = render_to_string('site/partials/signup_prompt.html', {'signup_prompt': {'show': True, 'days': 4}})
        self.assertIn('Вы играете уже 4 дня — сохраните прогресс', html)
        self.assertIn('data-login-open', html)
        self.assertIn('signup_prompt_shown', html)
        self.assertNotIn(
            'data-signup-prompt',
            render_to_string('site/partials/signup_prompt.html', {'signup_prompt': {'show': False, 'days': 1}}),
        )

    def test_daily_player_counts_days_for_visitors_and_users(self):
        with patch('games.ops.analytics.identity.gameplay_anon_key', return_value='anon-new'):
            self.assertEqual(dict(daily_player(self._request(AnonymousUser()))['daily_player']),
                             {'days': 0, 'newcomer': True})
        self._play_days(2, user=self.user)
        self.assertEqual(daily_player(self._request(self.user))['daily_player']['days'], 2)

    def test_another_game_hint_goes_to_first_day_players_only(self):
        game = {'id': 'salad'}
        html = render_to_string('site/partials/another_game_hint.html', {'daily_player': {'days': 1}, 'game': game})
        self.assertIn('href="/salad/first/"', html)
        self.assertIn('another_game_click', html)
        self.assertIn('new-daily-game-header__nav', html)
        self.assertNotIn(
            'data-another-game',
            render_to_string('site/partials/another_game_hint.html', {'daily_player': {'days': 2}, 'game': game}),
        )

    def test_channel_prompt_waits_for_a_returning_player(self):
        game = {'id': 'salad'}

        def render(days):
            return render_to_string('site/partials/channel_prompt.html', {'daily_player': {'days': days}, 'game': game})
        self.assertNotIn('data-channel-prompt', render(1))
        self.assertIn('https://t.me/interoves', render(2))
        self.assertIn('data-channel-prompt', render(6))
        self.assertNotIn('data-channel-prompt', render(7))
        self.assertNotIn('data-another-game', render_to_string(
            'site/partials/another_game_hint.html', {'daily_player': {'days': 2}, 'game': game}))

    def test_locked_random_game_opens_the_club_paywall(self):
        for game in ('censorly', 'alphabetty'):
            html = render_to_string(f'site/partials/{game}_random_button.html', {
                f'random_{game}_has_access': False, 'daily_player': {'days': 3, 'newcomer': False},
            })
            self.assertIn(f'data-club-paywall-open="random_{game}"', html)
            self.assertIn(f'data-club-paywall="random_{game}"', html)
            self.assertIn('club_paywall_click', html)

    def _solve(self, game_id, groups, user):
        game = Game.objects.get(id=game_id)
        for group in groups:
            PlayerCompletedGame.objects.create(
                game=game, task_group=group, game_kind=game_id,
                game_instance_id='{}-{}'.format(game_id, group.pk), user=user,
            )

    def test_archive_pitch_names_the_game_with_the_most_to_come_back_to(self):
        from games.club.offers import archive_pitch as pitch_module
        groups = [TaskGroup.objects.create(label='pitch:{}'.format(n)) for n in range(40)]
        archive = {
            'salad': [g.pk for g in groups[:30]],   # 8 solved below, 22 left
            'ladder': [g.pk for g in groups[30:36]],  # 6 solved, 0 left
            'alphabetty': [],
        }
        self._solve('salad', groups[:8], self.user)
        self._solve('ladder', groups[30:36] + groups[36:40], self.user)
        with patch.object(pitch_module, 'archive_task_group_ids', side_effect=lambda game_id: archive[game_id]):
            pitch = pitch_module.archive_pitch(self.user)
            self.assertEqual((pitch.game_id, pitch.solved, pitch.unsolved_in_archive), ('salad', 8, 22))
            # Fewer than five solved is not «many».
            other = User.objects.create_user('few')
            self._solve('salad', groups[:4], other)
            self.assertIsNone(pitch_module.archive_pitch(other))

    def test_club_offer_shows_the_pitch(self):
        from games.club.offers.archive_pitch import ArchivePitch
        html = render_to_string('site/partials/club_offer_modal.html', {
            'club_archive_offer_available': True, 'club_offer_active_days': 9, 'club_offer_member_count': 24,
            'club_offer_pitch': ArchivePitch('salad', 'Салатик,Салатика,Салатиков', 18, 56),
        })
        self.assertIn('Вы решили 18 Салатиков', html)
        self.assertIn('ещё 56 Салатиков', html)
        self.assertIn('data-club-offer-pitch="salad"', html)
