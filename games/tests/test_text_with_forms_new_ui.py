"""New UI: text_with_forms tasks, blanks in task-group names, cards hidden until solved."""
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from games.models import Attempt, CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup
from games.site.views.game.render import render_new_ui_task_card_html, update_task_html
from games.task_group.task_group_context import build_task_group_task_context_dicts
from games.task_group.text_with_forms_task_data import title_with_blanks_html

ANON = 'anon_twf'


class TextWithFormsNewUiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        with patch('games.realtime.track_compat.track_task_change'):
            cls.game = Game.objects.create(
                id='twf_game', name='twf', author='test', author_extra='', project_id='sections',
            )
            cls.tg = TaskGroup.objects.create(label='twf_tg', points=1, tags={'text_with_forms_in_name': True})
            cls.placement = GameTaskGroup.objects.create(
                game=cls.game, task_group=cls.tg, number=1, name='Про ______ и ______',
            )
            cls.text_task = Task.objects.create(
                task_group=cls.tg, number='0', task_type='text_with_forms', checker=checker,
                text='Кот ______т, пёс ______ет. LEFT_ANSWERS',
            )
            cls.t1 = Task.objects.create(
                task_group=cls.tg, number='1', checker=checker, points=1, text='', answer='спи',
            )
            cls.t2 = Task.objects.create(
                task_group=cls.tg, number='2', checker=checker, points=1, text='', answer='ла',
            )
            cls.locked = Task.objects.create(
                task_group=cls.tg, number='3', checker=checker, points=1, text='секретный текст',
                answer='x', tags={'should_be_hidden_if_not_solved': [cls.t1.id, cls.t2.id]},
            )

    def _solve(self, task):
        Attempt.manager.bulk_create([
            Attempt(anon_key=ANON, task=task, game=self.game, text=task.answer, status='Ok', points=1),
        ])

    def _card(self, task):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        return render_new_ui_task_card_html(
            request, task, None, 'general', anon_key=ANON, game=self.game,
        )

    def test_blanks_become_answer_fields_for_following_tasks(self):
        html = self._card(self.text_task)
        self.assertNotIn('Не получилось показать', html)
        self.assertIn('action="/send_attempt/{}/"'.format(self.t1.id), html)
        self.assertIn('action="/send_attempt/{}/"'.format(self.t2.id), html)
        self.assertNotIn('______', html.split('new-twf', 1)[1])
        self.assertNotIn('id="id_text"', html)
        # LEFT_ANSWERS lists the fragments not guessed yet.
        self.assertIn('- ла<br>- спи<br>', html)
        # The text itself is not scored.
        self.assertNotIn('new-proportions-compact-points-pill', html)

    def test_solved_blank_shows_answer_instead_of_field(self):
        self._solve(self.t1)
        html = self._card(self.text_task)
        self.assertNotIn('action="/send_attempt/{}/"'.format(self.t1.id), html)
        self.assertIn('<strong class="new-twf__answer">спи</strong>', html)
        self.assertIn('action="/send_attempt/{}/"'.format(self.t2.id), html)
        self.assertNotIn('- спи<br>', html)

    def test_card_hidden_until_referenced_tasks_solved(self):
        html = self._card(self.locked)
        self.assertNotIn('секретный текст', html)
        self.assertIn('будут решены задания 1, 2', html)
        self._solve(self.t1)
        self._solve(self.t2)
        html = self._card(self.locked)
        self.assertIn('секретный текст', html)

    def test_task_group_heading_fills_solved_blanks(self):
        tasks = sorted(self.tg.tasks.visible(), key=lambda t: t.key_sort())
        ctx = build_task_group_task_context_dicts(
            self.game, self.tg, tasks, None, None, ANON, 'general', placement=self.placement,
        )
        self.assertEqual(ctx['tg_heading_html'].count('<span class="new-title-blank">______</span>'), 2)

    def test_attempt_update_refreshes_heading_with_earlier_solves(self):
        # A group without a text card: the heading still needs every task's attempts.
        checker = self.t1.checker
        with patch('games.realtime.track_compat.track_task_change'):
            tg = TaskGroup.objects.create(label='twf_title_tg', points=1, tags={'text_with_forms_in_name': True})
            GameTaskGroup.objects.create(game=self.game, task_group=tg, number=2, name='______ и ______')
            first = Task.objects.create(task_group=tg, number='1', checker=checker, points=1, text='', answer='ам')
            second = Task.objects.create(task_group=tg, number='2', checker=checker, points=1, text='', answer='ба')
        self._solve(first)
        self._solve(second)
        request = RequestFactory().post('/')
        request.user = AnonymousUser()
        fragments = update_task_html(request, second, None, 'general', anon_key=ANON, game=self.game)
        heading = fragments['update_task_html_new']['tg-heading']
        self.assertIn('id="new-task-tg-heading"', heading)
        self.assertIn('new-title-blank--solved">ам<', heading)
        self.assertIn('new-title-blank--solved">ба<', heading)

    def test_title_blank_i_maps_to_task_i(self):
        self._solve(self.t1)
        attempts = Attempt.manager.get_bulk_actor_attempts_infos(
            [self.t1.id, self.t2.id], team=None, user=None, anon_key=ANON, mode='general', game=self.game,
        )
        html = title_with_blanks_html('Про ______ и ______', [self.t1, self.t2], attempts)
        self.assertEqual(
            html,
            'Про <span class="new-title-blank new-title-blank--solved">спи</span>'
            ' и <span class="new-title-blank">______</span>',
        )
