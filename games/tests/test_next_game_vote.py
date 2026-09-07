import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from allauth.socialaccount.models import SocialApp

from games.models import (
    HTMLPage,
    NextGameVoteAdjustment,
    NextGameVoteEvent,
    Project,
    StatisticsEvent,
)
from games.next_game_vote import (
    CANDIDATE_CRYPTIC,
    CANDIDATE_LOGIC,
    CANDIDATE_REDACTLE,
    add_adjustment,
    exclude_event,
    freeze_manually,
    original_minor_to_eur_cents,
    process_donation_event,
    scoreboard,
)
from games.tribute_util import compute_webhook_signature


MOSCOW = ZoneInfo('Europe/Moscow')

VOTE_SETTINGS = {
    'TRIBUTE_API_KEY': 'test-tribute-key',
    'TRIBUTE_NEXT_GAME_VOTE_REDACTLE_URL': 'https://web.tribute.tg/g/redactle-vote',
    'TRIBUTE_NEXT_GAME_VOTE_REDACTLE_DONATION_REQUEST_ID': '101',
    'TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_URL': 'https://web.tribute.tg/g/cryptic-vote',
    'TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_DONATION_REQUEST_ID': '202',
    'TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_URL': 'https://web.tribute.tg/g/logic-vote',
    'TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_DONATION_REQUEST_ID': '303',
}

LIVE_MOMENT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=MOSCOW)
BEFORE_START = datetime(2026, 9, 6, 12, 0, 0, tzinfo=MOSCOW)
AFTER_END = datetime(2026, 9, 28, 0, 0, 1, tzinfo=MOSCOW)
LAST_SECOND = datetime(2026, 9, 27, 23, 59, 59, tzinfo=MOSCOW)


def _ensure_page_deps():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
    for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
        app, created = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        if created:
            app.sites.add(site)


def _envelope(payload, *, event='new_donation', created_at='2026-09-10T12:00:00+03:00', donation_id=None):
    body = dict(payload)
    if donation_id is not None:
        body['donation_id'] = donation_id
    return {
        'name': event,
        'created_at': created_at,
        'sent_at': '2026-09-10T12:00:05+03:00',
        'payload': body,
    }


def _payload(donation_request_id, amount, currency, **overrides):
    payload = {
        'donation_request_id': donation_request_id,
        'amount': amount,
        'currency': currency,
        'period': 'once',
        'telegram_user_id': 555001,
        'trb_user_id': 'T-1',
    }
    payload.update(overrides)
    return payload


@override_settings(**VOTE_SETTINGS)
class NextGameVoteFxTests(TestCase):
    def test_eur_is_not_converted(self):
        self.assertEqual(original_minor_to_eur_cents(1500, 'EUR'), 1500)

    def test_rub_uses_frozen_rate(self):
        self.assertEqual(original_minor_to_eur_cents(10000, 'RUB'), 100)

    def test_decimal_rounding_half_up_to_cents(self):
        self.assertEqual(original_minor_to_eur_cents(5000, 'RUB'), 50)
        # $1 / 1.16 = €0.8620… → 86 cents
        self.assertEqual(original_minor_to_eur_cents(100, 'USD'), 86)
        # $1.16 → €1.00 exactly
        self.assertEqual(original_minor_to_eur_cents(116, 'USD'), 100)


