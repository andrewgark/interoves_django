"""Простые ежедневные игры: адвент-календарь и криптики.

Доступ к консоли, расписание дней, загрузка медиа, публичный раздел,
карточки на главной, кнопка «ответ» и чекер puzzlehunt.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from games.console.games.simple_daily import SimpleDailyError
from games.console.games.simple_daily_sections import console_for
from games.console.http.constants import (
    ADVENT_CONSOLE_GROUP,
    CRYPTIC_CONSOLE_GROUP,
    SUPPORT_CONSOLE_GROUP,
)
from games.daily.catalog.advent import ADVENT_GAME_ID, ADVENT_PUBLISH_START_TAG
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID, CRYPTIC_PUBLISH_START_TAG, enumeration_for
from games.models import Attempt, Audio, CheckerType, Game, GameTaskGroup, Image, Profile, Project, Task
from games.puzzles.core.check_runtime import KEEP_GOING_STATE, PuzzlehuntChecker

MOSCOW = ZoneInfo('Europe/Moscow')


def _game(game_id, tags):
    Project.objects.get_or_create(id='sections')
    for checker_id in ('equals_with_possible_spaces', 'puzzlehunt'):
        CheckerType.objects.get_or_create(id=checker_id)
    game, _ = Game.objects.update_or_create(
        id=game_id,
        defaults={
            'name': game_id,
            'outside_name': {'advent': 'New Year Hunt', 'cryptic': 'Криптики'}[game_id],
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'project_id': 'sections',
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': tags,
        },
    )
    GameTaskGroup.objects.filter(game=game).delete()
    for group in (ADVENT_CONSOLE_GROUP, CRYPTIC_CONSOLE_GROUP):
        Group.objects.get_or_create(name=group)
    return game


def _advent_game(start='2099-12-01T00:00:00+03:00', **tags):
    return _game(ADVENT_GAME_ID, {ADVENT_PUBLISH_START_TAG: start, **tags} if start else dict(tags))


def _cryptic_game(start=None, **tags):
    return _game(CRYPTIC_GAME_ID, {CRYPTIC_PUBLISH_START_TAG: start, **tags} if start else dict(tags))


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


def _advent_day(number, answers='ответ', keep_going='', **kw):
    return console_for(ADVENT_GAME_ID).create(
        at_number=number,
        fields={'text': f'<p>{number}</p>', 'answers': answers, 'keep_going': keep_going},
        **kw,
    )


class ConsoleAccessTests(TestCase):
    def setUp(self):
        _advent_game()
        _cryptic_game()
        self.editor = _user('advent_editor', group=ADVENT_CONSOLE_GROUP)
        self.cryptic_editor = _user('cryptic_editor', group=CRYPTIC_CONSOLE_GROUP)
        self.staff = _user('advent_staff', group=SUPPORT_CONSOLE_GROUP)
        self.outsider = _user('advent_outsider')

    def test_editor_opens_only_her_section(self):
        client = Client()
        client.force_login(self.editor)
        response = client.get(reverse('support:advent'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'New Year Hunt')
        self.assertContains(response, 'Адвент-календарь')
        self.assertNotContains(response, reverse('support:ladders'))
        self.assertNotContains(response, reverse('support:cryptic'))
        self.assertNotContains(response, '/admin/')
        self.assertRedirects(client.get(reverse('support:hub')), reverse('support:advent'),
                             fetch_redirect_response=False)
        self.assertRedirects(client.get(reverse('support:ladders')), reverse('support:advent'),
                             fetch_redirect_response=False)
        self.assertEqual(_post(client, 'support:ladders_reorder', {'order': []}).status_code, 403)
        self.assertEqual(_post(client, 'support:action', {}).status_code, 403)
        # The other limited section is closed too.
        self.assertEqual(client.get(reverse('support:cryptic')).status_code, 403)
        self.assertEqual(
            _post(client, 'support:cryptic_create', {'at_number': 1, 'fields': {}}).status_code, 403,
        )

    def test_cryptic_editor_lands_on_cryptics(self):
        client = Client()
        client.force_login(self.cryptic_editor)
        self.assertEqual(client.get(reverse('support:cryptic')).status_code, 200)
        self.assertRedirects(client.get(reverse('support:hub')), reverse('support:cryptic'),
                             fetch_redirect_response=False)
        self.assertEqual(client.get(reverse('support:advent')).status_code, 403)

    def test_support_staff_see_both_sections(self):
        client = Client()
        client.force_login(self.staff)
        hub = client.get(reverse('support:hub'))
        self.assertContains(hub, reverse('support:advent'))
        self.assertContains(hub, reverse('support:cryptic'))
        self.assertEqual(client.get(reverse('support:advent')).status_code, 200)
        self.assertEqual(client.get(reverse('support:cryptic')).status_code, 200)

    def test_outsider_gets_403_and_anonymous_goes_to_site_login(self):
        client = Client()
        response = client.get(reverse('support:advent'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        client.force_login(self.outsider)
        self.assertEqual(client.get(reverse('support:advent')).status_code, 403)

    def test_editor_creates_and_edits_a_day_over_http(self):
        client = Client()
        client.force_login(self.editor)
        response = _post(client, 'support:advent_create', {
            'at_number': 1,
            'fields': {'text': '<p>Что <b>это</b>?</p>', 'answers': 'Ёлка\nель\n', 'keep_going': 'хвоя'},
        })
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()['item']
        self.assertEqual(item['number'], 1)
        # Without Ира's profile in this database the day has no author.
        group = GameTaskGroup.objects.get(pk=item['link_id']).task_group
        self.assertFalse(group.authors.exists())
        self.assertEqual(item['fields']['answers'], 'Ёлка\nель')
        self.assertEqual(item['fields']['keep_going'], 'хвоя')
        response = _post(client, 'support:advent_update', {'name': 'Первый день'}, item['link_id'])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['rows'][0]['name'], 'Первый день')
        response = _post(client, 'support:advent_create', {'at_number': 2, 'fields': {'text': 'x'}})
        self.assertEqual(response.status_code, 400)


class AdventScheduleTests(TestCase):
    def setUp(self):
        self.game = _advent_game()
        self.console = console_for(ADVENT_GAME_ID)

    def test_day_is_one_default_task_with_the_puzzlehunt_checker(self):
        item = _advent_day(1, answers='Снег\nснежок', keep_going='снеж\nсугроб => Ближе к небу')
        link = GameTaskGroup.objects.get(pk=item['link_id'])
        task = Task.objects.get(task_group=link.task_group)
        self.assertEqual(task.task_type, 'default')
        self.assertEqual(task.checker_id, 'puzzlehunt')
        self.assertEqual(task.checker_data, 'Снег\nснежок\n? снеж\n? сугроб => Ближе к небу')
        self.assertEqual(task.answer, 'Снег')
        self.assertIsNone(task.max_attempts)
        self.assertEqual(link.name, 'День 1')
        self.assertTrue(link.share_hash)

    def test_insert_shifts_later_days_and_keeps_custom_names(self):
        _advent_day(1)
        second = self.console.create(at_number=2, name='Особый', fields={'answers': 'x'})
        _advent_day(3)
        _advent_day(2, answers='вставка')
        rows = self.console.rows()
        self.assertEqual([row.number for row in rows], [1, 2, 3, 4])
        self.assertEqual([row.name for row in rows], ['День 1', 'День 2', 'Особый', 'День 4'])
        self.assertEqual(rows[1].summary, 'Ответ: вставка')
        self.assertEqual(rows[2].link_id, second['link_id'])
        self.console.reorder([rows[0].link_id, rows[3].link_id, rows[1].link_id, rows[2].link_id])
        self.assertEqual(
            [row.name for row in self.console.rows()], ['День 1', 'День 2', 'День 3', 'Особый'],
        )

    def test_published_days_are_locked_and_days_after_the_end_stay_hidden(self):
        self.console.save_settings(
            title='Адвент', description='', start_date='2026-12-01', end_date='2026-12-03',
        )
        for number in (1, 2, 3, 4):
            _advent_day(number)
        now = datetime(2026, 12, 2, 12, tzinfo=MOSCOW)
        rows = self.console.rows(now=now)
        self.assertEqual([row.is_published for row in rows], [True, True, False, False])
        self.assertEqual([row.is_today for row in rows], [False, True, False, False])
        self.assertEqual([row.is_after_end for row in rows], [False, False, False, True])
        later = datetime(2026, 12, 10, 12, tzinfo=MOSCOW)
        self.assertEqual(
            [row.is_published for row in self.console.rows(now=later)], [True, True, True, False],
        )
        with self.assertRaises(SimpleDailyError):
            self.console.create(at_number=2, fields={'answers': 'x'}, now=now)
        with self.assertRaises(SimpleDailyError):
            self.console.reorder(
                [rows[1].link_id, rows[0].link_id, rows[2].link_id, rows[3].link_id], now=now,
            )

    def test_settings_round_trip_and_start_is_frozen_once_a_day_is_out(self):
        settings = self.console.save_settings(
            title='Адвент Иры', description='Каждый день задание',
            start_date='2026-12-01', end_date='2026-12-24', show_answer=True,
        )
        self.assertEqual(settings['day_count'], 24)
        self.assertTrue(settings['show_answer'])
        self.game.refresh_from_db()
        self.assertEqual(self.game.outside_name, 'Адвент Иры')
        self.assertEqual(self.game.tags['section_description'], 'Каждый день задание')
        self.assertEqual(self.game.tags['advent_end_date'], '2026-12-24')
        with self.assertRaises(SimpleDailyError):
            self.console.save_settings(title='x', description='', start_date='2026-12-05',
                                       end_date='2026-12-01')
        _advent_day(1)
        moment = datetime(2026, 12, 1, 9, tzinfo=MOSCOW)
        with patch('games.console.games.simple_daily.timezone.now', return_value=moment), \
                patch('games.daily.page.section.timezone.now', return_value=moment):
            with self.assertRaises(SimpleDailyError):
                self.console.save_settings(title='x', description='', start_date='2026-12-02')
            self.console.save_settings(title='Новое', description='', start_date='2026-12-01')
        self.game.refresh_from_db()
        self.assertNotIn('advent_end_date', self.game.tags)
        self.assertNotIn('section_description', self.game.tags)
        self.assertNotIn('show_answer_button', self.game.tags)

    def test_new_answers_recheck_sent_attempts(self):
        item = _advent_day(1, answers='старый')
        with patch('games.ops.workers.jobs.recheck.recheck_full') as recheck:
            with self.captureOnCommitCallbacks(execute=True):
                self.console.update(item['link_id'], fields={'answers': 'старый\nновый'})
            self.assertEqual(recheck.call_count, 1)
            with self.captureOnCommitCallbacks(execute=True):
                self.console.update(item['link_id'], fields={'text': '<p>другой</p>'})
            self.assertEqual(recheck.call_count, 1)
        detail = self.console.detail(item['link_id'])
        self.assertEqual(detail['fields']['text'], '<p>другой</p>')
        self.assertEqual(detail['fields']['answers'], 'старый\nновый')


class MediaUploadTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        _advent_game()
        self.client = Client()
        self.client.force_login(_user('advent_media', group=ADVENT_CONSOLE_GROUP))

    def _upload(self, name, content, content_type):
        with override_settings(MEDIA_ROOT=self.media_root):
            return self.client.post(
                reverse('support:advent_upload'),
                {'file': SimpleUploadedFile(name, content, content_type=content_type)},
            )

    def test_image_and_mp3_become_insertable_html(self):
        response = self._upload('ёлка.png', b'\x89PNG\r\n\x1a\n', 'image/png')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['kind'], 'image')
        self.assertTrue(Image.objects.filter(pk=data['id']).exists())
        self.assertIn('<img src="{}"'.format(data['url']), data['html'])

        response = self._upload('Jingle "Bells".mp3', b'ID3', 'audio/mpeg')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['kind'], 'audio')
        audio = Audio.objects.get(pk=data['id'])
        self.assertEqual(audio.title, 'Jingle "Bells"')
        self.assertIn('audio-url="{}"'.format(data['url']), data['html'])
        self.assertIn('audio-title="Jingle &quot;Bells&quot;"', data['html'])

        self.assertEqual(self._upload('notes.txt', b'x', 'text/plain').status_code, 400)


class PuzzlehuntCheckerTests(TestCase):
    def test_answers_and_keep_going(self):
        checker = PuzzlehuntChecker('Северный олень\n? олень\n? север => Почти, но не туда')
        ok = checker.check('  северный-ОЛЕНЬ! ', None)
        self.assertEqual((ok.status, ok.points), ('Ok', 1))
        keep = checker.check('Олень', None)
        self.assertEqual((keep.status, keep.points, keep.state), ('Wrong', 0, KEEP_GOING_STATE))
        self.assertEqual(keep.comment, 'Продолжайте!')
        self.assertEqual(checker.check('СЕВЕР', None).comment, 'Почти, но не туда')
        wrong = checker.check('лось', None)
        self.assertEqual(wrong.status, 'Wrong')
        self.assertIsNone(wrong.state)

    def test_keep_going_on_the_site(self):
        _advent_game(start='2020-12-01T00:00:00+03:00')
        item = _advent_day(1, answers='снеговик', keep_going='снег')
        task = Task.objects.get(task_group_id=item['task_group_id'])
        client = Client()
        client.force_login(_user('advent_keep_going'))
        response = client.post('/send_attempt/{}/'.format(task.pk), {'text': 'Снег', 'game_id': 'advent'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json().get('attempt_status'), 'KeepGoing')
        self.assertEqual(response.json().get('attempt_message'), 'Продолжайте!')
        attempt = Attempt.manager.get(task=task)
        self.assertEqual(attempt.state, KEEP_GOING_STATE)
        page = client.get('/advent/1/')
        self.assertContains(page, 'продолжайте')


class CrypticTests(TestCase):
    def test_enumeration(self):
        self.assertEqual(enumeration_for('снег'), '4')
        self.assertEqual(enumeration_for('лесная тропа'), '6, 5')
        self.assertEqual(enumeration_for('из-за'), '2-2')

    def test_clue_answer_and_mask(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        console = console_for(CRYPTIC_GAME_ID)
        item = console.create(at_number=1, fields={
            'clue': 'Спор внутри центра Сколково', 'answer': 'Ссора', 'explanation': 'разбор',
        })
        task = Task.objects.get(task_group_id=item['task_group_id'])
        self.assertEqual(task.checker_id, 'equals_with_possible_spaces')
        self.assertEqual(task.checker_data, 'Ссора')
        self.assertIn('data-cryptic-enum="5"', task.text)
        self.assertIn('(5)</span>', task.text)
        self.assertEqual(item['fields']['enumeration'], '')
        self.assertEqual(console.rows()[0].summary, 'Ответ: Ссора (5)')
        console.update(item['link_id'], fields={'enumeration': '2-3'})
        task.refresh_from_db()
        self.assertIn('(2-3)', task.text)
        with self.assertRaises(SimpleDailyError):
            console.update(item['link_id'], fields={'enumeration': 'пять'})

        client = Client()
        client.force_login(_user('cryptic_player'))
        page = client.get('/cryptic/1/')
        self.assertContains(page, 'Спор внутри центра Сколково')
        self.assertContains(page, 'cryptic_play.js')
        self.assertNotContains(page, 'data-answer-open')
        response = client.post('/send_attempt/{}/'.format(task.pk), {'text': 'с с о р а', 'game_id': 'cryptic'})
        self.assertEqual(response.json().get('attempt_status'), 'Ok')
        page = client.get('/cryptic/1/')
        self.assertContains(page, 'Ваш результат')
        self.assertContains(page, '🔑 Криптики №1')


class PublicPagesTests(TestCase):
    def test_hub_hash_preview_and_hidden_future_number(self):
        game = _advent_game(section_description='Задания от автора')
        item = _advent_day(1)
        link = GameTaskGroup.objects.get(pk=item['link_id'])
        client = Client()
        client.force_login(_user('advent_visitor'))
        hub = client.get('/advent/')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, 'Задания от автора')
        self.assertEqual(client.get('/advent/1/').status_code, 404)
        self.assertEqual(client.get('/advent/{}/'.format(link.share_hash)).status_code, 200)
        self.assertIn(link.share_hash, item['site_url'])
        game.tags = {ADVENT_PUBLISH_START_TAG: '2020-12-01T00:00:00+03:00'}
        game.save(update_fields=['tags'])
        self.assertEqual(client.get('/advent/1/').status_code, 200)
        self.assertEqual(client.get('/advent/last/').status_code, 302)

    def test_solved_advent_day_shows_the_result_card(self):
        _advent_game(start='2020-12-01T00:00:00+03:00')
        item = _advent_day(1, answers='ёлка')
        task = Task.objects.get(task_group_id=item['task_group_id'])
        client = Client()
        client.force_login(_user('advent_solver'))
        client.post('/send_attempt/{}/'.format(task.pk), {'text': 'Ёлка', 'game_id': 'advent'})
        page = client.get('/advent/1/')
        self.assertContains(page, 'Ваш результат')
        self.assertContains(page, '🌲 New Year Hunt №1')
        self.assertContains(page, '/advent/1</div>')
        self.assertNotContains(page, 'data-share-copy-image')


class HubAndNavTests(TestCase):
    def _cards(self, now=None):
        from games.site.pages.hub.cards import _build_hub_section_cards

        request = RequestFactory().get('/')
        request.user = _user('hub_visitor_{}'.format(User.objects.count()))
        request.session = {}
        with patch('games.sections.hub.timezone.now', return_value=now or datetime.now(MOSCOW)):
            result = _build_hub_section_cards(request, team=None)
        return {card['id']: card for card in result['daily_hub_grid_cards']}

    def _nav_ids(self, now):
        from games.sections.hub import daily_nav_items
        from games.site.pages.hub import link_cache

        link_cache.clear()
        with patch('games.sections.hub.timezone.now', return_value=now):
            return [item['id'] for item in daily_nav_items()]

    def test_advent_hidden_before_day_one(self):
        _advent_game(start='2099-12-01T00:00:00+03:00')
        self.assertNotIn(ADVENT_GAME_ID, self._cards())
        self.assertNotIn(ADVENT_GAME_ID, self._nav_ids(datetime(2026, 11, 30, tzinfo=MOSCOW)))

    def test_advent_card_and_nav_after_day_one(self):
        game = _advent_game(start='2020-12-01T00:00:00+03:00', section_description='Подпись Иры')
        game.outside_name = 'Зимний календарь'
        game.save(update_fields=['outside_name'])
        _advent_day(1)
        card = self._cards()[ADVENT_GAME_ID]
        self.assertEqual(card['title'], 'Зимний календарь')
        self.assertEqual(card['description'], 'Подпись Иры')
        self.assertEqual(card['ph_icon'], 'tree-evergreen')
        self.assertEqual(card['cta_number'], '1')
        self.assertIn(ADVENT_GAME_ID, self._nav_ids(datetime(2026, 12, 2, tzinfo=MOSCOW)))

    def test_cryptic_card_says_soon_from_december_first(self):
        _cryptic_game()
        self.assertNotIn(CRYPTIC_GAME_ID, self._cards(datetime(2026, 11, 30, 23, tzinfo=MOSCOW)))
        card = self._cards(datetime(2026, 12, 1, 0, 5, tzinfo=MOSCOW))[CRYPTIC_GAME_ID]
        self.assertEqual(card['status'], 'coming_soon')
        self.assertEqual(card['soon_text'], 'Скоро!')
        self.assertEqual(card['ph_icon'], 'key')
        self.assertIsNone(card['play_url'])


class AnswerButtonTests(TestCase):
    def test_answer_hidden_by_default_and_shown_when_enabled(self):
        _advent_game(start='2020-12-01T00:00:00+03:00')
        item = _advent_day(1, answers='секрет')
        task = Task.objects.get(task_group_id=item['task_group_id'])
        console = console_for(ADVENT_GAME_ID)
        client = Client()
        client.force_login(_user('advent_answer_visitor'))
        page = client.get('/advent/1/')
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'data-answer-open')
        self.assertEqual(client.get('/answer/{}/'.format(task.pk)).status_code, 404)

        console.save_settings(title='Адвент', description='', start_date='2020-12-01', show_answer=True)
        self.assertContains(client.get('/advent/1/'), 'data-answer-open')
        self.assertNotEqual(client.get('/answer/{}/'.format(task.pk)).status_code, 404)

        console.save_settings(title='Адвент', description='', start_date='2020-12-01')
        self.assertNotContains(client.get('/advent/1/'), 'data-answer-open')


class CrypticBeforeLaunchTests(TestCase):
    def test_section_pages_without_a_start_date(self):
        _cryptic_game()
        console_for(CRYPTIC_GAME_ID).create(at_number=1, fields={'clue': 'Подсказка', 'answer': 'ответ'})
        client = Client()
        client.force_login(_user('cryptic_early'))
        hub = client.get('/cryptic/')
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, 'Скоро!')
        self.assertNotContains(hub, 'Добавьте их в админке')
        self.assertNotContains(hub, 'Подсказка')
        self.assertEqual(client.get('/cryptic/results/').status_code, 200)
        self.assertEqual(client.get('/cryptic/1/').status_code, 404)
        self.assertIn(client.get('/cryptic/last/').status_code, (301, 302))


class DefaultAuthorTests(TestCase):
    def test_each_section_records_its_default_author(self):
        _advent_game()
        _cryptic_game()
        ira = User.objects.create_user('ira', password='x', id=16)
        Profile.objects.create(user=ira, first_name='Ирина', last_name='Броварь')
        andrey = User.objects.filter(pk=1).first() or User.objects.create_user('andrey', password='x', id=1)
        Profile.objects.get_or_create(user=andrey, defaults={'first_name': 'Андрей', 'last_name': 'Гаркавый'})
        advent = _advent_day(1)
        cryptic = console_for(CRYPTIC_GAME_ID).create(at_number=1, fields={'clue': 'п', 'answer': 'о'})
        self.assertEqual(
            list(GameTaskGroup.objects.get(pk=advent['link_id']).task_group.authors.values_list('user_id', flat=True)),
            [16],
        )
        self.assertEqual(
            list(GameTaskGroup.objects.get(pk=cryptic['link_id']).task_group.authors.values_list('user_id', flat=True)),
            [1],
        )


class TextAuthorTests(TestCase):
    def setUp(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        self.andrey = User.objects.filter(pk=1).first() or User.objects.create_user('andrey1', password='x', id=1)
        Profile.objects.get_or_create(user=self.andrey, defaults={'first_name': 'Андрей', 'last_name': 'Г'})
        self.console = console_for(CRYPTIC_GAME_ID)

    def _authors(self, item):
        group = GameTaskGroup.objects.get(pk=item['link_id']).task_group
        return list(group.authors.values_list('user_id', flat=True))

    def test_text_author_replaces_the_default_one(self):
        item = self.console.create(at_number=1, fields={
            'clue': 'Подсказка', 'answer': 'ответ', 'author': '  Ефим   Холмогоров ',
        })
        self.assertEqual(self._authors(item), [])
        task = Task.objects.get(task_group_id=item['task_group_id'])
        self.assertEqual(task.tags['author'], 'Ефим Холмогоров')
        self.assertEqual(item['fields']['author'], 'Ефим Холмогоров')
        from games.community.authorship import credited_elsewhere_group_ids
        self.assertIn(item['task_group_id'], credited_elsewhere_group_ids())

        client = Client()
        client.force_login(_user('cryptic_byline'))
        self.assertContains(client.get('/cryptic/1/'), 'Автор: Ефим Холмогоров')

        # Clearing the field gives the day back to the default author.
        self.console.update(item['link_id'], fields={'author': ''})
        self.assertEqual(self._authors(item), [1])
        task.refresh_from_db()
        self.assertNotIn('author', task.tags)
        self.assertNotContains(client.get('/cryptic/1/'), 'Автор:')
        # And naming someone again takes it away.
        self.console.update(item['link_id'], fields={'author': 'Гость'})
        self.assertEqual(self._authors(item), [])

    def test_default_author_without_text(self):
        item = self.console.create(at_number=1, fields={'clue': 'Подсказка', 'answer': 'ответ'})
        self.assertEqual(self._authors(item), [1])
        from games.community.authorship import credited_elsewhere_group_ids
        self.assertNotIn(item['task_group_id'], credited_elsewhere_group_ids())


class NewLabelTests(TestCase):
    def test_new_moves_from_censorly_to_advent(self):
        _advent_game(start='2020-12-01T00:00:00+03:00')
        _advent_day(1)
        cards = HubAndNavTests._cards(self)
        self.assertEqual(cards[ADVENT_GAME_ID].get('new_label'), 'NEW')
        if 'censorly' in cards:
            self.assertNotIn('new_label', cards['censorly'])
