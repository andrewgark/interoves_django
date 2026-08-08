"""Алфавитка: словари, префиксы, daily, support buffer, guess API."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.sites.models import Site
from django.contrib.auth.models import User
from django.test import Client, TestCase

from allauth.socialaccount.models import SocialApp

from games.alphabetty.core import (
    build_prefix_level,
    compare_words,
    guess_status,
    is_valid_guess,
    known_prefix,
    normalize_word,
    pick_answer_words,
)
from games.alphabetty.play import (
    apply_guess,
    apply_hint,
    build_share_lines,
    format_elapsed,
    format_hints_label,
    get_play_state,
    ru_attempt_word,
)
from games.anon_migrate import _solved_count
from games.alphabetty.dicts import get_answer_pool, invalidate_approved_extras
from games.alphabetty.suggestions import (
    approve_suggestions,
    approve_suggestions_for_answer,
    reject_suggestions,
    suggest_word,
)
from games.alphabetty_offer import accept_offer as accept_alphabetty_offer
from games.models import AlphabettyDictSuggestion, AlphabettyPersonalDictWord, ChainTaskState
from games.alphabetty_daily import (
    ALPHABETTY_GAME_ID,
    ALPHABETTY_PUBLISH_START_TAG,
)
from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
)
from games.results_snapshot import build_results_snapshot_payload
from games.support.services.alphabetty import (
    AlphabettySupportError,
    delete_alphabetty,
    ensure_future_buffer,
    forbid_alphabetty,
    generate_more,
    list_alphabetty_rows,
    reorder_alphabetty,
    scheduled_words,
    set_publish_start,
    update_alphabetty,
)
from games.support.services.banned import banned_word_set, list_banned_words

_FAKE_WORD = 'БЛЯМБУРГЕТОНИК'


class AlphabettyCoreTests(TestCase):
    def test_normalize_and_compare(self):
        self.assertEqual(normalize_word('  ёлка '), 'ЕЛКА')
        self.assertEqual(normalize_word('ЁЖ'), normalize_word('ЕЖ'))
        self.assertEqual(guess_status('арбуз', 'яблоко'), 'earlier')
        self.assertEqual(guess_status('яблоко', 'арбуз'), 'later')
        self.assertEqual(guess_status('слово', 'слово'), 'correct')
        self.assertEqual(compare_words('ЕЛКА', 'ЁЛКА'), 0)
        self.assertEqual(guess_status('ёлка', 'елка'), 'correct')

    def test_solved_count_prefers_alphabetty_progress(self):
        import json
        empty = json.dumps({'guesses': [], 'won': False})
        progress = json.dumps({'guesses': ['ГОД', 'ЯБЛОКО'], 'won': False})
        won = json.dumps({'guesses': ['СЛОВО'], 'won': True})
        self.assertGreater(_solved_count(progress), _solved_count(empty))
        self.assertGreater(_solved_count(won), _solved_count(progress))

    def test_valid_dict_loaded(self):
        self.assertTrue(is_valid_guess('год'))
        self.assertFalse(is_valid_guess('asdfqwer'))
        # Полный словарь: глаголы / прилагательные / формы тоже валидны.
        self.assertTrue(is_valid_guess('бежать'))
        self.assertTrue(is_valid_guess('красивая'))
        self.assertTrue(is_valid_guess('хорошо'))
        # Имена собственные (фамилии, топонимы).
        self.assertTrue(is_valid_guess('абабков'))
        self.assertTrue(is_valid_guess('абдулино'))
        self.assertTrue(is_valid_guess('австралия'))

    def test_prefix_rimlyanin_risunok(self):
        rows = build_prefix_level('римлянин', 'рисунок')
        displays = [r['display'] for r in rows]
        self.assertEqual(displays, ['РИМ+', 'РИН', 'РИО', 'РИП', 'РИР', 'РИС+'])
        self.assertTrue(rows[0]['expandable'])
        self.assertTrue(rows[-1]['expandable'])

        expanded = build_prefix_level('римлянин', 'рисунок', expand_prefix='РИС')
        self.assertEqual(expanded[0]['display'], 'РИСА')
        self.assertEqual(expanded[-1]['display'], 'РИСУ+')
        self.assertTrue(expanded[-1]['expandable'])

    def test_prefix_exact_lo_still_expandable(self):
        """Точная нижняя граница (слово = узел) всё равно даёт +, т.к. есть расширения."""
        rows = build_prefix_level('РИМ', 'РИСУНОК')
        rim = next(r for r in rows if r['prefix'] == 'РИМ')
        self.assertTrue(rim['expandable'])
        self.assertEqual(rim['display'], 'РИМ+')

    def test_prefix_only_hi_excludes_exact_hi_letter(self):
        rows = build_prefix_level(None, 'Я')
        letters = [r['letter'] for r in rows]
        self.assertNotIn('Я', letters)
        self.assertEqual(letters[-1], 'Ю')

    def test_known_prefix_lcp_and_hints(self):
        self.assertEqual(known_prefix('римлянин', 'рисунок'), 'РИ')
        self.assertEqual(known_prefix('римлянин', 'рисунок', hint_prefix='РИН'), 'РИН')
        self.assertEqual(known_prefix(None, None, hint_prefix='СЛ'), 'СЛ')

    def test_prefix_hint_only_known(self):
        rows = build_prefix_level(None, None, expand_prefix='РИ')
        self.assertTrue(rows)
        self.assertTrue(all(r['expandable'] for r in rows))
        self.assertEqual(rows[0]['prefix'], 'РИА')

    def test_pick_excludes(self):
        pool = pick_answer_words(3, exclude={'ГОД'}, rng=__import__('random').Random(0))
        self.assertEqual(len(pool), 3)
        self.assertNotIn('ГОД', pool)

    def test_share_formatting(self):
        self.assertEqual(format_elapsed(5564), '1ч 32м 44с')
        self.assertEqual(format_elapsed(124), '2м 4с')
        self.assertEqual(format_elapsed(9), '9с')
        self.assertEqual(ru_attempt_word(1), 'попытка')
        self.assertEqual(ru_attempt_word(3), 'попытки')
        self.assertEqual(ru_attempt_word(5), 'попыток')
        lines = build_share_lines(number=1, attempts=5, elapsed_seconds=5564, host='interoves.com')
        self.assertEqual(lines[0], '🔤 Алфавитка #1')
        self.assertEqual(lines[1], '🤔 5 попыток')
        self.assertEqual(lines[2], '⏱️ 1ч 32м 44с')
        self.assertEqual(lines[3], '🔗 interoves.com/alphabetty/1')
        lines_hints = build_share_lines(
            number=1, attempts=5, elapsed_seconds=5564, hints=3, host='interoves.com',
        )
        self.assertEqual(lines_hints[2], '💡💡💡 Взято 3 подсказки')
        hash_lines = build_share_lines(
            number='f639303b80c3ec03',
            attempts=3,
            elapsed_seconds=12,
            host='interoves.com',
            play_path='/alphabetty/f639303b80c3ec03/',
        )
        self.assertEqual(hash_lines[0], '🔤 Алфавитка #f639303b80c3ec03')
        self.assertEqual(hash_lines[3], '🔗 interoves.com/alphabetty/f639303b80c3ec03/')
        self.assertEqual(format_hints_label(0), '')
        self.assertEqual(format_hints_label(1), '💡 Взята 1 подсказка')
        self.assertEqual(format_hints_label(2), '💡💡 Взято 2 подсказки')
        self.assertEqual(format_hints_label(5), '💡💡💡💡💡 Взято 5 подсказок')


def _ensure_login_modal_deps():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
    for provider, name in (('google', 'Google'), ('vk', 'VK')):
        app, created = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        if created:
            app.sites.add(site)


def _ensure_alphabetty_game(**tag_overrides):
    Project.objects.get_or_create(id='sections')
    CheckerType.objects.get_or_create(id='alphabetty')
    tags = {ALPHABETTY_PUBLISH_START_TAG: '2026-08-01T00:00:00+03:00'}
    tags.update(tag_overrides)
    game, _ = Game.objects.update_or_create(
        id=ALPHABETTY_GAME_ID,
        defaults={
            'name': 'Алфавитка',
            'author': 'Interoves',
            'project_id': 'sections',
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': tags,
        },
    )
    # Чистый слот для теста: убрать seeded/предыдущие круги.
    GameTaskGroup.objects.filter(game=game).delete()
    return game


class AlphabettySupportTests(TestCase):
    def setUp(self):
        self.game = _ensure_alphabetty_game()

    def test_generate_and_buffer(self):
        result = generate_more(5)
        self.assertEqual(result['created_count'], 5)
        rows = list_alphabetty_rows()
        self.assertEqual(len(rows), 5)
        words = {r.word for r in rows}
        self.assertEqual(len(words), 5)

        future_before = sum(1 for r in rows if not r.is_published)
        buf = ensure_future_buffer(10)
        self.assertEqual(buf['added'], max(0, 10 - future_before))
        future_after = sum(1 for r in list_alphabetty_rows() if not r.is_published)
        self.assertGreaterEqual(future_after, min(10, future_before + buf['added']))

    def test_results_snapshot_excludes_future_round_columns(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        for number in ('1', '30'):
            task_group = TaskGroup.objects.create(label=f'snapshot_alphabetty_{number}')
            Task.objects.create(
                task_group=task_group,
                number='1',
                task_type='alphabetty',
                points=10,
                text='word',
            )
            GameTaskGroup.objects.create(
                game=self.game,
                task_group=task_group,
                number=number,
                name=f'#{number}',
            )

        now = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        with patch('games.alphabetty_daily.timezone.now', return_value=now):
            payload = build_results_snapshot_payload(self.game, mode='general')

        self.assertEqual([group['number'] for group in payload['task_groups']], ['1'])

    def test_update_and_reorder_lock(self):
        # Старт в будущем — все слоты ещё не опубликованы, слово можно менять.
        set_publish_start('2099-01-01')
        generate_more(3)
        rows = list_alphabetty_rows()
        detail = update_alphabetty(rows[0].link_id, word='слово')
        self.assertEqual(detail['word'], 'СЛОВО')

        # Publish start in the past so №1 is published
        set_publish_start('2020-01-01')
        rows = list_alphabetty_rows()
        published = [r for r in rows if r.is_published]
        self.assertTrue(published)
        with self.assertRaises(AlphabettySupportError):
            update_alphabetty(published[0].link_id, word='год')
        # Try to move published out of prefix
        ids = [r.link_id for r in rows]
        bad_order = ids[1:] + ids[:1]
        with self.assertRaises(AlphabettySupportError):
            reorder_alphabetty(bad_order)

    def test_duplicate_word_rejected(self):
        generate_more(2)
        rows = list_alphabetty_rows()
        with self.assertRaises(AlphabettySupportError):
            update_alphabetty(rows[1].link_id, word=rows[0].word)

    def test_missing_publish_start_keeps_closed(self):
        from games.alphabetty_daily import is_alphabetty_number_published
        generate_more(2)
        self.game.tags = {}
        self.game.save(update_fields=['tags'])
        self.assertFalse(is_alphabetty_number_published(self.game, 1))

    def test_delete_and_forbid_future(self):
        set_publish_start('2099-01-01')
        generate_more(2)
        rows = list_alphabetty_rows()
        word = rows[0].word
        result = forbid_alphabetty(rows[0].link_id)
        self.assertEqual(len(result['rows']), 1)
        self.assertIn(word, {r['word'] for r in result['banned']})
        self.assertIn(word, scheduled_words())
        self.game.refresh_from_db()
        self.assertIn(word, banned_word_set(self.game))

        rows = list_alphabetty_rows()
        delete_alphabetty(rows[0].link_id)
        self.assertEqual(len(list_alphabetty_rows()), 0)
        self.game.refresh_from_db()
        self.assertIn(word, banned_word_set(self.game))


class AlphabettyPlayApiTests(TestCase):
    def setUp(self):
        self.game = _ensure_alphabetty_game(
            **{ALPHABETTY_PUBLISH_START_TAG: '2020-01-01T00:00:00+03:00'},
        )
        checker = CheckerType.objects.get(id='alphabetty')
        tg = TaskGroup.objects.create(label='alphabetty:1', checker=checker, points=10)
        self.task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data='СЛОВО',
            answer='СЛОВО',
            points=10,
        )
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg, number='1', name='Алфавитка #1',
        )
        self.client = Client()

    def tearDown(self):
        # In-memory extras переживают rollback БД между тестами.
        invalidate_approved_extras()

    def test_progress_api_returns_rows(self):
        # Раньше 500: list от filter_published_* передавали в order_queryset_by_number.
        self.client.cookies['interoves_anon'] = 'test-anon-alphabetty-progress'
        resp = self.client.get('/alphabetty/progress/')
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()['rows']
        self.assertIn('1', rows)
        self.assertEqual(rows['1']['n_solved'], 0)
        self.assertFalse(rows['1']['is_fully_solved'])

    def test_guess_flow(self):
        # earlier
        r = self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'год', 'anon_key': 'testanon1'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='testanon1',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['status'], 'earlier')
        self.assertIn('ГОД', data['earlier'])

        # later
        r = self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'яблоко', 'anon_key': 'testanon1'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='testanon1',
        )
        data = r.json()
        self.assertEqual(data['status'], 'later')
        self.assertEqual(data['bounds']['lo'], 'ГОД')
        self.assertEqual(data['bounds']['hi'], 'ЯБЛОКО')

        # invalid
        r = self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'qqqqqq', 'anon_key': 'testanon1'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='testanon1',
        )
        self.assertEqual(r.json()['status'], 'invalid')

        # correct
        r = self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'слово', 'anon_key': 'testanon1'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='testanon1',
        )
        data = r.json()
        self.assertEqual(data['status'], 'correct')
        self.assertTrue(data['won'])
        self.assertEqual(data['secret'], 'СЛОВО')
        from decimal import Decimal
        from games.models import Attempt
        ok = Attempt.manager.filter(
            task=self.task, anon_key='testanon1', status='Ok',
        ).first()
        self.assertIsNotNone(ok)
        self.assertEqual(ok.points, Decimal('10'))
        ai = Attempt.manager.get_attempts_info(
            team=None, task=self.task, mode='general',
            user=None, anon_key='testanon1', game=self.game,
        )
        self.assertEqual(ai.get_sum_hint_penalty(), 0)
        self.assertEqual(ai.get_result_points(), Decimal('10'))

    def test_hint_flow(self):
        r = self.client.post(
            '/alphabetty/1/hint/',
            data=json.dumps({'anon_key': 'hintanon'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='hintanon',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['hint_prefix'], 'С')
        self.assertEqual(data['hints'], 1)
        self.assertEqual(data['next_hint_letter'], 2)

        r = self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'слово', 'anon_key': 'hintanon'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='hintanon',
        )
        data = r.json()
        self.assertEqual(data['status'], 'correct')
        self.assertEqual(data['hints'], 1)
        self.assertIn('💡 Взята 1 подсказка', '\n'.join(data['share_lines']))
        from decimal import Decimal
        from games.models import Attempt
        ok = Attempt.manager.filter(
            task=self.task, anon_key='hintanon', status='Ok',
        ).first()
        self.assertIsNotNone(ok)
        self.assertEqual(ok.points, Decimal('10'))
        ai = Attempt.manager.get_attempts_info(
            team=None, task=self.task, mode='general',
            user=None, anon_key='hintanon', game=self.game,
        )
        self.assertEqual(ai.get_sum_hint_penalty(), 1)
        self.assertEqual(ai.get_result_points(), Decimal('9'))

    def test_hint_skips_letters_known_from_guesses(self):
        checker = CheckerType.objects.get(id='alphabetty')
        tg = TaskGroup.objects.create(label='alphabetty:psy', checker=checker, points=1)
        task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data='ПСИХОЛОГ',
            answer='ПСИХОЛОГ',
            points=1,
        )
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg, number='9', name='Алфавитка #9',
        )
        anon = 'hint-skip'
        self.client.post(
            '/alphabetty/9/guess/',
            data=json.dumps({'word': 'псих', 'anon_key': anon}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=anon,
        )
        self.client.post(
            '/alphabetty/9/guess/',
            data=json.dumps({'word': 'психотерапия', 'anon_key': anon}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=anon,
        )
        r = self.client.post(
            '/alphabetty/9/hint/',
            data=json.dumps({'anon_key': anon}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=anon,
        )
        data = r.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['known_prefix'], 'ПСИХО')
        self.assertEqual(data['next_hint_letter'], 6)
        self.assertEqual(data['hint_reveal']['position'], 5)
        self.assertEqual(data['hint_reveal']['letter'], 'О')
        self.assertEqual(data['hint_prefix'], 'ПСИХО')
        self.assertEqual(data['hints'], 1)

        r2 = self.client.post(
            '/alphabetty/9/hint/',
            data=json.dumps({'anon_key': anon}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=anon,
        )
        data2 = r2.json()
        self.assertEqual(data2['status'], 'ok')
        self.assertEqual(data2['hint_reveal']['position'], 6)
        self.assertEqual(data2['hint_prefix'], 'ПСИХОЛ')
        self.assertEqual(data2['hints'], 2)

    def test_play_page_ok(self):
        _ensure_login_modal_deps()
        r = self.client.get('/alphabetty/1/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Алфавитка')
        self.assertContains(r, 'href="/alphabetty/1/results/"')

    def test_hub_page_has_create_link(self):
        _ensure_login_modal_deps()
        response = self.client.get('/alphabetty/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Создать свою алфавитку')
        self.assertContains(response, 'href="/create_alphabetty/"')

    def test_create_page_shows_offer_flow(self):
        user = User.objects.create_user('ab_creator', 'ab@example.com', 'x')
        Profile.objects.create(
            user=user,
            first_name='А',
            last_name='Б',
            telegram_handle='ab_creator',
        )
        self.client.force_login(user)

        response = self.client.get('/create_alphabetty/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Создать свою алфавитку')
        self.assertEqual(response.context['offers_json'], [])
        self.assertContains(response, 'alphabetty-offers-bootstrap')
        self.assertContains(response, 'Добавить алфавитку')
        self.assertNotContains(response, 'Номер раунда')

    def test_create_offer_update_and_send(self):
        user = User.objects.create_user('ab_submitter', 'ab2@example.com', 'x')
        Profile.objects.create(
            user=user,
            first_name='А',
            last_name='Б',
            telegram_handle='ab_submitter',
        )
        self.client.force_login(user)

        create_response = self.client.post(
            '/create_alphabetty/create/',
            content_type='application/json',
            data='{}',
        )
        self.assertEqual(create_response.status_code, 200)
        offer = create_response.json()['offer']
        self.assertEqual(offer['status'], 'draft')
        self.assertTrue(offer['play_url'].startswith('/alphabetty/'))

        update_response = self.client.post(
            f"/create_alphabetty/{offer['id']}/",
            data=json.dumps({'word': _FAKE_WORD, 'comment': 'проверьте'}),
            content_type='application/json',
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()['offer']
        self.assertEqual(updated['word'], _FAKE_WORD)
        self.assertEqual(updated['comment'], 'проверьте')

        send_response = self.client.post(
            f"/create_alphabetty/{offer['id']}/send/",
            content_type='application/json',
            data='{}',
        )
        self.assertEqual(send_response.status_code, 200)
        sent = send_response.json()['offer']
        self.assertEqual(sent['status'], 'sent')
        self.assertEqual(sent['word'], _FAKE_WORD)

    def test_hash_page_public_for_draft_offer(self):
        user = User.objects.create_user('ab_owner', 'owner@example.com', 'x')
        Profile.objects.create(
            user=user,
            first_name='А',
            last_name='Б',
            telegram_handle='ab_owner',
        )
        self.client.force_login(user)
        offer_id = self.client.post(
            '/create_alphabetty/create/',
            content_type='application/json',
            data='{}',
        ).json()['offer']['id']
        self.client.post(
            f'/create_alphabetty/{offer_id}/',
            content_type='application/json',
            data=json.dumps({'word': _FAKE_WORD, 'comment': ''}),
        )
        from games.models import AlphabettyOffer
        offer = AlphabettyOffer.objects.get(pk=offer_id)

        _ensure_login_modal_deps()
        anon = Client()
        response = anon.get(f'/alphabetty/{offer.share_hash}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Алфавитка #{offer.share_hash}')
        self.assertContains(response, f'/alphabetty/{offer.share_hash}/guess/')

    def test_hash_like_dislike_works(self):
        user = User.objects.create_user('ab_like_owner', 'ab_like@example.com', 'x')
        Profile.objects.create(
            user=user,
            first_name='А',
            last_name='Б',
            telegram_handle='ab_like_owner',
        )
        self.client.force_login(user)
        offer_id = self.client.post(
            '/create_alphabetty/create/',
            content_type='application/json',
            data='{}',
        ).json()['offer']['id']
        self.client.post(
            f'/create_alphabetty/{offer_id}/',
            content_type='application/json',
            data=json.dumps({'word': _FAKE_WORD, 'comment': ''}),
        )
        from games.models import AlphabettyOffer
        offer = AlphabettyOffer.objects.get(pk=offer_id)
        task = Task.objects.get(task_group_id=offer.task_group_id, number='1')

        _ensure_login_modal_deps()
        page = self.client.get(f'/alphabetty/{offer.share_hash}/')
        self.assertEqual(page.status_code, 200)

        response = self.client.post(
            f'/like-dislike/{task.id}/',
            data={'likes': '1', 'dislikes': '0', 'game_id': ALPHABETTY_GAME_ID},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['likes'], 1)
        self.assertEqual(data['dislikes'], 0)
        self.assertTrue(data['liked'])
        self.assertFalse(data['disliked'])

    def test_hash_embargo_after_accept_unpublished(self):
        tags = dict(self.game.tags or {})
        tags[ALPHABETTY_PUBLISH_START_TAG] = '2099-01-01T00:00:00+03:00'
        self.game.tags = tags
        self.game.save(update_fields=['tags'])
        user = User.objects.create_user('ab_embargo', 'embargo@example.com', 'x')
        Profile.objects.create(
            user=user,
            first_name='А',
            last_name='Б',
            telegram_handle='ab_embargo',
        )
        self.client.force_login(user)
        offer_id = self.client.post(
            '/create_alphabetty/create/',
            content_type='application/json',
            data='{}',
        ).json()['offer']['id']
        self.client.post(
            f'/create_alphabetty/{offer_id}/',
            content_type='application/json',
            data=json.dumps({'word': _FAKE_WORD, 'comment': ''}),
        )
        self.client.post(
            f'/create_alphabetty/{offer_id}/send/',
            content_type='application/json',
            data='{}',
        )
        from games.models import AlphabettyOffer
        offer = AlphabettyOffer.objects.get(pk=offer_id)
        accept_alphabetty_offer(offer)
        offer.refresh_from_db()

        _ensure_login_modal_deps()
        anon = Client()
        denied = anon.get(f'/alphabetty/{offer.share_hash}/')
        self.assertEqual(denied.status_code, 404)

        owner = Client()
        owner.force_login(user)
        allowed = owner.get(f'/alphabetty/{offer.share_hash}/')
        self.assertEqual(allowed.status_code, 200)

    def test_play_page_pager_neighbors(self):
        _ensure_login_modal_deps()
        checker = CheckerType.objects.get(id='alphabetty')
        tg2 = TaskGroup.objects.create(label='alphabetty:2', checker=checker, points=1)
        Task.objects.create(
            task_group=tg2,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data='ГОД',
            answer='ГОД',
            points=1,
        )
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg2, number='2', name='Алфавитка #2',
        )
        # Будущий слот не должен попадать в «Дальше».
        tg_future = TaskGroup.objects.create(label='alphabetty:50000', checker=checker, points=1)
        Task.objects.create(
            task_group=tg_future,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data='КОТ',
            answer='КОТ',
            points=1,
        )
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg_future, number='50000', name='Алфавитка #50000',
        )

        r1 = self.client.get('/alphabetty/1/')
        self.assertEqual(r1.status_code, 200)
        self.assertContains(r1, 'href="/alphabetty/2/"')
        self.assertNotContains(r1, 'href="/alphabetty/50000/"')
        self.assertContains(r1, 'Дальше')
        self.assertNotContains(r1, 'new-tg-pager__link--prev')

        r2 = self.client.get('/alphabetty/2/')
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, 'href="/alphabetty/1/"')
        self.assertContains(r2, 'Назад')
        self.assertNotContains(r2, 'href="/alphabetty/50000/"')
        self.assertNotContains(r2, 'new-tg-pager__link--next')

    def test_last_redirects_to_latest_published(self):
        checker = CheckerType.objects.get(id='alphabetty')
        tg2 = TaskGroup.objects.create(label='alphabetty:2', checker=checker, points=1)
        Task.objects.create(
            task_group=tg2,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data='ГОД',
            answer='ГОД',
            points=1,
        )
        GameTaskGroup.objects.create(
            game=self.game, task_group=tg2, number='2', name='Алфавитка #2',
        )
        r = self.client.get('/alphabetty/last/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], '/alphabetty/2/')

    def test_last_without_published_goes_to_hub(self):
        GameTaskGroup.objects.filter(game=self.game).delete()
        r = self.client.get('/alphabetty/last/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], '/alphabetty/')

    def test_prefix_endpoint(self):
        r = self.client.get(
            '/alphabetty/1/prefix/',
            {'lo': 'римлянин', 'hi': 'рисунок'},
        )
        self.assertEqual(r.status_code, 200)
        rows = r.json()['rows']
        self.assertEqual(rows[0]['display'], 'РИМ+')

    def test_state_endpoint_uses_anon_header(self):
        self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'год', 'anon_key': 'stateanon'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='stateanon',
        )
        r = self.client.get(
            '/alphabetty/1/state/',
            HTTP_X_INTEROVES_ANON='stateanon',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['status'], 'ok')
        self.assertIn('ГОД', data['earlier'])

    def test_unguessable_secret_allowed_in_support(self):
        from games.models import Task
        from games.support.services.alphabetty import _create_slot
        link = _create_slot(number=99, word='QQNOTAWORD')
        self.assertEqual(link.number, '99')
        self.assertEqual(
            Task.objects.get(task_group=link.task_group, number='1').answer,
            'QQNOTAWORD',
        )

    def test_get_play_state_does_not_create_empty_cts(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user('ab_viewer', 'ab@example.com', 'x')
        before = ChainTaskState.objects.filter(user=user, task=self.task).count()
        payload = get_play_state(game=self.game, task=self.task, user=user)
        self.assertEqual(payload['attempts'], 0)
        self.assertEqual(
            ChainTaskState.objects.filter(user=user, task=self.task).count(),
            before,
        )
        apply_guess(game=self.game, task=self.task, word='год', user=user)
        self.assertEqual(ChainTaskState.objects.filter(user=user, task=self.task).count(), 1)

    def test_invalid_guess_does_not_create_chain_task_state(self):
        before = ChainTaskState.objects.filter(anon_key='inv1', task=self.task).count()
        r = self.client.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': 'QQNOTAWORD', 'anon_key': 'inv1'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='inv1',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'invalid')
        self.assertEqual(
            ChainTaskState.objects.filter(anon_key='inv1', task=self.task).count(),
            before,
        )

    def test_suggest_and_approve_makes_word_valid(self):
        self.assertFalse(is_valid_guess(_FAKE_WORD))
        r = self.client.post(
            '/alphabetty/1/suggest/',
            data=json.dumps({'word': _FAKE_WORD, 'anon_key': 'sug1'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='sug1',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'ok')
        obj = AlphabettyDictSuggestion.objects.get(word=_FAKE_WORD)
        self.assertEqual(obj.status, AlphabettyDictSuggestion.STATUS_PENDING)

        # Автору слово сразу валидно в личном словаре, но не глобально.
        self.assertTrue(is_valid_guess(_FAKE_WORD, anon_key='sug1'))
        self.assertFalse(is_valid_guess(_FAKE_WORD, anon_key='other'))
        self.assertFalse(is_valid_guess(_FAKE_WORD))

        # Отдельный Client: иначе cookie interoves_anon от sug1 перебьёт header.
        c2 = Client()
        r2 = c2.post(
            '/alphabetty/1/suggest/',
            data=json.dumps({'word': _FAKE_WORD, 'anon_key': 'sug2'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='sug2',
        )
        self.assertEqual(r2.json()['status'], 'already_pending')
        self.assertEqual(
            r2.json()['message'],
            f'Слова {_FAKE_WORD} нет в словаре, но мы добавили его для вас',
        )
        obj.refresh_from_db()
        self.assertEqual(obj.suggest_count, 2)
        self.assertTrue(is_valid_guess(_FAKE_WORD, anon_key='sug2'))

        approve_suggestions(AlphabettyDictSuggestion.objects.filter(pk=obj.pk))
        invalidate_approved_extras()
        self.assertTrue(is_valid_guess(_FAKE_WORD))

        # После approve guess принимает слово
        c3 = Client()
        r3 = c3.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': _FAKE_WORD, 'anon_key': 'sug3'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='sug3',
        )
        self.assertEqual(r3.json()['status'], 'earlier')  # Б… < СЛОВО

    def test_suggest_makes_guess_valid_for_proposer(self):
        suggest_word(_FAKE_WORD, anon_key='me-only')
        c_me = Client()
        r = c_me.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': _FAKE_WORD, 'anon_key': 'me-only'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='me-only',
        )
        self.assertEqual(r.json()['status'], 'earlier')
        c_other = Client()
        r_other = c_other.post(
            '/alphabetty/1/guess/',
            data=json.dumps({'word': _FAKE_WORD, 'anon_key': 'stranger'}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON='stranger',
        )
        self.assertEqual(r_other.json()['status'], 'invalid')

    def test_approve_for_answer_adds_to_answer_pool(self):
        suggest_word('ЮЮЮЮЮЮЮЮЮЮ', anon_key='ans1')
        qs = AlphabettyDictSuggestion.objects.filter(word='ЮЮЮЮЮЮЮЮЮЮ')
        self.assertNotIn('ЮЮЮЮЮЮЮЮЮЮ', get_answer_pool())
        approve_suggestions_for_answer(qs)
        invalidate_approved_extras()
        self.assertTrue(is_valid_guess('ЮЮЮЮЮЮЮЮЮЮ'))
        self.assertIn('ЮЮЮЮЮЮЮЮЮЮ', get_answer_pool())
        self.assertEqual(
            AlphabettyDictSuggestion.objects.get(word='ЮЮЮЮЮЮЮЮЮЮ').status,
            AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
        )
        # Обычный approve не даунгрейдит ApprovedAnswer
        approve_suggestions(qs)
        self.assertEqual(
            AlphabettyDictSuggestion.objects.get(word='ЮЮЮЮЮЮЮЮЮЮ').status,
            AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
        )

    def test_reject_keeps_word_invalid_globally(self):
        suggest_word('ЗЗЗЗЗЗЗЗЗЗЗЗЗ', anon_key='rej1')
        qs = AlphabettyDictSuggestion.objects.filter(word='ЗЗЗЗЗЗЗЗЗЗЗЗЗ')
        reject_suggestions(qs)
        invalidate_approved_extras()
        self.assertFalse(is_valid_guess('ЗЗЗЗЗЗЗЗЗЗЗЗЗ'))
        # У автора остаётся в личном словаре
        self.assertTrue(is_valid_guess('ЗЗЗЗЗЗЗЗЗЗЗЗЗ', anon_key='rej1'))
        self.assertEqual(
            AlphabettyDictSuggestion.objects.get(word='ЗЗЗЗЗЗЗЗЗЗЗЗЗ').status,
            AlphabettyDictSuggestion.STATUS_REJECTED,
        )

    def test_rare_task_answer_is_accepted_without_wide_dictionary(self):
        checker = CheckerType.objects.get(id='alphabetty')
        tg = TaskGroup.objects.create(label='ab-round-test', checker=checker, points=10)
        task = Task.objects.create(
            task_group=tg,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data=_FAKE_WORD,
            answer=_FAKE_WORD,
            points=10,
        )
        GameTaskGroup.objects.create(
            game=self.game,
            task_group=tg,
            number='77',
            name='Алфавитка #77',
        )
        self.assertFalse(is_valid_guess(_FAKE_WORD))
        self.assertTrue(is_valid_guess(_FAKE_WORD, task=task))
        result = apply_guess(
            game=self.game,
            task=task,
            word=_FAKE_WORD,
            anon_key='create1',
        )
        self.assertEqual(result['status'], 'correct')
        self.assertTrue(result['won'])