@override_settings(**VOTE_SETTINGS)
class NextGameVoteWebhookTests(TestCase):
    def setUp(self):
        self.http = Client()

    def _post(self, envelope, *, signature=True):
        body = json.dumps(envelope).encode()
        sig = compute_webhook_signature(body, VOTE_SETTINGS['TRIBUTE_API_KEY']) if signature else 'deadbeef'
        return self.http.post(
            '/tribute/webhook/',
            data=body,
            content_type='application/json',
            HTTP_TRBT_SIGNATURE=sig,
        )

    def test_invalid_signature_is_rejected(self):
        envelope = _envelope(_payload('101', 1000, 'eur'), donation_id=1)
        response = self._post(envelope, signature=False)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(NextGameVoteEvent.objects.count(), 0)

    def test_one_payment_one_contribution(self):
        envelope = _envelope(_payload('101', 1000, 'eur'), donation_id=11)
        response = self._post(envelope)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NextGameVoteEvent.objects.filter(include_in_scoreboard=True).count(), 1)
        event = NextGameVoteEvent.objects.get()
        self.assertEqual(event.candidate, CANDIDATE_REDACTLE)
        self.assertEqual(event.normalized_amount_eur_cents, 1000)
        self.assertEqual(event.original_currency, 'EUR')

    def test_duplicate_delivery_does_not_double_count(self):
        envelope = _envelope(_payload('101', 1000, 'eur'), donation_id=12)
        self.assertEqual(self._post(envelope).status_code, 200)
        self.assertEqual(self._post(envelope).status_code, 200)
        self.assertEqual(NextGameVoteEvent.objects.count(), 1)
        self.assertEqual(NextGameVoteEvent.objects.get().normalized_amount_eur_cents, 1000)
        board = scoreboard(now=LIVE_MOMENT)
        self.assertEqual(board['totals'][CANDIDATE_REDACTLE], 1000)

    def test_two_payments_same_goal_both_count(self):
        self._post(_envelope(_payload('101', 1000, 'eur'), donation_id=21))
        self._post(_envelope(_payload('101', 2500, 'eur'), donation_id=22))
        self.assertEqual(NextGameVoteEvent.objects.filter(include_in_scoreboard=True).count(), 2)
        board = scoreboard(now=LIVE_MOMENT)
        self.assertEqual(board['totals'][CANDIDATE_REDACTLE], 3500)

    def test_three_goals_map_to_three_candidates(self):
        self._post(_envelope(_payload('101', 100, 'eur'), donation_id=31))
        self._post(_envelope(_payload('202', 200, 'eur'), donation_id=32))
        self._post(_envelope(_payload('303', 300, 'eur'), donation_id=33))
        by_candidate = dict(
            NextGameVoteEvent.objects.values_list('candidate', 'normalized_amount_eur_cents')
        )
        self.assertEqual(by_candidate[CANDIDATE_REDACTLE], 100)
        self.assertEqual(by_candidate[CANDIDATE_CRYPTIC], 200)
        self.assertEqual(by_candidate[CANDIDATE_LOGIC], 300)

    def test_unrelated_donation_is_ignored_when_mapping_ready(self):
        response = self._post(_envelope(_payload('999', 5000, 'eur'), donation_id=40))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NextGameVoteEvent.objects.count(), 0)

    def test_recurrent_donation_does_not_count(self):
        envelope = _envelope(_payload('101', 1000, 'eur'), event='recurrent_donation', donation_id=41)
        self._post(envelope)
        event = NextGameVoteEvent.objects.get()
        self.assertEqual(event.result, NextGameVoteEvent.RESULT_IGNORED_EVENT)
        self.assertFalse(event.include_in_scoreboard)

    def test_payment_at_deadline_counts(self):
        envelope = _envelope(
            _payload('101', 1000, 'eur'),
            created_at=LAST_SECOND.isoformat(),
            donation_id=51,
        )
        self._post(envelope)
        self.assertTrue(NextGameVoteEvent.objects.get().include_in_scoreboard)

    def test_payment_after_deadline_does_not_change_official_result(self):
        self._post(_envelope(_payload('101', 1000, 'eur'), created_at=LAST_SECOND.isoformat(), donation_id=52))
        after = _envelope(
            _payload('202', 9000, 'eur'),
            created_at=AFTER_END.isoformat(),
            donation_id=53,
        )
        self._post(after)
        board = scoreboard(now=AFTER_END)
        self.assertEqual(board['totals'][CANDIDATE_REDACTLE], 1000)
        self.assertEqual(board['totals'][CANDIDATE_CRYPTIC], 0)
        self.assertEqual(board['phase'], 'closed')
        late = NextGameVoteEvent.objects.get(tribute_donation_id='53')
        self.assertEqual(late.result, NextGameVoteEvent.RESULT_OUTSIDE_PERIOD)
        self.assertFalse(late.include_in_scoreboard)

    def test_composite_idempotency_without_donation_id(self):
        envelope = _envelope(_payload('101', 1000, 'eur'))
        process_donation_event(envelope)
        process_donation_event(envelope)
        self.assertEqual(NextGameVoteEvent.objects.count(), 1)


