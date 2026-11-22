"""The blue dot on edition arrows marks an open neighbour the player has not finished."""

import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from games.models import GameTaskGroup, PlayerCompletedGame
from games.tests.test_censorly import _make_puzzle_task


class DailyFreshDotTests(TestCase):
    def setUp(self):
        self.task_groups = {}
        for number in ('1', '2', '3'):
            game, task, _share_hash, _puzzle = _make_puzzle_task(
                title='Кот' + number, body='Кот' + number + ' сидит на окне.',
            )
            link = GameTaskGroup.objects.get(game=game, task_group=task.task_group)
            link.number = number
            link.name = 'Цензурка #' + number
            link.save(update_fields=['number', 'name'])
            self.task_groups[number] = task.task_group
        self.game = game
        start = (timezone.now() - timedelta(days=2)).date()
        tags = dict(game.tags or {})
        tags['censorly_publish_start'] = f'{start.isoformat()}T00:00:00+03:00'
        game.tags = tags
        game.is_ready = True
        game.save(update_fields=['tags', 'is_ready'])
        self.user = User.objects.create_user('fresh_dot', password='x', is_staff=True)
        self.client = Client()
        self.client.force_login(self.user)

    def _finish(self, number):
        task_group = self.task_groups[number]
        PlayerCompletedGame.objects.create(
            user=self.user, game=self.game, task_group=task_group, game_kind='censorly',
            game_instance_id=f'{self.game.id}:{task_group.pk}', result='solved',
        )

    def _page(self):
        response = self.client.get('/censorly/2/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_dot_marks_only_unfinished_neighbours(self):
        self._finish('1')
        html = self._page()
        header = re.search(r'<nav class="new-daily-game-header__nav".*?</nav>', html, re.S).group(0)
        pager = re.search(r'<nav class="new-tg-pager".*?</nav>', html, re.S).group(0)
        prev_link, next_link = re.findall(r'<a class="new-daily-game-header__nav-button.*?</a>', header, re.S)

        self.assertNotIn('new-fresh-dot', prev_link)
        self.assertIn('new-fresh-dot', next_link)
        self.assertIn('Следующий выпуск, ещё не решён', next_link)
        self.assertNotIn('ещё не', prev_link)
        self.assertEqual(pager.count('class="new-fresh-dot'), 1)
        self.assertIn('Цензурки №3<span class="new-fresh-dot', pager)

    def test_no_dot_when_both_neighbours_are_finished(self):
        self._finish('1')
        self._finish('3')
        html = self._page()
        header = re.search(r'<nav class="new-daily-game-header__nav".*?</nav>', html, re.S).group(0)
        pager = re.search(r'<nav class="new-tg-pager".*?</nav>', html, re.S).group(0)

        self.assertNotIn('new-fresh-dot', header)
        self.assertNotIn('new-fresh-dot', pager)

    def _nav_link(self, html, game_id):
        return re.search(
            r'<a class="new-nav__section"[^>]*data-nav-game="{}".*?</a>'.format(game_id), html, re.S,
        ).group(0)

    def test_nav_and_hub_mark_an_unfinished_newest_edition(self):
        self._finish('1')
        html = self.client.get('/').content.decode()

        self.assertIn('new-fresh-dot', self._nav_link(html, 'censorly'))
        self.assertIn('новый выпуск ещё не решён', self._nav_link(html, 'censorly'))
        self.assertIn('data-hub-fresh-dot', html)

    def test_nav_dot_clears_once_the_newest_edition_is_finished(self):
        self._finish('3')
        html = self.client.get('/').content.decode()

        self.assertNotIn('new-fresh-dot', self._nav_link(html, 'censorly'))
