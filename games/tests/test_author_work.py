from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from games.community.authorship import player_author_work, team_author_work
from games.models import (
    CheckerType,
    Game,
    GameAuthor,
    GameTaskGroup,
    HTMLPage,
    PlayerCompletedGame,
    Profile,
    Project,
    TaskGroup,
    Team,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=dt_timezone.utc)


class AuthorWorkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        cls.ladder, _ = Game.objects.get_or_create(
            id='ladder', defaults={'name': 'Лесенка', 'author': 'test', 'author_extra': '', 'project_id': 'main'},
        )
        cls.ladder.name = 'Лесенка'
        cls.ladder.tags = {**(cls.ladder.tags or {}), 'ladder_publish_start': '2026-01-01'}
        cls.ladder.save()
        cls.author = User.objects.create_user('aw_author')
        cls.mate = User.objects.create_user('aw_mate')
        cls.profile = Profile.objects.create(user=cls.author, first_name='Анна', last_name='Петрова')
        cls.mate_profile = Profile.objects.create(user=cls.mate, first_name='Вера', last_name='Зорина')
        cls.team = Team.objects.create(name='aw_team', visible_name='Сфинксы')
        cls.profile.add_team_membership(cls.team, make_primary=True)

        with patch('games.realtime.track_compat.track_task_change'):
            for number, name in ((3, 'Лесенка #3'), (7, 'Морская'), (99999, 'Лесенка #99999')):
                group = TaskGroup.objects.create(label='aw-{}'.format(number))
                group.authors.add(cls.profile)
                GameTaskGroup.objects.create(game=cls.ladder, task_group=group, number=str(number), name=name)
            other = TaskGroup.objects.create(label='aw-other')
            GameTaskGroup.objects.create(game=cls.ladder, task_group=other, number='5', name='5')

        past = timezone.now() - timedelta(days=30)
        cls.mine = Game.objects.create(
            id='des90', name='Десяточка 90', author='Анна', project_id='main',
            is_ready=True, start_time=past,
        )
        cls.teams = Game.objects.create(
            id='des91', name='Десяточка 91', author='команда Сфинксы', project_id='main',
            is_ready=True, start_time=past,
        )
        cls.draft = Game.objects.create(
            id='des92', name='Десяточка 92', author='Анна', project_id='main', is_ready=False,
        )
        cls.future = Game.objects.create(
            id='des93', name='Десяточка 93', author='Анна', project_id='main',
            is_ready=True, start_time=timezone.now() + timedelta(days=3),
        )
        for game in (cls.mine, cls.draft, cls.future):
            GameAuthor.objects.create(game=game, profile=cls.profile)
        GameAuthor.objects.create(game=cls.teams, team=cls.team)

    def test_player_sees_published_daily_releases_and_desyatochki(self):
        work = player_author_work(self.author)
        [ladder] = work.daily
        self.assertEqual(ladder.name, 'Лесенка')
        self.assertEqual(
            [(item.title, item.url, item.published_on) for item in ladder.items],
            [('Морская', '/ladder/7/', date(2026, 1, 7)), ('#3', '/ladder/3/', date(2026, 1, 3))],
        )
        # Team grants stay on the team page: membership is not authorship.
        self.assertEqual([(item.title, item.url) for item in work.desyatochki], [('Десяточка 90', '/games/des90/')])

    def test_team_lists_only_desyatochki_granted_to_the_team(self):
        work = team_author_work(self.team)
        # Daily releases have personal authors only; a team owns its members'.
        self.assertEqual(work.daily_count, 2)
        # des90 is Анна's own grant: it stays on her page.
        self.assertEqual([item.url for item in work.desyatochki], ['/games/des91/'])

    def test_release_title_drops_only_the_whole_game_name(self):
        from types import SimpleNamespace

        from games.community.authorship import _release_title

        def title(name, number='4'):
            return _release_title(SimpleNamespace(name=name, number=number), 'Лесенка')

        self.assertEqual(title('Лесенка #4'), '#4')
        self.assertEqual(title('Лесенка: Морская'), 'Морская')
        self.assertEqual(title('4'), '#4')
        self.assertEqual(title('Лесенкин день'), 'Лесенкин день')

    def test_non_author_has_nothing(self):
        self.assertFalse(player_author_work(self.mate).has_any)
        self.assertEqual(render_to_string('site/partials/author_work.html', {
            'author_work': player_author_work(self.mate),
        }).strip(), '')

    def test_section_renders_links_and_folds_long_lists(self):
        for number in range(100, 112):
            game = Game.objects.create(
                id='des{}'.format(number), name='Десяточка {}'.format(number), author='Анна',
                project_id='main', is_ready=True, start_time=timezone.now() - timedelta(days=1),
            )
            GameAuthor.objects.create(game=game, profile=self.profile)
        html = render_to_string('site/partials/author_work.html', {
            'author_work': player_author_work(self.author),
        })
        self.assertIn('id="author"', html)
        self.assertIn('href="/ladder/7/"', html)
        self.assertIn('Десяточки · 13', html)
        self.assertIn('Ещё 3', html)
        self.assertNotIn('/games/des92/', html)

    def test_now_decides_what_is_published(self):
        work = player_author_work(self.author, now=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc))
        [ladder] = work.daily
        self.assertEqual([item.number for item in ladder.items], [3])

    def test_stats_count_matches_the_section(self):
        from games.community.stats import player_stats

        other = GameTaskGroup.objects.get(game=self.ladder, number='5')
        PlayerCompletedGame.objects.create(
            user=self.author, game=self.ladder, task_group=other.task_group,
            game_kind='ladder', game_instance_id='aw-play',
        )
        work = player_author_work(self.author)
        with patch('games.daily.timing.streak.daily_streaks_for_actor', return_value={}):
            [counted] = player_stats(self.author).games
            with patch('games.community.authorship.daily_releases') as recount:
                # The page passes its author work: no second count.
                [passed] = player_stats(self.author, author_work=work).games
        recount.assert_not_called()
        self.assertEqual((counted.authored, passed.authored), (2, work.daily_count))