@override_settings(**VOTE_SETTINGS)
class NextGameVoteScoreboardTests(TestCase):
    def test_zero_state(self):
        board = scoreboard(now=LIVE_MOMENT)
        self.assertTrue(board['is_zero'])
        self.assertEqual(board['total_eur_cents'], 0)
        self.assertEqual(board['leaders'], [])
        self.assertIsNone(board['candidates'][0]['percent'])
        self.assertEqual(board['candidates'][0]['bar_percent'], 0)

    def test_unique_leader(self):
        process_donation_event(_envelope(_payload('101', 4200, 'eur'), donation_id=61))
        process_donation_event(_envelope(_payload('202', 3500, 'eur'), donation_id=62))
        process_donation_event(_envelope(_payload('303', 2300, 'eur'), donation_id=63))
        board = scoreboard(now=LIVE_MOMENT)
        self.assertEqual(board['unique_leader'], CANDIDATE_REDACTLE)
        self.assertFalse(board['is_tie'])
        redactle = board['candidates'][0]
        self.assertTrue(redactle['is_leader'])
        self.assertEqual(redactle['percent'], 42)
        self.assertEqual(board['total_eur_cents'], 10000)

    def test_tie_does_not_pick_a_leader(self):
        process_donation_event(_envelope(_payload('101', 2000, 'eur'), donation_id=71))
        process_donation_event(_envelope(_payload('202', 2000, 'eur'), donation_id=72))
        board = scoreboard(now=LIVE_MOMENT)
        self.assertTrue(board['is_tie'])
        self.assertEqual(board['unique_leader'], '')
        self.assertTrue(board['candidates'][0]['is_tied_leader'])
        self.assertTrue(board['candidates'][1]['is_tied_leader'])
        self.assertFalse(board['candidates'][2]['is_tied_leader'])

    def test_manual_adjustment_changes_totals_and_keeps_audit(self):
        process_donation_event(_envelope(_payload('101', 1000, 'eur'), donation_id=81))
        user = User.objects.create_user('adj-user', password='secret')
        add_adjustment(candidate=CANDIDATE_CRYPTIC, amount_eur_cents=400, comment='Test correction', user=user)
        board = scoreboard(now=LIVE_MOMENT)
        self.assertEqual(board['totals'][CANDIDATE_CRYPTIC], 400)
        adjustment = NextGameVoteAdjustment.objects.get()
        self.assertEqual(adjustment.comment, 'Test correction')
        self.assertEqual(adjustment.created_by, user)
        event = NextGameVoteEvent.objects.get()
        self.assertEqual(event.normalized_amount_eur_cents, 1000)

    def test_exclude_does_not_edit_original_amount(self):
        process_donation_event(_envelope(_payload('101', 1000, 'eur'), donation_id=82))
        event = NextGameVoteEvent.objects.get()
        exclude_event(event, reason='test payment')
        event.refresh_from_db()
        self.assertEqual(event.original_amount_minor, 1000)
        self.assertTrue(event.excluded)
        self.assertFalse(event.include_in_scoreboard)
        self.assertTrue(scoreboard(now=LIVE_MOMENT)['is_zero'])

    def test_payment_event_is_server_side_only(self):
        process_donation_event(_envelope(_payload('101', 1000, 'eur'), donation_id=83))
        stats = StatisticsEvent.objects.filter(kind='next_game_vote_payment')
        self.assertEqual(stats.count(), 1)
        payload = stats.get().payload
        self.assertEqual(payload['candidate'], CANDIDATE_REDACTLE)
        self.assertEqual(payload['currency'], 'EUR')
        self.assertEqual(payload['normalized_amount_eur'], '10.00')
        self.assertNotIn('telegram_user_id', payload)


