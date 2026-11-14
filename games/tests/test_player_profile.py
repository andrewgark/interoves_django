from datetime import date, timedelta
from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from games.community.stats import day_streaks, head_to_head, player_stats
from games.community.visibility import player_urls_for_users
from games.models import (
    DailyAggregateSnapshot,
    DailyAggregateSnapshotRow,
    DailyAggregateSnapshotVersion,
    DailyResultProjection,
    DailySolveTiming,
    Game,
    PlayerCompletedGame,
    Profile,
    Project,
    TaskGroup,
    Team,
)
from games.models.people import PUBLIC_ID_ALPHABET, PUBLIC_ID_LENGTH, PersonalResultsParticipant
from games.results.table.context import attach_results_profile_links

DAILY_GAME_ID = 'walls'


class PublicIdTests(TestCase):
    def test_profile_and_team_get_public_ids_on_first_save(self):
        Project.objects.get_or_create(pk='main', defaults={})
        user = User.objects.create_user('pid_user')
        profile = Profile.objects.create(user=user, first_name='A', last_name='B')
        team = Team.objects.create(name='pid_team')
        for value in (profile.public_id, team.public_id):
            self.assertEqual(len(value), PUBLIC_ID_LENGTH)
            self.assertTrue(set(value) <= set(PUBLIC_ID_ALPHABET))

    def test_update_fields_save_persists_new_public_id(self):
        user = User.objects.create_user('pid_legacy')
        Profile.objects.create(user=user, first_name='A', last_name='B')
        Profile.objects.filter(user=user).update(public_id=None)
        profile = Profile.objects.get(user=user)
        profile.first_name = 'C'
        profile.save(update_fields=['first_name'])
        self.assertIsNotNone(Profile.objects.get(user=user).public_id)

    def test_existing_public_id_is_stable(self):
        user = User.objects.create_user('pid_stable')
        profile = Profile.objects.create(user=user, first_name='A', last_name='B')
        original = profile.public_id
        profile.last_name = 'Z'
        profile.save()
        self.assertEqual(Profile.objects.get(user=user).public_id, original)


class PlayerPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.owner = User.objects.create_user('pp_owner', password='secret')
        cls.viewer = User.objects.create_user('pp_viewer', password='secret')
        cls.profile = Profile.objects.create(
            user=cls.owner, first_name='Анна', last_name='Петрова',
            avatar_url='https://lh3.googleusercontent.com/anna.jpg',
            telegram_handle='anna_secret_tg', vk_url='vk.com/anna_secret_vk',
            email='anna-secret@example.com',
        )
        Profile.objects.create(user=cls.viewer, first_name='Вера', last_name='Зорина')
        cls.team = Team.objects.create(name='pp_team', visible_name='Сфинксы')
        cls.hidden_team = Team.objects.create(name='pp_hidden', visible_name='Тайные', is_hidden=True)
        cls.profile.add_team_membership(cls.team, make_primary=True)
        cls.profile.add_team_membership(cls.hidden_team)

    def setUp(self):
        # The anonymous header renders social login buttons.
        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)

    def url(self):
        return reverse('new_player_profile', kwargs={'public_id': self.profile.public_id})

    def set_flags(self, **flags):
        Profile.objects.filter(pk=self.profile.pk).update(**flags)

    def test_anonymous_visitor_sees_name_photo_teams_but_no_contacts(self):
        response = Client().get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Анна Петрова', body)
        self.assertIn('https://lh3.googleusercontent.com/anna.jpg', body)
        self.assertIn('Сфинксы', body)
        self.assertNotIn('Тайные', body)
        for secret in ('anna_secret_tg', 'anna_secret_vk', 'anna-secret@example.com'):
            self.assertNotIn(secret, body)
        self.assertIn('noindex', body)

    def test_unknown_profile_is_404(self):
        self.assertEqual(Client().get('/u/nosuchid/').status_code, 404)

    def test_admin_hidden_profile_still_has_a_page(self):
        # ``is_hidden`` only keeps the player out of results tables.
        self.set_flags(is_hidden=True)
        response = Client().get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertIn('Анна Петрова', response.content.decode())

    def test_hidden_page_shows_only_name_to_others(self):
        self.set_flags(page_hidden=True)
        body = Client().get(self.url()).content.decode()
        self.assertIn('Анна Петрова', body)
        self.assertIn('скрыл свою страницу', body)
        self.assertNotIn('Сфинксы', body)
        self.assertNotIn('https://lh3.googleusercontent.com/anna.jpg', body)

    def test_hidden_avatar_is_replaced_with_initials_for_others_only(self):
        self.set_flags(avatar_hidden=True)
        body = Client().get(self.url()).content.decode()
        self.assertNotIn('https://lh3.googleusercontent.com/anna.jpg', body)
        self.assertIn('АП', body)
        # The owner's public page previews what others see: no photo either,
        # with a note explaining why.
        owner = Client()
        owner.login(username='pp_owner', password='secret')
        body = owner.get(self.url()).content.decode()
        self.assertNotIn('https://lh3.googleusercontent.com/anna.jpg', body)
        self.assertIn('Фото скрыто', body)

    def test_owner_sees_own_hidden_page_with_note(self):
        self.set_flags(page_hidden=True)
        owner = Client()
        owner.login(username='pp_owner', password='secret')
        body = owner.get(self.url()).content.decode()
        self.assertIn('Страница скрыта', body)
        self.assertIn('Сфинксы', body)

    def test_profile_settings_save_privacy_flags(self):
        # The photo and its visibility have their own controls: the main form
        # must neither take a typed URL nor reset the photo switch.
        self.set_flags(avatar_hidden=True)
        owner = Client()
        owner.login(username='pp_owner', password='secret')
        response = owner.post(reverse('new_profile'), {
            'first_name': 'Анна', 'last_name': 'Петрова',
            'avatar_url': 'https://evil.example/typed.jpg',
            'timezone': 'Europe/Moscow', 'page_hidden': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.page_hidden)
        self.assertTrue(self.profile.avatar_hidden)
        self.assertEqual(self.profile.avatar_url, 'https://lh3.googleusercontent.com/anna.jpg')


class ResultsLinkTests(TestCase):
    def test_links_skip_hidden_pages_in_one_query(self):
        users = [User.objects.create_user('rl_{}'.format(i)) for i in range(3)]
        profiles = [Profile.objects.create(user=u, first_name='P', last_name=str(i)) for i, u in enumerate(users)]
        Profile.objects.filter(pk=profiles[1].pk).update(page_hidden=True)
        Profile.objects.filter(pk=profiles[2].pk).update(is_hidden=True)
        actors = [PersonalResultsParticipant(user_id=u.pk) for u in users]
        actors.append(PersonalResultsParticipant(anon_key='anon-key-1'))
        with self.assertNumQueries(1):
            data = attach_results_profile_links({'teams_sorted': actors})
        self.assertEqual(
            data['team_to_player_url'],
            {
                actors[0]: '/u/{}/'.format(profiles[0].public_id),
                actors[2]: '/u/{}/'.format(profiles[2].public_id),
            },
        )

    def test_no_query_without_personal_rows(self):
        with self.assertNumQueries(0):
            self.assertEqual(player_urls_for_users([]), {})


class PlayerStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        # A daily-timing game id; data migrations may already have created it.
        cls.game, _ = Game.objects.get_or_create(
            id=DAILY_GAME_ID,
            defaults={'name': 'Стенки', 'author': 'test', 'author_extra': '', 'project_id': 'main', 'is_ready': True},
        )
        cls.groups = [TaskGroup.objects.create(label='ps_{}'.format(i)) for i in range(4)]
        cls.me = User.objects.create_user('ps_me')
        cls.rival = User.objects.create_user('ps_rival')
        Profile.objects.create(user=cls.me, first_name='Я', last_name='Сам')
        Profile.objects.create(user=cls.rival, first_name='Со', last_name='Перник')

    def complete(self, user, group, *, when, result=PlayerCompletedGame.RESULT_SOLVED):
        row = PlayerCompletedGame.objects.create(
            user=user, game=self.game, task_group=group, game_kind='walls',
            game_instance_id='{}:{}'.format(self.game.pk, group.pk), result=result,
        )
        PlayerCompletedGame.objects.filter(pk=row.pk).update(completed_at=when)

    def project(self, user, group, score, ms=None):
        DailyResultProjection.objects.create(
            game=self.game, task_group=group, actor_type='user',
            actor_key=str(user.pk), user=user, score=score,
        )
        if ms is not None:
            DailySolveTiming.objects.create(
                game=self.game, task_group=group, user=user,
                status=DailySolveTiming.STATUS_COMPLETED,
                accumulated_ms=ms, frozen_ms=ms,
            )

    def test_day_streaks(self):
        today = date(2026, 11, 2)
        days = {today, today - timedelta(days=1), date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)}
        self.assertEqual(day_streaks(days, today=today), (2, 3))
        self.assertEqual(day_streaks({today - timedelta(days=1)}, today=today), (1, 1))
        self.assertEqual(day_streaks(set(), today=today), (0, 0))

    def test_player_stats_counts_personal_play_only(self):
        now = timezone.now()
        self.complete(self.me, self.groups[0], when=now - timedelta(days=1))
        self.complete(self.me, self.groups[1], when=now, result=PlayerCompletedGame.RESULT_FAILED)
        team = Team.objects.create(name='ps_team')
        PlayerCompletedGame.objects.create(
            team=team, game=self.game, task_group=self.groups[2], game_kind='walls',
            game_instance_id='team-instance',
        )
        self.project(self.me, self.groups[0], 5, ms=90_000)
        self.project(self.me, self.groups[1], 1, ms=150_000)
        with patch('games.daily.timing.streak.daily_streaks_for_actor', return_value={DAILY_GAME_ID: 0}):
            stats = player_stats(self.me, now=now)
        self.assertEqual(stats.total_played, 2)
        [game] = stats.games
        self.assertEqual((game.played, game.solved), (2, 1))
        self.assertEqual(game.median_display, '2:00')
        self.assertEqual(len(stats.activity), 16)
        self.assertTrue(all(len(week) == 7 for week in stats.activity))

    def test_month_place_uses_published_snapshot_rank(self):
        now = timezone.now()
        self.complete(self.me, self.groups[0], when=now)
        from games.daily.page.section import MOSCOW
        today = now.astimezone(MOSCOW).date()
        snapshot = DailyAggregateSnapshot.objects.create(game=self.game, month=date(today.year, today.month, 1))
        version = DailyAggregateSnapshotVersion.objects.create(snapshot=snapshot, published_at=now)
        DailyAggregateSnapshot.objects.filter(pk=snapshot.pk).update(published_version=version)
        hidden_team = Team.objects.create(name='ps_hidden', is_hidden=True)
        for actor_type, key, extra, score in (
            ('user', str(self.rival.pk), {'user': self.rival}, 10),
            ('team', hidden_team.pk, {'team': hidden_team}, 50),
            ('user', str(self.me.pk), {'user': self.me}, 7),
            ('anon', 'a1', {'anon_key': 'a1'}, 7),
        ):
            DailyAggregateSnapshotRow.objects.create(
                version=version, actor_type=actor_type, actor_key=key, score=score, **extra,
            )
        with patch('games.daily.timing.streak.daily_streaks_for_actor', return_value={}):
            stats = player_stats(self.me, now=now)
        [game] = stats.games
        self.assertEqual((game.month_place, game.month_total), (2, 3))
        self.assertEqual(
            game.month_results_url,
            '/walls/results/?month={:%Y-%m}&sort=time'.format(today),
        )
        from django.template.loader import render_to_string
        html = render_to_string('site/partials/daily_stats.html', {'stats': stats})
        self.assertIn('href="/walls/results/?month={:%Y-%m}&amp;sort=time"'.format(today), html)

    def test_head_to_head(self):
        self.project(self.me, self.groups[0], 5)
        self.project(self.rival, self.groups[0], 3)          # win by score
        self.project(self.me, self.groups[1], 4, ms=60_000)
        self.project(self.rival, self.groups[1], 4, ms=50_000)  # loss on time
        self.project(self.me, self.groups[2], 2)
        self.project(self.rival, self.groups[2], 2)          # tie, no timers
        self.project(self.me, self.groups[3], 9)             # not common
        result = head_to_head(self.me, self.rival)
        self.assertEqual((result.common, result.wins, result.losses, result.ties), (3, 1, 1, 1))


