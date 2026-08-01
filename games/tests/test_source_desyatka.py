"""Источник Десяточки для кругов Замен / Стен / Палиндромов."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, TaskGroup
from games.section_hub import get_source_desyatka_context


def _ensure_base():
    Project.objects.get_or_create(id='main')
    Project.objects.get_or_create(id='sections')
    CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


class SourceDesyatkaContextTests(TestCase):
    def setUp(self):
        _ensure_base()
        now = timezone.now()
        self.des, _ = Game.objects.update_or_create(
            id='des169',
            defaults={
                'name': 'Десяточка 169',
                'outside_name': 'Десяточка 169',
                'author': 'Interoves',
                'project_id': 'main',
                'is_ready': True,
                'is_playable': True,
                'start_time': now - timedelta(days=30),
                'end_time': now - timedelta(days=29),
                'answers_url': 'https://docs.google.com/document/d/answers169',
            },
        )
        self.hub, _ = Game.objects.update_or_create(
            id='replacements',
            defaults={
                'name': 'Замены',
                'author': 'Interoves',
                'project_id': 'sections',
                'is_ready': True,
                'is_playable': True,
            },
        )
        checker = CheckerType.objects.get(id='equals_with_possible_spaces')
        self.tg = TaskGroup.objects.create(label='Замены', checker=checker, points=1)
        GameTaskGroup.objects.create(
            game=self.des, task_group=self.tg, number='6', name='Замены (18+)',
        )
        GameTaskGroup.objects.create(
            game=self.hub, task_group=self.tg, number='169', name='Замены (18+)',
        )

    def test_resolves_desyatka_link_and_answers(self):
        ctx = get_source_desyatka_context(self.tg)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx['number'], '169')
        self.assertEqual(ctx['label'], 'Десяточки 169')
        self.assertEqual(ctx['url'], '/games/des169/')
        self.assertEqual(
            ctx['answers_url'],
            'https://docs.google.com/document/d/answers169',
        )

    def test_hides_answers_while_source_game_running(self):
        now = timezone.now()
        self.des.start_time = now - timedelta(hours=1)
        self.des.end_time = now + timedelta(hours=3)
        self.des.save(update_fields=['start_time', 'end_time'])
        ctx = get_source_desyatka_context(self.tg)
        self.assertEqual(ctx['url'], '/games/des169/')
        self.assertEqual(ctx['answers_url'], '')

    def test_none_without_des_link(self):
        checker = CheckerType.objects.get(id='equals_with_possible_spaces')
        orphan = TaskGroup.objects.create(label='orphan', checker=checker, points=1)
        GameTaskGroup.objects.create(
            game=self.hub, task_group=orphan, number='1', name='Легкие',
        )
        self.assertIsNone(get_source_desyatka_context(orphan))