@override_settings(**VOTE_SETTINGS)
class NextGameVotePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_page_deps()

    def setUp(self):
        self.http = Client()

    def test_page_is_public(self):
        with patch('games.next_game_vote.timezone.now', return_value=LIVE_MOMENT):
            response = self.http.get('/vote/next-game/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Выберите следующую игру', body)
        self.assertIn('next_game_vote_view', body)
        self.assertIn('Поддержать Redactle', body)
        self.assertIn('Поддержать Криптик', body)
        self.assertIn('Поддержать логические пазлы', body)
        self.assertIn('Какую игру сделать следующей? — Inter Oves', body)
        self.assertIn('next-game-vote-og.png', body)
        self.assertIn('российской картой', body)
        self.assertIn('иностранной картой', body)
        self.assertIn('криптокошелька', body)
        self.assertNotIn('Показать таблицу курса', body)
        self.assertNotIn('€1 = 100', body)
        self.assertNotIn('7–27 сентября 2026', body)
        self.assertNotIn('реализация в октябре', body)
        self.assertNotIn('3 кандидата', body)
        self.assertNotIn('Любая сумма от минимальной', body)
        self.assertIn('но все слова в ней скрыты', body)
        self.assertNotIn('почти все значимые слова', body)
        self.assertIn('https://redactle.net', body)
        self.assertIn('https://minutecryptic.com', body)
        self.assertIn('https://gmpuzzles.com/blog', body)
        self.assertIn('Порешать на английском', body)
        self.assertIn('Подборка логических пазлов', body)
        self.assertIn('https://interoves.com/games/des92/1/', body)
        self.assertIn('Десяточке 92', body)
        self.assertLess(body.index('Текущий счёт'), body.index('Как это работает'))

    def test_upcoming_disables_cta(self):
        with patch('games.next_game_vote.timezone.now', return_value=BEFORE_START):
            body = self.http.get('/vote/next-game/').content.decode()
        self.assertIn('Голосование начнётся 7 сентября', body)
        self.assertIn('disabled', body)
        self.assertNotIn('data-vote-cta', body)

    def test_live_shows_countdown_and_ctas(self):
        with patch('games.next_game_vote.timezone.now', return_value=LIVE_MOMENT):
            body = self.http.get('/vote/next-game/').content.decode()
        self.assertIn('data-vote-countdown', body)
        self.assertIn('data-vote-cta="redactle"', body)
        self.assertIn('https://web.tribute.tg/g/redactle-vote', body)
        self.assertIn('target="_blank"', body)
        self.assertIn('rel="noopener noreferrer"', body)

    def test_closed_hides_cta_and_shows_winner(self):
        process_donation_event(_envelope(_payload('303', 5000, 'eur'), donation_id=91))
        with patch('games.next_game_vote.timezone.now', return_value=AFTER_END):
            body = self.http.get('/vote/next-game/').content.decode()
        self.assertIn('Голосование завершено', body)
        self.assertIn('Победитель — Логические пазлы', body)
        self.assertIn('Эту игру запускаем в октябре', body)
        self.assertNotIn('data-vote-cta', body)
        self.assertIn('€50', body)

    def test_zero_state_copy(self):
        with patch('games.next_game_vote.timezone.now', return_value=LIVE_MOMENT):
            body = self.http.get('/vote/next-game/').content.decode()
        self.assertIn('Пока ничья. Можно стать первым голосом', body)

    def test_tribute_return_goal_only_with_explicit_query(self):
        with patch('games.next_game_vote.timezone.now', return_value=LIVE_MOMENT):
            response = self.http.get('/vote/next-game/?from=tribute&candidate=redactle')
        body = response.content.decode()
        self.assertIn('next_game_vote_tribute_return', body)
        self.assertIn('redactle', body)

    def test_manual_freeze_hides_cta_before_deadline(self):
        freeze_manually(now=LIVE_MOMENT)
        with patch('games.next_game_vote.timezone.now', return_value=LIVE_MOMENT):
            body = self.http.get('/vote/next-game/').content.decode()
        self.assertIn('Голосование завершено', body)
        self.assertNotIn('data-vote-cta', body)