class ResultsRowTemplateTests(TestCase):
    def test_release_rows_link_only_mapped_players(self):
        from django.template.loader import render_to_string

        linked = PersonalResultsParticipant(user_id=1, display_name='Анна')
        plain = PersonalResultsParticipant(user_id=2, display_name='Вера')
        html = render_to_string('site/partials/results_rows.html', {
            'teams_sorted': [linked, plain],
            'team_to_player_url': {linked: '/u/abcd2345/'},
        })
        self.assertIn('<a class="results-actor-link" href="/u/abcd2345/">Анна</a>', html)
        self.assertNotIn('>Вера</a>', html)
        self.assertIn('Вера', html)

    def test_aggregate_rows_link_player_url(self):
        from django.template.loader import render_to_string

        rows = [
            {'actor_label': 'Анна', 'actor_kind': 'user', 'player_url': '/u/abcd2345/', 'cells': {}, 'cell_meta': {}},
            {'actor_label': 'Сфинксы', 'actor_kind': 'team', 'cells': {}, 'cell_meta': {}},
        ]
        html = render_to_string('site/partials/aggregate_results_rows.html', {'aggregate_rows': rows})
        self.assertIn('<a class="results-actor-link" href="/u/abcd2345/">Анна</a>', html)
        self.assertNotIn('>Сфинксы</a>', html)


class TrustedAvatarTests(TestCase):
    def test_only_provider_https_photos_are_shown_to_others(self):
        from games.community.visibility import is_trusted_avatar_url, public_avatar_url

        for url in (
            'https://sun9-1.userapi.com/a.jpg', 'https://lh3.googleusercontent.com/a',
            'https://avatars.yandex.net/get-yapic/1/islands-200', 'https://t.me/i/userpic/320/a.jpg',
        ):
            self.assertTrue(is_trusted_avatar_url(url), url)
        for url in (
            'http://lh3.googleusercontent.com/a', 'https://evil.example/a.jpg',
            'https://googleusercontent.com.evil.example/a', 'https://notvk.com/a', 'javascript:alert(1)', '',
        ):
            self.assertFalse(is_trusted_avatar_url(url), url)
        owner = User.objects.create_user('ta_owner')
        profile = Profile.objects.create(user=owner, first_name='A', last_name='B', avatar_url='https://evil.example/a.jpg')
        self.assertEqual(public_avatar_url(profile, User.objects.create_user('ta_other')), '')
        self.assertEqual(public_avatar_url(profile, owner), 'https://evil.example/a.jpg')
