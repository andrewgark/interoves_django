import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import Client, SimpleTestCase, TestCase, override_settings

from games.club.billing.tribute.util import compute_webhook_signature
from games.club.next_game import process_donation_event
from games.club.next_game_autumn import carryover_cents, half_cents, scoreboard
from games.models import (
    HTMLPage,
    NextGameAutumnGoalMapping,
    NextGameAutumnVoteEvent,
    NextGameVoteEvent,
    Project,
)

MOSCOW = ZoneInfo('Europe/Moscow')
LIVE = datetime(2026, 10, 28, 15, 0, tzinfo=MOSCOW)
BEFORE = datetime(2026, 10, 27, 23, 0, tzinfo=MOSCOW)
AFTER = datetime(2026, 11, 12, 0, 0, tzinfo=MOSCOW)

VOTE_SETTINGS = {
    'TRIBUTE_API_KEY': 'test-tribute-key',
    'TRIBUTE_NEXT_GAME_VOTE_REDACTLE_URL': 'https://web.tribute.tg/d/redactle-vote',
    'TRIBUTE_NEXT_GAME_VOTE_REDACTLE_DONATION_REQUEST_ID': '101',
    'TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_URL': 'https://web.tribute.tg/d/cryptic-vote',
    'TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_DONATION_REQUEST_ID': '202',
    'TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_URL': 'https://web.tribute.tg/d/logic-vote',
    'TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_DONATION_REQUEST_ID': '303',
    'TRIBUTE_NEXT_GAME_AUTUMN_POWDERS_URL': 'https://web.tribute.tg/d/powders-autumn',
    'TRIBUTE_NEXT_GAME_AUTUMN_CRYPTIC_URL': 'https://web.tribute.tg/d/cryptic-autumn',
    'TRIBUTE_NEXT_GAME_AUTUMN_LOGIC_URL': 'https://web.tribute.tg/d/logic-autumn',
}


def _ensure_page_deps():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    site, _created = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
    for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
        app, created = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        if created:
            app.sites.add(site)


def _donate(request_id, amount_minor, donation_id):
    process_donation_event({
        'name': 'new_donation',
        'created_at': '2026-09-10T12:00:00+03:00',
        'payload': {
            'donation_request_id': request_id,
            'donation_id': donation_id,
            'amount': amount_minor,
            'currency': 'eur',
            'period': 'once',
            'telegram_user_id': 555001,
        },
    })


@override_settings(**VOTE_SETTINGS)
class NextGameAutumnCarryoverTests(TestCase):
    def test_half_rounds_half_up(self):
        self.assertEqual(half_cents(3001), 1501)
        self.assertEqual(half_cents(4000), 2000)

    def test_only_september_losers_keep_half(self):
        _donate('303', 10000, 1)
        _donate('202', 4000, 2)
        _donate('101', 8000, 3)
        carried = carryover_cents()
        self.assertEqual(carried['logic'], 0)
        self.assertEqual(carried['cryptic'], 2000)
        self.assertEqual(carried['powders'], 0)
        board = scoreboard(now=LIVE)
        by_slug = {row['slug']: row for row in board['candidates']}
        self.assertEqual(by_slug['cryptic']['eur_display'], '€20')
        self.assertEqual(by_slug['cryptic']['carry_bar_percent'], 100)
        self.assertEqual(by_slug['logic']['eur_display'], '€0')
        self.assertTrue(board['has_carryover'])
        self.assertEqual(board['september_winner_name'], 'Логические пазлы')


