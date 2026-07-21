import json
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from games.ladder_daily import LADDER_GAME_ID, LADDER_PUBLISH_START_TAG
from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Profile, Project, Task
from games.support.constants import SUPPORT_CONSOLE_GROUP
from games.support.services import ladders as ladder_svc


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    CheckerType.objects.get_or_create(pk='raddle')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


def _make_ladder_game():
    project = Project.objects.get(pk='sections')
    game, _ = Game.objects.get_or_create(
        pk=LADDER_GAME_ID,
        defaults={
            'name': 'Лесенка',
            'author': 'test',
            'project': project,
        },
    )
    tags = dict(game.tags or {})
    tags[LADDER_PUBLISH_START_TAG] = '2026-07-08T00:00:00+03:00'
    game.tags = tags
    game.project = project
    game.save(update_fields=['tags', 'project'])
    return game


class LadderSupportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.game = _make_ladder_game()

    def setUp(self):
        # Все создаваемые лесенки по умолчанию ещё не вышли — свободная перестановка.
        ladder_svc.set_publish_start('2099-01-01')

    def test_create_insert_shifts_numbers_and_dates(self):
        a = ladder_svc.create_ladder(
            at_number=1,
            words=['КОТ', 'РОТ'],
            hints=['первая буква'],
        )
        b = ladder_svc.create_ladder(
            at_number=2,
            words=['ДОМ', 'СОМ'],
            hints=['первая буква'],
        )
        self.assertEqual(a['number'], 1)
        self.assertEqual(b['number'], 2)

        inserted = ladder_svc.create_ladder(
            at_number=2,
            words=['ЛЕС', 'БЕС'],
            hints=['первая буква'],
        )
        self.assertEqual(inserted['number'], 2)

        rows = ladder_svc.list_ladder_rows()
        nums = [r.number for r in rows]
        self.assertEqual(nums, [1, 2, 3])
        # original #2 became #3
        self.assertEqual(rows[0].words_preview, 'КОТ → РОТ')
        self.assertEqual(rows[1].words_preview, 'ЛЕС → БЕС')
        self.assertEqual(rows[2].words_preview, 'ДОМ → СОМ')
        self.assertEqual(rows[0].publish_date, '2099-01-01')
        self.assertEqual(rows[1].publish_date, '2099-01-02')
        self.assertEqual(rows[2].publish_date, '2099-01-03')
        # internal ids of old ladders unchanged
        self.assertEqual(rows[0].link_id, a['link_id'])
        self.assertEqual(rows[2].link_id, b['link_id'])

    def test_reorder_updates_public_numbers_keeps_ids(self):
        first = ladder_svc.create_ladder(
            at_number=1, words=['ААА', 'БББ'], hints=['x']
        )
        second = ladder_svc.create_ladder(
            at_number=2, words=['ВВВ', 'ГГГ'], hints=['y']
        )
        third = ladder_svc.create_ladder(
            at_number=3, words=['ДДД', 'ЕЕЕ'], hints=['z']
        )
        rows = ladder_svc.reorder_ladders([
            third['link_id'],
            first['link_id'],
            second['link_id'],
        ])
        self.assertEqual([r.link_id for r in rows], [
            third['link_id'], first['link_id'], second['link_id'],
        ])
        self.assertEqual([r.number for r in rows], [1, 2, 3])
        link = GameTaskGroup.objects.get(pk=third['link_id'])
        self.assertEqual(link.number, '1')
        self.assertEqual(link.name, 'Лесенка #1')

    def test_cannot_reorder_published(self):
        ladder_svc.set_publish_start('2026-07-08')
        a = ladder_svc.create_ladder(at_number=1, words=['А', 'Б'], hints=['x'])
        b = ladder_svc.create_ladder(at_number=2, words=['В', 'Г'], hints=['y'])
        c = ladder_svc.create_ladder(at_number=3, words=['Д', 'Е'], hints=['z'])
        d = ladder_svc.create_ladder(at_number=4, words=['Ж', 'З'], hints=['w'])
        now = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        # №1 и №2 вышли; нельзя поставить вышедшую на место будущей
        with self.assertRaises(ladder_svc.LadderSupportError):
            ladder_svc.reorder_ladders(
                [c['link_id'], a['link_id'], b['link_id'], d['link_id']],
                now=now,
            )
        # нельзя поменять местами две вышедшие
        with self.assertRaises(ladder_svc.LadderSupportError):
            ladder_svc.reorder_ladders(
                [b['link_id'], a['link_id'], c['link_id'], d['link_id']],
                now=now,
            )
        # будущие между собой — ок, префикс вышедших на месте
        rows = ladder_svc.reorder_ladders(
            [a['link_id'], b['link_id'], d['link_id'], c['link_id']],
            now=now,
        )
        self.assertEqual(
            [r.link_id for r in rows],
            [a['link_id'], b['link_id'], d['link_id'], c['link_id']],
        )
        self.assertEqual([r.number for r in rows], [1, 2, 3, 4])
        self.assertEqual(GameTaskGroup.objects.get(pk=d['link_id']).number, '3')
        self.assertEqual(GameTaskGroup.objects.get(pk=c['link_id']).number, '4')

    def test_cannot_insert_among_published(self):
        ladder_svc.set_publish_start('2026-07-08')
        ladder_svc.create_ladder(at_number=1, words=['А', 'Б'], hints=['x'])
        ladder_svc.create_ladder(at_number=2, words=['В', 'Г'], hints=['y'])
        ladder_svc.create_ladder(at_number=3, words=['Д', 'Е'], hints=['z'])
        now = datetime(2026, 7, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        with self.assertRaises(ladder_svc.LadderSupportError):
            ladder_svc.create_ladder(
                at_number=1, words=['Л', 'М'], hints=['h'], now=now,
            )
        with self.assertRaises(ladder_svc.LadderSupportError):
            ladder_svc.create_ladder(
                at_number=2, words=['Л', 'М'], hints=['h'], now=now,
            )
        created = ladder_svc.create_ladder(
            at_number=3, words=['Л', 'М'], hints=['h'], now=now,
        )
        self.assertEqual(created['number'], 3)
        rows = ladder_svc.list_ladder_rows(now=now)
        self.assertEqual([r.number for r in rows], [1, 2, 3, 4])

    def test_update_content(self):
        created = ladder_svc.create_ladder(
            at_number=1, words=['СТАРТ', 'ФИНИШ'], hints=['заглушка']
        )
        detail = ladder_svc.update_ladder(
            created['link_id'],
            words=['МОРЕ', 'ГОРЕ'],
            hints=['первая буква'],
            intro='гостевая',
            author='Тест',
        )
        self.assertEqual(detail['words'], ['МОРЕ', 'ГОРЕ'])
        self.assertEqual(detail['author'], 'Тест')
        self.assertEqual(detail['intro'], 'гостевая')
        task = Task.objects.get(pk=detail['task_id'])
        self.assertEqual(task.tags.get('author'), 'Тест')

    def test_set_publish_start_shifts_dates(self):
        ladder_svc.create_ladder(at_number=1, words=['А', 'Б'], hints=['x'])
        ladder_svc.create_ladder(at_number=2, words=['В', 'Г'], hints=['y'])
        ladder_svc.set_publish_start('2026-08-01')
        rows = ladder_svc.list_ladder_rows()
        self.assertEqual(rows[0].publish_date, '2026-08-01')
        self.assertEqual(rows[1].publish_date, '2026-08-02')

    def test_published_flag(self):
        ladder_svc.set_publish_start('2026-07-08')
        ladder_svc.create_ladder(at_number=1, words=['А', 'Б'], hints=['x'])
        ladder_svc.create_ladder(at_number=2, words=['В', 'Г'], hints=['y'])
        now = datetime(2026, 7, 8, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        rows = ladder_svc.list_ladder_rows(now=now)
        self.assertTrue(rows[0].is_published)
        self.assertTrue(rows[0].is_today)
        self.assertFalse(rows[1].is_published)


class LadderSupportViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.game = _make_ladder_game()
        cls.staff = User.objects.create_user('ladder_staff', 's@ex.com', 'secret')
        Profile.objects.create(user=cls.staff, first_name='S', last_name='T')
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='ladder_staff', password='secret'))
        ladder_svc.set_publish_start('2099-01-01')

    def test_dashboard_renders(self):
        ladder_svc.create_ladder(at_number=1, words=['А', 'Б'], hints=['x'])
        response = self.client.get(reverse('support:ladders'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Лесенки')
        self.assertContains(response, 'ladders-bootstrap')

    def test_reorder_endpoint(self):
        a = ladder_svc.create_ladder(at_number=1, words=['А', 'Б'], hints=['x'])
        b = ladder_svc.create_ladder(at_number=2, words=['В', 'Г'], hints=['y'])
        response = self.client.post(
            reverse('support:ladders_reorder'),
            data=json.dumps({'order': [b['link_id'], a['link_id']]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(
            [row['link_id'] for row in payload['ladders']],
            [b['link_id'], a['link_id']],
        )
        self.assertEqual(GameTaskGroup.objects.get(pk=b['link_id']).number, '1')

    def test_create_and_update_endpoints(self):
        response = self.client.post(
            reverse('support:ladders_create'),
            data=json.dumps({'at_number': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        link_id = response.json()['ladder']['link_id']
        response = self.client.post(
            reverse('support:ladders_update', kwargs={'link_id': link_id}),
            data=json.dumps({
                'words': ['СНЕГ', 'СМЕХ'],
                'hints': ['вторая буква'],
                'intro': '',
                'author': '',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ladder']['words'], ['СНЕГ', 'СМЕХ'])

    def test_nav_link_present(self):
        response = self.client.get(reverse('support:hub'))
        self.assertContains(response, reverse('support:ladders'))
