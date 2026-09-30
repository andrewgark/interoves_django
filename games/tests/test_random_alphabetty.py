from unittest.mock import patch

from django.test import TestCase

from games.alphabetty.random_game import (
    RandomAlphabettyDictionaryExhausted,
    get_or_create_random_game,
)
from games.difficulty import ensure_daily_difficulty_row, get_game_difficulty
from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    Project,
    RandomAlphabettyGame,
    PlayerCompletedGame,
    Task,
    TaskGroup,
)
from games.results.tables import load_results_placements_and_tasks


class RandomAlphabettyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        project, _ = Project.objects.get_or_create(id='sections')
        cls.game, _ = Game.objects.get_or_create(
            id='alphabetty',
            defaults={
                'name': 'Алфавитка',
                'author': 'Tests',
                'project': project,
                'is_ready': True,
                'is_playable': True,
                'requires_ticket': False,
                'tags': {'alphabetty_publish_start': '2026-01-01T00:00:00+03:00'},
            },
        )
        cls.checker, _ = CheckerType.objects.get_or_create(id='alphabetty')

    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО',))
    def test_same_word_reuses_hash_and_game(self, _pool):
        first = get_or_create_random_game(game=self.game)
        second = get_or_create_random_game(game=self.game)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.share_hash, second.share_hash)
        self.assertEqual(RandomAlphabettyGame.objects.filter(word='СЛОВО').count(), 1)
        self.assertEqual(
            GameTaskGroup.objects.filter(game=self.game, share_hash=first.share_hash).count(),
            1,
        )
        self.assertEqual(Task.objects.filter(task_group=first.task_group).count(), 1)

    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО',))
    def test_random_placement_is_not_daily_difficulty(self, _pool):
        random_game = get_or_create_random_game(game=self.game)
        placement = GameTaskGroup.objects.get(
            game=self.game,
            task_group=random_game.task_group,
        )

        self.assertIsNone(ensure_daily_difficulty_row(placement))
        self.assertIsNone(get_game_difficulty(placement))

    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО', 'ВТОРОЕ'))
    def test_random_words_are_not_repeated_until_dictionary_is_exhausted(self, _pool):
        first = get_or_create_random_game(game=self.game)
        second = get_or_create_random_game(game=self.game)

        self.assertNotEqual(first.word, second.word)
        with self.assertRaises(RandomAlphabettyDictionaryExhausted):
            get_or_create_random_game(game=self.game)

    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО',))
    def test_numeric_sorting_excludes_random_placement(self, _pool):
        random_game = get_or_create_random_game(game=self.game)
        regular_group = self._create_regular_placement('99999')

        placements = GameTaskGroup.sorted_links(game=self.game)
        list_placements = GameTaskGroup.sorted_links(
            list(GameTaskGroup.objects.filter(game=self.game)),
        )

        placement_ids = [placement.task_group_id for placement in placements]
        self.assertIn(regular_group.id, placement_ids)
        self.assertNotIn(random_game.task_group_id, [placement.task_group_id for placement in placements])
        self.assertNotIn(
            random_game.task_group_id,
            [placement.task_group_id for placement in list_placements],
        )

    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО',))
    def test_hash_results_are_loaded_as_scoped_placement(self, _pool):
        random_game = get_or_create_random_game(game=self.game)
        placements, groups, _tasks, _ids, _headers = load_results_placements_and_tasks(
            self.game,
            task_group_number=random_game.share_hash,
        )

        self.assertEqual([p.share_hash for p in placements], [random_game.share_hash])
        self.assertEqual(list(groups), [random_game.share_hash])

    @patch('games.views.alphabetty_views.has_club_access', return_value=False)
    def test_random_game_post_redirects_non_resident(self, _access):
        response = self.client.post('/alphabetty/random/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/subscription/')

    @patch('games.views.alphabetty_views.has_club_access', return_value=True)
    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО',))
    def test_random_game_post_reuses_existing_word_game(self, _pool, _access):
        first = self.client.post('/alphabetty/random/')
        second = self.client.post('/alphabetty/random/')

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first['Location'], second['Location'])
        self.assertEqual(RandomAlphabettyGame.objects.count(), 1)

    @patch('games.views.alphabetty_views.has_club_access', return_value=True)
    @patch('games.alphabetty.random_game.get_answer_pool', return_value=())
    def test_empty_random_dictionary_shows_message(self, _pool, _access):
        response = self.client.post('/alphabetty/random/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [str(message) for message in response.wsgi_request._messages],
            ['Словарь случайных алфавиток пока пуст. Попробуйте ещё раз позже.'],
        )

    @patch('games.views.alphabetty_views.has_club_access', return_value=True)
    @patch('games.alphabetty.random_game.get_answer_pool', return_value=('СЛОВО',))
    def test_random_game_has_daily_statistics_endpoint(self, _pool, _access):
        user = self._create_user('random-stats')
        self.client.force_login(user)
        response = self.client.post('/alphabetty/random/')
        random_game = RandomAlphabettyGame.objects.get()
        PlayerCompletedGame.objects.create(
            user=user,
            game=self.game,
            task_group=random_game.task_group,
            game_kind='alphabet',
            game_instance_id='alphabetty:{}'.format(random_game.task_group_id),
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

        statistics = self.client.get(
            '/daily-statistics/alphabetty/{}/'.format(random_game.share_hash),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(statistics.status_code, 200)
        self.assertEqual(statistics.json()['solved'], 1)

    def _create_user(self, username):
        from django.contrib.auth.models import User

        return User.objects.create_user(username=username, password='password')

    def _create_regular_placement(self, number):
        task_group = TaskGroup.objects.create(
            label='regular placement',
            checker=self.checker,
            points=1,
            max_attempts=3,
        )
        return GameTaskGroup.objects.create(
            game=self.game,
            task_group=task_group,
            number=number,
            name='regular placement',
        )
