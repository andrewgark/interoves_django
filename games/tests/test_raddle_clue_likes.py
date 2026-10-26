"""Лайки подсказок лесенки и суммы лайков в списке групп заданий."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.utils import timezone

from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    Like,
    RaddleClueLike,
    Task,
    TaskGroup,
)
from games.ops.analytics.identity import attach_anon_cookie
from games.task_group.likes_meta import like_totals_by_task_group_id
from games.tests.test_raddle_send_attempt import MINI_LADDER, _ensure_fixtures


class RaddleClueLikeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()
        now = timezone.now()
        with patch('games.realtime.track_compat.track_task_change'):
            cls.game = Game.objects.create(
                id='raddle_clue_like_test',
                name='Raddle clue likes',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
                is_tournament=False,
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=1),
            )
            cls.tg = TaskGroup.objects.create(label='raddle_clue_like_tg')
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='L1')
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(MINI_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD',
            )
        cls.anon_key = 'raddle-clue-like-anon'

    def setUp(self):
        self.client = Client()
        attach_anon_cookie(self.client, self.anon_key)

    def _react(self, hint_index, likes=0, dislikes=0):
        return self.client.post(
            '/like-dislike/{}/clue/{}/'.format(self.task.id, hint_index),
            {'likes': likes, 'dislikes': dislikes, 'game_id': self.game.id, 'anon_key': self.anon_key},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_like_switch_and_remove(self):
        data = self._react(0, likes=1).json()
        self.assertEqual(data, {'likes': 1, 'dislikes': 0, 'liked': True, 'disliked': False})
        like = RaddleClueLike.objects.get(task=self.task, hint_index=0)
        self.assertEqual(like.anon_key, self.anon_key)
        self.assertEqual(like.clue_text, 'A ____')

        self._react(0, likes=1)
        self.assertEqual(RaddleClueLike.objects.filter(task=self.task).count(), 1)

        data = self._react(0, dislikes=1).json()
        self.assertEqual(data, {'likes': 0, 'dislikes': 1, 'liked': False, 'disliked': True})

        data = self._react(0, dislikes=-1).json()
        self.assertEqual(data, {'likes': 0, 'dislikes': 0, 'liked': False, 'disliked': False})
        self.assertFalse(RaddleClueLike.objects.exists())
        # Task likes are a separate thing.
        self.assertFalse(Like.manager.exists())

    def test_rejects_bad_index_and_payload(self):
        self.assertEqual(self._react(3, likes=1).status_code, 404)
        self.assertEqual(self._react(0, likes=1, dislikes=1).status_code, 400)

    def test_buttons_appear_on_used_clue_only(self):
        self._react(0, likes=1)
        resp = self.client.post(
            '/send_attempt/{}/'.format(self.task.id),
            {'game_id': self.game.id, 'anon_key': self.anon_key, 'word_index': 1, 'word': 'BBB'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        update = resp.json()['update_task_html_new']
        html = ''.join(update.values()) if isinstance(update, dict) else update
        self.assertEqual(html.count('data-clue-like-btn'), 1)
        self.assertIn('data-hint-index="0" data-liked="1"', html)


class TaskGroupLikeTotalsTests(TestCase):
    def test_totals_summed_per_group_and_rendered(self):
        tg = TaskGroup.objects.create(label='like totals')
        empty_tg = TaskGroup.objects.create(label='like totals empty')
        t1 = Task.objects.create(task_group=tg, number='1')
        t2 = Task.objects.create(task_group=tg, number='2')
        Task.objects.create(task_group=empty_tg, number='1')
        Like.manager.create(task=t1, anon_key='a', value=1)
        Like.manager.create(task=t2, anon_key='b', value=1)
        Like.manager.create(task=t2, anon_key='c', value=-1)

        totals = like_totals_by_task_group_id([tg.id, empty_tg.id])

        self.assertEqual(totals, {tg.id: {'likes': 2, 'dislikes': 1}})
        html = render_to_string(
            'site/partials/like_totals_badge.html', {'like_totals': totals[tg.id]},
        )
        self.assertIn('Лайков: 2, дизлайков: 1', html)
        self.assertEqual(
            render_to_string('site/partials/like_totals_badge.html', {'like_totals': None}).strip(),
            '',
        )


class ClueLikeOwnershipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        tg = TaskGroup.objects.create(label='clue like owners')
        cls.task = Task.objects.create(task_group=tg, number='1', task_type='raddle')
        cls.user = User.objects.create_user('clue_like_user')
        cls.other = User.objects.create_user('clue_like_other')

    def test_anon_migration_moves_and_collapses(self):
        from games.identity.merge.anon_steps import (
            anon_migration_counts,
            claim_and_migrate_anon_history,
        )

        RaddleClueLike.objects.create(task=self.task, hint_index=0, anon_key='g1', value=1)
        RaddleClueLike.objects.create(task=self.task, hint_index=1, anon_key='g1', value=1)
        RaddleClueLike.objects.create(task=self.task, hint_index=1, user=self.user, value=-1)
        self.assertEqual(anon_migration_counts('g1')['raddle_clue_likes'], 2)

        result = claim_and_migrate_anon_history(self.user, 'g1')

        self.assertEqual(result['moved_likes'], 2)
        self.assertFalse(RaddleClueLike.objects.filter(anon_key='g1').exists())
        mine = dict(
            RaddleClueLike.objects.filter(user=self.user).values_list('hint_index', 'value')
        )
        # The reaction made while logged in wins over the guest one.
        self.assertEqual(mine, {0: 1, 1: -1})

    def test_anon_migration_step_batches(self):
        from games.identity.merge.anon_steps import migrate_anon_history_step

        for i in range(3):
            RaddleClueLike.objects.create(task=self.task, hint_index=i, anon_key='g2', value=1)

        first = migrate_anon_history_step(self.user, 'g2', 'likes', batch_size=2)
        self.assertFalse(first['complete'])
        second = migrate_anon_history_step(self.user, 'g2', 'likes', batch_size=2)
        self.assertTrue(second['complete'])
        self.assertEqual(RaddleClueLike.objects.filter(user=self.user).count(), 3)

    def test_account_merge(self):
        from games.puzzles.raddle.clue_likes import merge_user_clue_likes

        RaddleClueLike.objects.create(task=self.task, hint_index=0, user=self.other, value=1)
        RaddleClueLike.objects.create(task=self.task, hint_index=1, user=self.other, value=1)
        RaddleClueLike.objects.create(task=self.task, hint_index=1, user=self.user, value=-1)

        self.assertEqual(merge_user_clue_likes(self.user, self.other), 2)

        self.assertFalse(RaddleClueLike.objects.filter(user=self.other).exists())
        mine = dict(
            RaddleClueLike.objects.filter(user=self.user).values_list('hint_index', 'value')
        )
        self.assertEqual(mine, {0: 1, 1: -1})


class LikesMetaTests(TestCase):
    def test_two_queries_for_many_tasks(self):
        from games.task_group.likes_meta import likes_meta_by_task_id

        tg = TaskGroup.objects.create(label='likes meta')
        tasks = [Task.objects.create(task_group=tg, number=str(i)) for i in range(5)]
        Like.manager.create(task=tasks[0], anon_key='me', value=1)
        Like.manager.create(task=tasks[0], anon_key='x', value=-1)
        Like.manager.create(task=tasks[1], anon_key='me', value=-1)
        # Orphaned rows (actor deleted) count in totals but are nobody's own reaction.
        Like.manager.create(task=tasks[2], value=1)

        with self.assertNumQueries(2):
            meta = likes_meta_by_task_id(tasks, team=None, user=None, anon_key='me')

        self.assertEqual(meta[tasks[0].id], {'likes': 1, 'dislikes': 1, 'liked': True, 'disliked': False})
        self.assertEqual(meta[tasks[1].id], {'likes': 0, 'dislikes': 1, 'liked': False, 'disliked': True})
        self.assertEqual(meta[tasks[2].id], {'likes': 1, 'dislikes': 0, 'liked': False, 'disliked': False})
        self.assertEqual(meta[tasks[3].id], {'likes': 0, 'dislikes': 0, 'liked': False, 'disliked': False})
        with self.assertNumQueries(1):
            meta = likes_meta_by_task_id(tasks, team=None, user=None, anon_key=None)
        self.assertFalse(meta[tasks[2].id]['liked'])


class PostedReactionTests(TestCase):
    def _reaction(self, likes, dislikes):
        from django.test import RequestFactory

        from games.site.pages.play.reactions import _posted_reaction

        request = RequestFactory().post('/', {'likes': likes, 'dislikes': dislikes})
        return _posted_reaction(request)

    def test_values(self):
        self.assertEqual(self._reaction(1, 0), 1)
        self.assertEqual(self._reaction(0, 1), -1)
        self.assertEqual(self._reaction(-1, 0), 0)
        self.assertIsNone(self._reaction(0, 0))
        # Legacy jQuery plugin switches a reaction in one request.
        self.assertEqual(self._reaction(-1, 1), -1)
        self.assertEqual(self._reaction(1, -1), 1)
        self.assertEqual(self._reaction(1, 1).status_code, 400)
        self.assertEqual(self._reaction('x', 0).status_code, 400)
        self.assertEqual(self._reaction(2, 0).status_code, 400)
