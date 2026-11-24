"""Адвент-календарь: доступ к консоли, расписание дней, публичный раздел."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from games.console.games.advent import (
    AdventSupportError,
    create_advent_day,
    get_advent_detail,
    list_advent_rows,
    reorder_advent,
    save_advent_settings,
    update_advent_day,
)
from games.console.http.constants import ADVENT_CONSOLE_GROUP, SUPPORT_CONSOLE_GROUP
from games.daily.catalog.advent import ADVENT_GAME_ID, ADVENT_PUBLISH_START_TAG
from games.models import CheckerType, Game, GameTaskGroup, Profile, Project, Task

MOSCOW = ZoneInfo('Europe/Moscow')


def _advent_game(start='2099-12-01T00:00:00+03:00', **tags):
    Project.objects.get_or_create(id='sections')
    CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
    game, _ = Game.objects.update_or_create(
        id=ADVENT_GAME_ID,
        defaults={
            'name': 'Адвент-календарь',
            'outside_name': 'Адвент-календарь',
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'project_id': 'sections',
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': {ADVENT_PUBLISH_START_TAG: start, **tags},
        },
    )
    GameTaskGroup.objects.filter(game=game).delete()
    Group.objects.get_or_create(name=ADVENT_CONSOLE_GROUP)
    return game


def _user(username, *, group=None, **extra):
    user = User.objects.create_user(username, password='secret', **extra)
    Profile.objects.create(user=user, first_name=username, last_name='T')
    if group:
        user.groups.add(Group.objects.get_or_create(name=group)[0])
    return user


def _post(client, name, body, *args):
    return client.post(
        reverse(name, args=args), data=json.dumps(body), content_type='application/json',
    )


class AdventConsoleAccessTests(TestCase):
    def setUp(self):
        _advent_game()
        self.editor = _user('advent_editor', group=ADVENT_CONSOLE_GROUP)
        self.staff = _user('advent_staff', group=SUPPORT_CONSOLE_GROUP)
        self.outsider = _user('advent_outsider')

    def test_editor_opens_only_the_advent_section(self):
        client = Client()
        client.force_login(self.editor)
        response = client.get(reverse('support:advent'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Адвент-календарь')
        self.assertNotContains(response, reverse('support:ladders'))
        self.assertNotContains(response, '/admin/')
        # Other console pages send her back to the advent section.
        response = client.get(reverse('support:hub'))
        self.assertRedirects(response, reverse('support:advent'), fetch_redirect_response=False)
        response = client.get(reverse('support:ladders'))
        self.assertRedirects(response, reverse('support:advent'), fetch_redirect_response=False)
        # …and their write endpoints stay closed.
        response = _post(client, 'support:ladders_reorder', {'order': []})
        self.assertEqual(response.status_code, 403)
        response = _post(client, 'support:action', {})
        self.assertEqual(response.status_code, 403)

    def test_support_staff_see_the_link_and_the_page(self):
        client = Client()
        client.force_login(self.staff)
        self.assertContains(client.get(reverse('support:hub')), reverse('support:advent'))
        self.assertEqual(client.get(reverse('support:advent')).status_code, 200)

    def test_outsider_gets_403_and_anonymous_goes_to_site_login(self):
        client = Client()
        response = client.get(reverse('support:advent'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        client.force_login(self.outsider)
        self.assertEqual(client.get(reverse('support:advent')).status_code, 403)
        response = _post(client, 'support:advent_create', {'at_number': 1, 'answers': 'x'})
        self.assertEqual(response.status_code, 403)

    def test_editor_creates_and_edits_a_day_over_http(self):
        client = Client()
        client.force_login(self.editor)
        response = _post(client, 'support:advent_create', {
            'at_number': 1,
            'text': '<p>Что <b>это</b>?</p>',
            'answers': 'Ёлка\nель\n',
        })
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()['item']
        self.assertEqual(item['number'], 1)
        self.assertEqual(item['answers'], 'Ёлка\nель')
        self.assertEqual(item['answer'], 'Ёлка')
        response = _post(
            client, 'support:advent_update', {'name': 'Первый день'}, item['link_id'],
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['rows'][0]['name'], 'Первый день')


class AdventScheduleTests(TestCase):
    def setUp(self):
        self.game = _advent_game()

    def _day(self, number, answers='ответ', **fields):
        return create_advent_day(at_number=number, text=f'<p>{number}</p>', answers=answers, **fields)

    def test_day_is_one_default_task_with_the_standard_checker(self):
        item = self._day(1, answers='Снег\nснежок')
        link = GameTaskGroup.objects.get(pk=item['link_id'])
        task = Task.objects.get(task_group=link.task_group)
        self.assertEqual(task.task_type, 'default')
        self.assertEqual(task.checker_id, 'equals_with_possible_spaces')
        self.assertEqual(task.checker_data, 'Снег\nснежок')
        self.assertIsNone(task.max_attempts)
        self.assertEqual(link.name, 'День 1')
        self.assertTrue(link.share_hash)

    def test_insert_shifts_later_days_and_keeps_custom_names(self):
        self._day(1)
        second = self._day(2, name='Особый')
        self._day(3)
        self._day(2, answers='вставка')
        rows = list_advent_rows()
        self.assertEqual([row.number for row in rows], [1, 2, 3, 4])
        self.assertEqual([row.name for row in rows], ['День 1', 'День 2', 'Особый', 'День 4'])
        self.assertEqual(rows[1].answer, 'вставка')
        self.assertEqual(rows[2].link_id, second['link_id'])
        reorder_advent([rows[0].link_id, rows[3].link_id, rows[1].link_id, rows[2].link_id])
        self.assertEqual(
            [row.name for row in list_advent_rows()], ['День 1', 'День 2', 'День 3', 'Особый'],
        )

    def test_published_days_are_locked(self):
        save_advent_settings(
            title='Адвент', description='', start_date='2026-12-01', end_date='2026-12-03',
        )
        for number in (1, 2, 3, 4):
            self._day(number)
        now = datetime(2026, 12, 2, 12, tzinfo=MOSCOW)
        rows = list_advent_rows(now=now)
        self.assertEqual([row.is_published for row in rows], [True, True, False, False])
        self.assertEqual([row.is_today for row in rows], [False, True, False, False])
        self.assertEqual([row.is_after_end for row in rows], [False, False, False, True])
        later = datetime(2026, 12, 10, 12, tzinfo=MOSCOW)
        self.assertEqual(
            [row.is_published for row in list_advent_rows(now=later)], [True, True, True, False],
        )
        with self.assertRaises(AdventSupportError):
            create_advent_day(at_number=2, answers='x', now=now)
        with self.assertRaises(AdventSupportError):
            reorder_advent([rows[1].link_id, rows[0].link_id, rows[2].link_id, rows[3].link_id], now=now)

    def test_settings_round_trip_and_start_is_frozen_once_a_day_is_out(self):
        settings = save_advent_settings(
            title='Адвент Иры', description='Каждый день задание',
            start_date='2026-12-01', end_date='2026-12-24',
        )
        self.assertEqual(settings['day_count'], 24)
        self.game.refresh_from_db()
        self.assertEqual(self.game.outside_name, 'Адвент Иры')
        self.assertEqual(self.game.tags['section_description'], 'Каждый день задание')
        self.assertEqual(self.game.tags['advent_end_date'], '2026-12-24')
        with self.assertRaises(AdventSupportError):
            save_advent_settings(title='x', description='', start_date='2026-12-05', end_date='2026-12-01')
        self._day(1)
        with patch('games.console.games.advent.timezone.now',
                   return_value=datetime(2026, 12, 1, 9, tzinfo=MOSCOW)):
            with self.assertRaises(AdventSupportError):
                save_advent_settings(title='x', description='', start_date='2026-12-02')
            # Same start, new end and title are fine.
            save_advent_settings(title='Новое', description='', start_date='2026-12-01')
        self.game.refresh_from_db()
        self.assertNotIn('advent_end_date', self.game.tags)
        self.assertNotIn('section_description', self.game.tags)

    def test_new_answers_recheck_sent_attempts(self):
        item = self._day(1, answers='старый')
        with patch('games.ops.workers.jobs.recheck.recheck_full') as recheck:
            with self.captureOnCommitCallbacks(execute=True):
                update_advent_day(item['link_id'], answers='старый\nновый')
            self.assertEqual(recheck.call_count, 1)
            with self.captureOnCommitCallbacks(execute=True):
                update_advent_day(item['link_id'], text='<p>другой</p>', answers='старый\nновый')
            self.assertEqual(recheck.call_count, 1)
        self.assertEqual(get_advent_detail(item['link_id'])['text'], '<p>другой</p>')


class AdventPublicPagesTests(TestCase):
    def test_hub_hash_preview_and_hidden_future_number(self):
        game = _advent_game(section_description='Задания от автора')
        item = create_advent_day(at_number=1, text='<p>Загадка</p>', answers='ответ')
        link = GameTaskGroup.objects.get(pk=item['link_id'])
        client = Client()
        client.force_login(_user('advent_visitor'))
        hub = client.get('/advent/')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, 'Задания от автора')
        self.assertEqual(client.get('/advent/1/').status_code, 404)
        preview = client.get('/advent/{}/'.format(link.share_hash))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'Загадка')
        self.assertIn(link.share_hash, item['site_url'])
        game.tags = {ADVENT_PUBLISH_START_TAG: '2020-12-01T00:00:00+03:00'}
        game.save(update_fields=['tags'])
        self.assertEqual(client.get('/advent/1/').status_code, 200)
        self.assertEqual(client.get('/advent/last/').status_code, 302)


class AdventHubCardTests(TestCase):
    def _cards(self):
        from django.test import RequestFactory

        from games.site.pages.hub.cards import _build_hub_section_cards

        request = RequestFactory().get('/')
        request.user = _user('advent_hub_visitor')
        request.session = {}
        result = _build_hub_section_cards(request, team=None)
        return {card['id']: card for card in result['daily_hub_grid_cards']}

    def test_card_hidden_before_the_first_day(self):
        _advent_game(start='2099-12-01T00:00:00+03:00')
        self.assertNotIn(ADVENT_GAME_ID, self._cards())

    def test_card_after_the_first_day_uses_the_settings(self):
        game = _advent_game(start='2020-12-01T00:00:00+03:00', section_description='Подпись Иры')
        game.outside_name = 'Зимний календарь'
        game.save(update_fields=['outside_name'])
        create_advent_day(at_number=1, text='<p>x</p>', answers='x')
        card = self._cards()[ADVENT_GAME_ID]
        self.assertEqual(card['title'], 'Зимний календарь')
        self.assertEqual(card['description'], 'Подпись Иры')
        self.assertTrue(card['wide'])
        self.assertEqual(card['cta_number'], '1')


class AdventAnswerButtonTests(TestCase):
    def test_answer_hidden_by_default_and_shown_when_enabled(self):
        _advent_game(start='2020-12-01T00:00:00+03:00')
        item = create_advent_day(at_number=1, text='<p>Загадка</p>', answers='секрет')
        task = Task.objects.get(task_group_id=item['task_group_id'])
        client = Client()
        client.force_login(_user('advent_answer_visitor'))
        page = client.get('/advent/1/')
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'data-answer-open')
        self.assertEqual(client.get('/answer/{}/'.format(task.pk)).status_code, 404)

        settings = save_advent_settings(
            title='Адвент', description='', start_date='2020-12-01', show_answer=True,
        )
        self.assertTrue(settings['show_answer'])
        self.assertContains(client.get('/advent/1/'), 'data-answer-open')
        self.assertNotEqual(client.get('/answer/{}/'.format(task.pk)).status_code, 404)

        settings = save_advent_settings(title='Адвент', description='', start_date='2020-12-01')
        self.assertFalse(settings['show_answer'])
        self.assertNotContains(client.get('/advent/1/'), 'data-answer-open')
