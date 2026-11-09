from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from games.daily import entry
from games.models import (
    DailyGameDifficulty,
    Game,
    GameTaskGroup,
    Like,
    PlayerCompletedGame,
    PlayerStartedGame,
    Task,
    TaskGroup,
)


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

    def pick(self, game=None, votes=None, newcomers=None, **actor):
        game = game or self.game
        patches = [patch.object(entry, 'free_published_placements', return_value=self.links)]
        if votes is not None:
            patches.append(patch.object(entry, 'like_votes', side_effect=lambda links: {
                link.pk: votes.get(link.number, (0, 0)) for link in links}))
        if newcomers is not None:
            patches.append(patch.object(entry, 'newcomer_finish_counts', side_effect=lambda links: {
                link.pk: newcomers.get(link.number, (0, 0)) for link in links}))
        for item in patches:
            item.start()
        try:
            link = entry.newcomer_placement(game, **actor)
        finally:
            for item in patches:
                item.stop()
        return link.number if link else None

    def test_without_likes_or_newcomers_the_easiest_eligible_rating_wins(self):
        self.assertEqual(self.pick(), '19')  # 1★: newcomers finish it most often

    def test_skips_puzzles_the_visitor_already_finished(self):
        user = User.objects.create_user('entry-player')
        for number in ('19', '18'):
            link = next(link for link in self.links if link.number == number)
            PlayerCompletedGame.objects.create(
                game=self.game, task_group=link.task_group, game_kind='ladder',
                game_instance_id=f'ladder-{number}', user=user,
            )
        self.assertEqual(self.pick(user=user), '14')  # 4★ prior 0.62 > 3★ 0.58
        self.assertEqual(self.pick(anon_key='someone-else'), '19')

    def test_newcomers_who_did_not_finish_outweigh_the_star_prior(self):
        self.assertEqual(self.pick(newcomers={'19': (40, 8)}), '18')

    def test_a_disliked_easy_puzzle_loses_to_a_liked_harder_one(self):
        votes = {'19': (2, 20), '18': (3, 15), '17': (40, 2)}
        self.assertEqual(self.pick(votes=votes), '17')

    def test_real_likes_are_counted(self):
        older = next(link for link in self.links if link.number == '16')
        task = Task.objects.create(task_group=older.task_group, number='1', task_type='raddle', text='')
        for n in range(3):
            Like.manager.create(task=task, value=1, anon_key=f'fan-{n}')
        Like.manager.create(task=task, value=-1, anon_key='critic')
        votes = entry.like_votes([older])
        self.assertEqual(votes[older.pk], (3, 1))

    def test_a_shorter_salad_breaks_a_tie(self):
        for row in DailyGameDifficulty.objects.exclude(placement__number__in=('17', '16')):
            row.stars = 5
            row.payload = {**(row.payload or {}), 'stars': 5, 'n': 50, 'is_visible': True}
            row.save()
        salad = Game.objects.get(id='salad')
        for longer, shorter in (('16', '17'), ('17', '16')):
            sizes = {link.pk: (8 if link.number == shorter else 12) for link in self.links}
            with patch.object(entry, 'salad_word_counts', return_value=sizes):
                self.assertEqual(self.pick(game=salad), shorter)

    def test_never_picks_five_stars_or_unrated(self):
        DailyGameDifficulty.objects.filter(stars__in=(1, 2, 3, 4)).delete()
        self.assertIsNone(self.pick())


class NewcomerFinishCountsTests(TestCase):
    def setUp(self):
        self.game = Game.objects.get(id='ladder')
        self.groups = [TaskGroup.objects.create(label=f'finish:{n}') for n in range(2)]

    def _start(self, group, at, **actor):
        row = PlayerStartedGame.objects.create(
            game=self.game, task_group=group, game_kind='ladder',
            game_instance_id=f'ladder:{group.pk}', **actor,
        )
        PlayerStartedGame.objects.filter(pk=row.pk).update(started_at=at)

    def _finish(self, group, **actor):
        PlayerCompletedGame.objects.create(
            game=self.game, task_group=group, game_kind='ladder',
            game_instance_id=f'ladder:{group.pk}', **actor,
        )

    def test_counts_only_players_whose_first_game_it_was(self):
        first, second = self.groups
        now = timezone.now()
        user = User.objects.create_user('finish-user')
        self._start(second, now, anon_key='new-a')
        self._finish(second, anon_key='new-a')
        self._start(second, now, user=user)
        self._start(first, now - timedelta(days=3), anon_key='old-b')
        self._start(second, now, anon_key='old-b')
        self._finish(second, anon_key='old-b')

        counts = entry._compute_newcomer_finish_counts([first.pk, second.pk])

        self.assertEqual(counts[second.pk], (2, 1))
        self.assertEqual(counts[first.pk], (1, 0))


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