class NextGameVoteLayoutTests(SimpleTestCase):
    def test_mobile_stacks_and_desktop_is_three_columns(self):
        css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'next_game_vote.css').read_text()
        self.assertIn('.vote-grid {\n  display: grid;\n  grid-template-columns: 1fr;', css)
        self.assertIn('@media (min-width: 920px)', css)
        self.assertIn('grid-template-columns: repeat(3, minmax(0, 1fr));', css)
        self.assertIn('grid-template-columns: repeat(5, minmax(0, 1fr));', css)
        self.assertIn('min-width: 0', css)

    def test_cards_are_equal_in_template(self):
        html = (Path(__file__).resolve().parents[2] / 'static' / 'templates' / 'new' / 'next_game_vote.html').read_text()
        self.assertIn('{% for candidate in candidates %}', html)
        self.assertIn('vote-card vote-card--{{ candidate.slug }}', html)
        self.assertNotIn('рекоменд', html.lower())
        self.assertIn('vote-grid-demo', html)
        self.assertIn('vote-preview--redactle', html)
        self.assertIn('vote-preview--cryptic', html)
        self.assertIn('vote-preview--logic', html)
        self.assertIn('Италии', html)
        self.assertIn('Финансист поворачивается на гром', html)
        self.assertIn('Ломбардии', html)
        self.assertIn('https://redactle.net', html)
        self.assertIn('https://minutecryptic.com', html)
        self.assertIn('https://gmpuzzles.com/blog', html)
        self.assertIn('https://interoves.com/games/des92/1/', html)
        self.assertNotIn('vote-funnel', html)
        self.assertNotIn('vote-hero__meta', html)
        self.assertEqual(html.count('class="is-num"'), 6)

    def test_hub_cta_overrides_nowrap_and_spans_full_width(self):
        css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'new.css').read_text()
        self.assertIn('a.new-btn.new-hub-next-game-vote__btn', css)
        block_start = css.index('a.new-btn.new-hub-next-game-vote__btn')
        block = css[block_start:block_start + 400]
        self.assertIn('width: 100%', block)
        self.assertIn('white-space: normal', block)
        self.assertIn('min-height: var(--btn-height-mini)', block)


class NextGameVoteDefaultGoalUrlTests(SimpleTestCase):
    def test_telegram_mini_app_goal_urls(self):
        from django.conf import settings

        from games.tribute_config import next_game_vote_configuration_errors

        self.assertEqual(
            settings.TRIBUTE_NEXT_GAME_VOTE_REDACTLE_URL,
            'https://t.me/tribute/app?startapp=g68Y',
        )
        self.assertEqual(
            settings.TRIBUTE_NEXT_GAME_VOTE_CRYPTIC_URL,
            'https://t.me/tribute/app?startapp=g68Z',
        )
        self.assertEqual(
            settings.TRIBUTE_NEXT_GAME_VOTE_LOGIC_PUZZLES_URL,
            'https://t.me/tribute/app?startapp=g690',
        )
        self.assertEqual(next_game_vote_configuration_errors(), [])


class NextGameVoteConfiguredCtaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_page_deps()

    def test_live_ctas_use_telegram_goal_links(self):
        with patch('games.next_game_vote.timezone.now', return_value=LIVE_MOMENT):
            body = Client().get('/vote/next-game/').content.decode()
        self.assertIn('https://t.me/tribute/app?startapp=g68Y', body)
        self.assertIn('https://t.me/tribute/app?startapp=g68Z', body)
        self.assertIn('https://t.me/tribute/app?startapp=g690', body)
        self.assertIn('data-vote-cta="redactle"', body)
        self.assertNotIn('Ссылка Tribute появится после настройки цели.', body)
