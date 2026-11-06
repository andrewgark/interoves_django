from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from games.daily import entry
from games.models import DailyGameDifficulty, Game, GameTaskGroup, Like, PlayerCompletedGame, Task, TaskGroup


class NewcomerPlacementTests(TestCase):
    def setUp(self):
        self.game = Game.objects.get(id='ladder')
        self.links = []
        # Newest first, like free_published_placements returns them.
        for number, stars in ((20, 5), (19, 1), (18, 2), (17, 3), (16, 3), (15, None), (14, 4)):
            group = TaskGroup.objects.create(label=f'entry:{number}')
            link = GameTaskGroup.objects.create(
                game=self.game, task_group=group, number=str(number), name=f'#{number}',
            )
            # A difficulty row is created with the placement; give it a rating.
            DailyGameDifficulty.objects.update_or_create(placement=link, defaults={
                'stars': stars, 'n': 50 if stars else 0,
                'payload': {'stars': stars, 'n': 50, 'is_visible': True, 'is_preliminary': False} if stars else {},
            })
            self.links.append(link)

    def pick(self, **actor):
        with patch.object(entry, 'free_published_placements', return_value=self.links):
            link = entry.newcomer_placement(self.game, **actor)
        return link.number if link else None

    def test_prefers_the_newest_three_star_puzzle_over_easy_and_hard_ones(self):
        self.assertEqual(self.pick(), '17')

    def test_skips_puzzles_the_visitor_already_finished(self):
        user = User.objects.create_user('entry-player')
        for number in ('17', '16'):
            link = next(link for link in self.links if link.number == number)
            PlayerCompletedGame.objects.create(
                game=self.game, task_group=link.task_group, game_kind='ladder',
                game_instance_id=f'ladder-{number}', user=user,
            )
        self.assertEqual(self.pick(user=user), '18')
        self.assertEqual(self.pick(anon_key='someone-else'), '17')

    def test_censorly_newcomers_get_an_easier_article_first(self):
        censorly = Game.objects.get(id='censorly')
        for link in self.links:
            link.game = censorly
        with patch.object(entry, 'free_published_placements', return_value=self.links):
            self.assertEqual(entry.newcomer_placement(censorly).number, '18')  # the 2★ one

    def test_a_better_liked_puzzle_wins_within_the_same_stars(self):
        older = next(link for link in self.links if link.number == '16')  # 3★, like 17
        task = Task.objects.create(task_group=older.task_group, number='1', task_type='raddle', text='')
        for n in range(3):
            Like.manager.create(task=task, value=1, anon_key=f'fan-{n}')
        self.assertEqual(self.pick(), '16')

    def test_a_shorter_salad_wins_within_the_same_stars(self):
        salad = Game.objects.get(id='salad')
        sizes = {link.pk: (8 if link.number == '17' else 12) for link in self.links}
        with patch.object(entry, 'free_published_placements', return_value=self.links), \
                patch.object(entry, 'salad_word_counts', return_value=sizes):
            self.assertEqual(entry.newcomer_placement(salad).number, '17')
        sizes = {link.pk: (8 if link.number == '16' else 12) for link in self.links}
        with patch.object(entry, 'free_published_placements', return_value=self.links), \
                patch.object(entry, 'salad_word_counts', return_value=sizes):
            self.assertEqual(entry.newcomer_placement(salad).number, '16')

    def test_never_picks_one_or_five_stars(self):
        DailyGameDifficulty.objects.filter(stars__in=(2, 3, 4)).delete()
        self.assertIsNone(self.pick())


class NewcomerRedirectTests(TestCase):
    def test_redirects_to_the_chosen_puzzle_and_keeps_the_query(self):
        link = GameTaskGroup(number='117')
        with patch('games.daily.entry.newcomer_placement', return_value=link):
            response = self.client.get('/ladder/first/?utm_source=yandex&yclid=5')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/ladder/117/?utm_source=yandex&yclid=5')

    def test_falls_back_to_last_without_rated_puzzles(self):
        with patch('games.daily.entry.newcomer_placement', return_value=None):
            response = self.client.get('/censorly/first/?utm_campaign=x')
        self.assertEqual(response['Location'], '/censorly/last/?utm_campaign=x')

    def test_only_daily_games_have_an_entry(self):
        self.assertEqual(self.client.get('/walls/first/').status_code, 404)


class StartVariantMergeTests(TestCase):
    def test_signup_keeps_the_arm_the_anonymous_visitor_saw_first(self):
        from datetime import timedelta

        from django.utils import timezone

        from games.identity.merge.anon_steps import migrate_anon_analytics_state
        from games.models import PlayerAnalyticsState

        user = User.objects.create_user('arm-user')
        earlier = timezone.now() - timedelta(days=2)
        PlayerAnalyticsState.objects.create(anon_key='arm-anon', start_variant='ladder', start_viewed_at=earlier)
        PlayerAnalyticsState.objects.create(user=user, start_variant='salad', start_viewed_at=timezone.now())
        migrate_anon_analytics_state(user, 'arm-anon')
        state = PlayerAnalyticsState.objects.get(user=user)
        self.assertEqual(state.start_variant, 'ladder')
        self.assertFalse(PlayerAnalyticsState.objects.filter(anon_key='arm-anon').exists())
