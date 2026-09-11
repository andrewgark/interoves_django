from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase

from games.daily_streak import daily_streaks_for_user, streak_from_completion_dates
from games.models import Game, GameTaskGroup, PlayerCompletedGame, Project, TaskGroup


MOSCOW = ZoneInfo('Europe/Moscow')


class DailyStreakLogicTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project, _ = Project.objects.get_or_create(id='sections')
        cls.user = User.objects.create_user('streak-user')
        cls.now = datetime(2026, 9, 11, 9, 0, tzinfo=MOSCOW)
        cls.games = {}
        cls.links = {}
        for game_id, tag in (('ladder', 'ladder_publish_start'), ('alphabetty', 'alphabetty_publish_start')):
            game, _ = Game.objects.get_or_create(
                id=game_id,
                defaults={
                    'name': game_id,
                    'author': 'test',
                    'project': cls.project,
                    'tags': {tag: '2026-09-01T00:00:00+03:00'},
                },
            )
            Game.objects.filter(pk=game_id).update(
                project=cls.project,
                tags={tag: '2026-09-01T00:00:00+03:00'},
            )
            game.refresh_from_db()
            cls.games[game_id] = game
            cls.links[game_id] = {}
            for day in range(1, 12):
                tg = TaskGroup.objects.create(label='{}-{}-streak'.format(game_id, day))
                cls.links[game_id][day], _ = GameTaskGroup.objects.get_or_create(
                    game=game, number=str(day), defaults={'task_group': tg, 'name': str(day)},
                )

    def completion(self, game_id, day, completed_at, suffix=None):
        link = self.links[game_id][day]
        row = PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.games[game_id],
            task_group=link.task_group,
            game_kind=game_id,
            game_instance_id='{}-{}-{}'.format(game_id, day, suffix or completed_at.timestamp()),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        PlayerCompletedGame.objects.filter(pk=row.pk).update(completed_at=completed_at)

    def streak(self, *game_ids):
        return daily_streaks_for_user(
            self.user,
            games=[self.games[game_id] for game_id in game_ids],
            now=self.now,
        )

    def test_no_completed_daily_games(self):
        self.assertEqual(self.streak('ladder')['ladder'], 0)

    def test_today_only_is_one(self):
        self.completion('ladder', 11, self.now)
        self.assertEqual(self.streak('ladder')['ladder'], 1)

    def test_last_five_days_completed_on_time(self):
        for day in range(7, 12):
            self.completion('ladder', day, datetime(2026, 9, day, 18, tzinfo=MOSCOW))
        self.assertEqual(self.streak('ladder')['ladder'], 5)

    def test_unfinished_today_does_not_reset_streak(self):
        for day in range(6, 11):
            self.completion('ladder', day, datetime(2026, 9, day, 18, tzinfo=MOSCOW))
        self.assertEqual(self.streak('ladder')['ladder'], 5)

    def test_gap_yesterday_resets_before_today(self):
        self.completion('ladder', 9, datetime(2026, 9, 9, 18, tzinfo=MOSCOW))
        self.assertEqual(self.streak('ladder')['ladder'], 0)

    def test_today_after_gap_starts_one(self):
        self.completion('ladder', 9, datetime(2026, 9, 9, 18, tzinfo=MOSCOW))
        self.completion('ladder', 11, self.now)
        self.assertEqual(self.streak('ladder')['ladder'], 1)

    def test_late_completion_does_not_repair_gap(self):
        self.completion('ladder', 10, self.now)
        self.assertEqual(self.streak('ladder')['ladder'], 0)

    def test_daily_games_have_independent_streaks(self):
        self.completion('ladder', 11, self.now)
        self.completion('alphabetty', 10, datetime(2026, 9, 10, 18, tzinfo=MOSCOW))
        self.assertEqual(self.streak('ladder', 'alphabetty'), {'ladder': 1, 'alphabetty': 1})

    def test_moscow_midnight_is_used_for_completion_date(self):
        # 20:59 UTC is 23:59 in Moscow and still belongs to the 10 September edition.
        utc = ZoneInfo('UTC')
        self.completion('ladder', 10, datetime(2026, 9, 10, 20, 59, tzinfo=utc))
        self.assertEqual(self.streak('ladder')['ladder'], 1)

    def test_date_helper_keeps_unfinished_today(self):
        self.assertEqual(
            streak_from_completion_dates({self.now.date() - timedelta(days=1)}, today=self.now.date()),
            1,
        )