@override_settings(**VOTE_SETTINGS)
class NextGameAutumnPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_page_deps()

    def setUp(self):
        self.http = Client()

    def test_live_page_has_examples_previous_vote_and_donation_links(self):
        _donate('303', 10000, 11)
        _donate('202', 4000, 12)
        with patch('games.club.next_game.timezone.now', return_value=LIVE):
            response = self.http.get('/vote/next-game/2026/october-november/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('до конца 11 ноября', body)
        self.assertIn('ноябре–декабре', body)
        self.assertIn('/vote/next-game/2026/september/', body)
        self.assertIn('сохранена половина накопившихся донатов', body)
        self.assertIn('vote-bar__carry', body)
        self.assertIn('Старт €20', body)
        self.assertIn('Победитель того сбора — Логические пазлы', body)
        self.assertIn('е три сказал сурово карпов', body)
        self.assertIn('placeholder="два слога"', body)
        self.assertIn('https://t.me/igraporoshki/304', body)
        self.assertIn('https://t.me/igraporoshki/53', body)
        self.assertIn('https://t.me/igraporoshki/240', body)
        self.assertIn('Спор внутри центра Сколково', body)
        self.assertIn('Служба в храме не белом в сторону запада', body)
        self.assertIn('Первое начало термодинамики', body)
        self.assertIn('Castle Wall', body)
        self.assertIn('Nurikabe', body)
        self.assertIn('>Cave<', body)
        self.assertIn('Правила Castle Wall', body)
        self.assertIn('появится отрезок петли', body)
        self.assertIn('Левая кнопка мыши закрашивает клетку (море)', body)
        self.assertIn('правая ставит точку (пещера)', body)
        self.assertIn('data-vote-grid=', body)
        self.assertIn('&quot;kind&quot;: &quot;loop&quot;', body)
        self.assertIn('Это пока всё — донатьте, чтобы этот кандидат победил :-)', body)
        self.assertIn('https://web.tribute.tg/d/powders-autumn', body)
        self.assertIn('data-vote-cta="powders"', body)
        self.assertIn('next_game_autumn_vote_view', body)
        self.assertNotIn('>убит<', body)
        self.assertNotIn('>физрук<', body)
        self.assertLess(body.index('Текущий счёт'), body.index('Как это работает'))
        script = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'next_game_autumn_vote.js').read_text()
        self.assertIn("'powders-1': 'убит'", script)
        self.assertIn("'cryptic-2': 'молебен'", script)
        self.assertIn("'cryptic-3': 'токсин'", script)

    def test_upcoming_disables_support_buttons(self):
        with patch('games.club.next_game.timezone.now', return_value=BEFORE):
            body = self.http.get('/vote/next-game/2026/october-november/').content.decode()
        self.assertIn('Голосование начнётся 28 октября', body)
        self.assertNotIn('data-vote-cta', body)

    def test_closed_names_the_leader(self):
        _donate('101', 9000, 21)
        _donate('202', 5000, 22)
        with patch('games.club.next_game.timezone.now', return_value=AFTER):
            body = self.http.get('/vote/next-game/2026/october-november/').content.decode()
        self.assertIn('Голосование завершено', body)
        self.assertIn('Победитель — Криптики', body)
        self.assertIn('Старт €25', body)
        self.assertIn('ноябре–декабре', body)


AUTUMN_LINKS = {
    **VOTE_SETTINGS,
    'TRIBUTE_NEXT_GAME_AUTUMN_POWDERS_URL': 'https://t.me/tribute/app?startapp=dT4m',
    'TRIBUTE_NEXT_GAME_AUTUMN_CRYPTIC_URL': 'https://t.me/tribute/app?startapp=dT4n',
    'TRIBUTE_NEXT_GAME_AUTUMN_LOGIC_URL': 'https://t.me/tribute/app?startapp=dT4o',
}


def _autumn_envelope(payload, *, created_at='2026-10-28T12:00:00+03:00', donation_id=None, event='new_donation'):
    body = dict(payload)
    if donation_id is not None:
        body['donation_id'] = donation_id
    return {
        'name': event,
        'created_at': created_at,
        'payload': body,
    }


def _autumn_payload(request_id, amount, currency='eur', **overrides):
    payload = {
        'donation_request_id': request_id,
        'amount': amount,
        'currency': currency,
        'period': 'once',
        'telegram_user_id': 555001,
    }
    payload.update(overrides)
    return payload


class NextGameAutumnDefaultLinkTests(SimpleTestCase):
    def test_telegram_mini_app_donation_urls(self):
        from django.conf import settings

        from games.club.billing.tribute.config import next_game_vote_configuration_errors

        self.assertEqual(
            settings.TRIBUTE_NEXT_GAME_AUTUMN_POWDERS_URL,
            'https://t.me/tribute/app?startapp=dT4m',
        )
        self.assertEqual(
            settings.TRIBUTE_NEXT_GAME_AUTUMN_CRYPTIC_URL,
            'https://t.me/tribute/app?startapp=dT4n',
        )
        self.assertEqual(
            settings.TRIBUTE_NEXT_GAME_AUTUMN_LOGIC_URL,
            'https://t.me/tribute/app?startapp=dT4o',
        )
        self.assertEqual(next_game_vote_configuration_errors(), [])


@override_settings(**AUTUMN_LINKS)
class NextGameAutumnWebhookTests(TestCase):
    def setUp(self):
        self.http = Client()

    def _post(self, envelope):
        body = json.dumps(envelope).encode()
        signature = compute_webhook_signature(body, AUTUMN_LINKS['TRIBUTE_API_KEY'])
        return self.http.post(
            '/tribute/webhook/',
            data=body,
            content_type='application/json',
            HTTP_TRBT_SIGNATURE=signature,
        )

    def test_new_donation_counts_for_powders_and_not_september(self):
        response = self._post(_autumn_envelope(
            _autumn_payload('701', 1500, web_app_link='https://t.me/tribute/app?startapp=dT4m'),
            donation_id=701,
        ))
        self.assertEqual(response.status_code, 200)
        event = NextGameAutumnVoteEvent.objects.get()
        self.assertEqual(event.candidate, 'powders')
        self.assertEqual(event.normalized_amount_eur_cents, 1500)
        self.assertTrue(event.include_in_scoreboard)
        self.assertEqual(NextGameVoteEvent.objects.count(), 0)
        self.assertEqual(NextGameAutumnGoalMapping.objects.get(candidate='powders').donation_request_id, '701')
        board = scoreboard(now=LIVE)
        by_slug = {row['slug']: row for row in board['candidates']}
        self.assertEqual(by_slug['powders']['fresh_cents'], 1500)
        self.assertEqual(by_slug['powders']['eur_display'], '€15')

    def test_duplicate_webhook_does_not_double_count(self):
        envelope = _autumn_envelope(
            _autumn_payload('701', 1500, web_app_link='https://t.me/tribute/app?startapp=dT4m'),
            donation_id=702,
        )
        self.assertEqual(self._post(envelope).status_code, 200)
        self.assertEqual(self._post(envelope).status_code, 200)
        self.assertEqual(NextGameAutumnVoteEvent.objects.count(), 1)
        self.assertEqual(scoreboard(now=LIVE)['total_eur_cents'], 1500)

    def test_web_path_and_title_map_the_other_two_candidates(self):
        self._post(_autumn_envelope(
            _autumn_payload('702', 2000, web_app_link='https://web.tribute.tg/d/T4n'),
            donation_id=703,
        ))
        self._post(_autumn_envelope(
            _autumn_payload('703', 3000, donation_name='Логички — следующая игра Inter Oves'),
            donation_id=704,
        ))
        by_candidate = dict(
            NextGameAutumnVoteEvent.objects.values_list('candidate', 'normalized_amount_eur_cents')
        )
        self.assertEqual(by_candidate['cryptic'], 2000)
        self.assertEqual(by_candidate['logic'], 3000)
        self.assertEqual(NextGameVoteEvent.objects.count(), 0)

    def test_learned_request_id_counts_without_the_link(self):
        self._post(_autumn_envelope(
            _autumn_payload('704', 1000, web_app_link='https://t.me/tribute/app?startapp=dT4o'),
            donation_id=705,
        ))
        self._post(_autumn_envelope(_autumn_payload('704', 500), donation_id=706))
        logic = NextGameAutumnVoteEvent.objects.filter(candidate='logic', include_in_scoreboard=True)
        self.assertEqual(logic.count(), 2)
        self.assertEqual(sum(logic.values_list('normalized_amount_eur_cents', flat=True)), 1500)

    def test_payment_before_the_vote_is_stored_and_not_scored(self):
        self._post(_autumn_envelope(
            _autumn_payload('705', 9000, web_app_link='https://t.me/tribute/app?startapp=dT4m'),
            donation_id=707,
            created_at='2026-10-27T23:00:00+03:00',
        ))
        event = NextGameAutumnVoteEvent.objects.get()
        self.assertEqual(event.result, NextGameAutumnVoteEvent.RESULT_OUTSIDE_PERIOD)
        self.assertFalse(event.include_in_scoreboard)
        self.assertEqual(scoreboard(now=LIVE)['total_eur_cents'], 0)

    def test_september_donation_stays_on_the_september_board(self):
        response = self._post(_autumn_envelope(
            _autumn_payload('101', 1000),
            donation_id=708,
            created_at='2026-09-10T12:00:00+03:00',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NextGameAutumnVoteEvent.objects.count(), 0)
        event = NextGameVoteEvent.objects.get()
        self.assertEqual(event.candidate, 'redactle')
        self.assertTrue(event.include_in_scoreboard)

    def test_live_page_enables_the_three_buttons_and_adds_fresh_money_beside_carryover(self):
        _ensure_page_deps()
        _donate('303', 10000, 31)
        _donate('202', 4000, 32)
        self._post(_autumn_envelope(
            _autumn_payload('706', 1500, web_app_link='https://t.me/tribute/app?startapp=dT4m'),
            donation_id=709,
        ))
        with patch('games.club.next_game.timezone.now', return_value=LIVE):
            body = self.http.get('/vote/next-game/2026/october-november/').content.decode()
        self.assertIn('https://t.me/tribute/app?startapp=dT4m', body)
        self.assertIn('https://t.me/tribute/app?startapp=dT4n', body)
        self.assertIn('https://t.me/tribute/app?startapp=dT4o', body)
        self.assertIn('data-vote-cta="powders"', body)
        self.assertIn('data-vote-cta="cryptic"', body)
        self.assertIn('data-vote-cta="logic"', body)
        self.assertNotIn('Ссылка Tribute появится после настройки доната.', body)
        self.assertIn('Старт €20', body)
        self.assertIn('€15', body)
        self.assertIn('Всего поддержано: €35', body)
