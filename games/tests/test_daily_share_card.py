from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from games.daily_share_card import (
    CARD_HEIGHT,
    CARD_WIDTH,
    HEADLINE_SOLVED_IN,
    KIND_ALPHABETTY,
    KIND_LADDER,
    KIND_SALAD,
    build_alphabetty_share_payload,
    build_ladder_share_payload,
    build_salad_share_payload,
    dumps_payload,
    format_share_date,
    synthetic_preview_payloads,
)
from games.raddle import default_raddle_state, parse_raddle_data
from games.tests.test_raddle import PARIS_LADDER
from games.word_salad import default_state as salad_default_state


def _png_bytes(width=CARD_WIDTH, height=CARD_HEIGHT, color=(30, 80, 50)):
    image = Image.new('RGB', (width, height), color)
    buf = BytesIO()
    image.save(buf, format='PNG')
    return buf.getvalue()


def _paris_payload(**kwargs):
    parsed = parse_raddle_data(type('T', (), {
        'task_type': 'raddle',
        'checker_data': __import__('json').dumps(PARIS_LADDER, ensure_ascii=False),
        'answer': '',
    })())
    n = parsed['n_words']
    state = default_raddle_state(n)
    state['solved_indices'] = list(range(n))
    defaults = dict(
        parsed=parsed,
        state=state,
        number=46,
        date_value=date(2026, 9, 3),
        elapsed_seconds=272,
        locale='ru',
    )
    defaults.update(kwargs)
    return build_ladder_share_payload(**defaults)


class DailyShareCardPayloadTests(SimpleTestCase):
    def test_ladder_payload_is_structured_and_has_no_answers(self):
        payload = _paris_payload()
        blob = dumps_payload(payload)
        self.assertEqual(payload['kind'], KIND_LADDER)
        self.assertEqual(payload['title'], 'Лесенка #46')
        self.assertEqual(payload['headline'], 'Лесенка · 4:32')
        self.assertEqual(payload['date_label'], '3 сентября 2026')
        self.assertIn('steps', payload)
        self.assertGreater(len(payload['steps']), 2)
        self.assertNotIn('ПАРИЖ', blob)
        self.assertNotIn('ДАКАР', blob)
        self.assertNotIn('МОСКВА', blob)
        self.assertNotIn('words', payload)
        self.assertEqual(payload['steps'][0]['state'], 'given')
        self.assertEqual(payload['steps'][-1]['state'], 'given')
        self.assertTrue(all(step['state'] == 'green' for step in payload['steps'][1:-1]))

    def test_ladder_imperfect_uses_assist_states_not_emoji(self):
        payload = _paris_payload()
        parsed = payload  # rebuild with assist
        parsed = _paris_payload()
        from games.raddle import default_raddle_state, parse_raddle_data
        import json
        task = type('T', (), {
            'task_type': 'raddle',
            'checker_data': json.dumps(PARIS_LADDER, ensure_ascii=False),
            'answer': '',
        })()
        parsed = parse_raddle_data(task)
        state = default_raddle_state(parsed['n_words'])
        state['solved_indices'] = list(range(parsed['n_words']))
        state['assist_tier'] = {'2': 1, '4': 2}
        payload = build_ladder_share_payload(
            parsed=parsed,
            state=state,
            number=46,
            elapsed_seconds=500,
            locale='ru',
        )
        states = [step['state'] for step in payload['steps']]
        self.assertIn('yellow', states)
        self.assertIn('red', states)
        self.assertGreater(payload['hint_count'], 0)
        self.assertIn('подсказ', payload['stats_line'])
        self.assertNotIn('🟩', dumps_payload(payload))

    def test_salad_payload_has_no_grid_or_words(self):
        words = ['МОСКВА', 'ПАРИЖ', 'РИМ']
        state = salad_default_state()
        state['solved_indices'] = [0, 1, 2]
        state['hint_counts'] = {1: 2}
        payload = build_salad_share_payload(
            words=words,
            state=state,
            number=23,
            date_value=date(2026, 9, 3),
            elapsed_seconds=377,
            locale='ru',
        )
        blob = dumps_payload(payload)
        self.assertEqual(payload['kind'], KIND_SALAD)
        self.assertEqual(payload['word_count'], 3)
        self.assertEqual(payload['hint_total'], 2)
        self.assertNotIn('МОСКВА', blob)
        self.assertNotIn('ПАРИЖ', blob)
        self.assertNotIn('words', payload)
        self.assertNotIn('grid', payload)

    def test_alphabetty_payload_does_not_include_secret(self):
        payload = build_alphabetty_share_payload(
            number=31,
            date_value=date(2026, 9, 3),
            elapsed_seconds=128,
            attempts=6,
            hints=1,
            locale='ru',
        )
        blob = dumps_payload(payload)
        self.assertEqual(payload['kind'], KIND_ALPHABETTY)
        self.assertEqual(payload['headline'], 'Алфавитка · 2:08')
        self.assertIn('попыт', payload['stats_line'])
        self.assertNotIn('secret', payload)
        self.assertNotIn('загад', blob.lower())

    def test_locales_ru_and_en(self):
        ru = build_alphabetty_share_payload(
            number=1, elapsed_seconds=70, attempts=1, hints=0, locale='ru',
        )
        en = build_alphabetty_share_payload(
            number=1, elapsed_seconds=70, attempts=1, hints=0, locale='en',
        )
        self.assertEqual(ru['locale'], 'ru')
        self.assertEqual(en['locale'], 'en')
        self.assertIn('Алфавитка', ru['title'])
        self.assertIn('Alphabetty', en['title'])
        self.assertIn('без подсказок', ru['stats_line'])
        self.assertIn('no hints', en['stats_line'])
        self.assertEqual(format_share_date(date(2026, 9, 3), 'en'), 'September 3, 2026')

    def test_long_strings_stay_in_payload(self):
        payload = build_alphabetty_share_payload(
            number='999999',
            date_value=date(2026, 9, 3),
            elapsed_seconds=12 * 3600 + 4,
            attempts=12345,
            hints=0,
            locale='en',
        )
        self.assertIn('Alphabetty #999999', payload['title'])
        self.assertIn('12:00:04', payload['headline'])
        self.assertIn('12345 tries', payload['stats_line'])

    def test_solved_in_headline_agrees_with_game_name(self):
        payload = build_ladder_share_payload(
            parsed=parse_raddle_data(type('T', (), {
                'task_type': 'raddle',
                'checker_data': __import__('json').dumps(PARIS_LADDER, ensure_ascii=False),
                'answer': '',
            })()),
            number=1,
            elapsed_seconds=272,
            locale='ru',
            headline_style=HEADLINE_SOLVED_IN,
        )
        self.assertEqual(payload['headline'], 'Лесенка пройдена за 4:32')

    def test_synthetic_preview_payloads_are_marked(self):
        items = synthetic_preview_payloads()
        self.assertGreaterEqual(len(items), 6)
        kinds = {item['kind'] for item in items}
        self.assertEqual(kinds, {KIND_LADDER, KIND_SALAD, KIND_ALPHABETTY})
        locales = {item['locale'] for item in items}
        self.assertEqual(locales, {'ru', 'en'})
        self.assertTrue(all(item.get('synthetic') for item in items))

    def test_same_inputs_are_deterministic(self):
        a = build_alphabetty_share_payload(number=7, elapsed_seconds=40, attempts=3, hints=0)
        b = build_alphabetty_share_payload(number=7, elapsed_seconds=40, attempts=3, hints=0)
        self.assertEqual(dumps_payload(a), dumps_payload(b))


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token-secret',
    TELEGRAM_ADMIN_CHAT_ID='12345',
)
class DailySharePreviewCommandTests(SimpleTestCase):
    def test_preview_uses_shared_renderer_and_sends_png_bytes(self):
        sent = []

        def fake_render(payload):
            return _png_bytes()

        def fake_send(png, *, filename, caption):
            sent.append((png, filename, caption))
            self.assertTrue(png.startswith(b'\x89PNG'))
            self.assertNotIn('/', filename)
            return {'message_id': len(sent)}

        from games.telegram.daily_share_preview import preview_daily_share_cards

        ok, message = preview_daily_share_cards(
            include_social=False,
            render_fn=fake_render,
            send_fn=fake_send,
            intro_fn=lambda text: True,
        )
        self.assertTrue(ok)
        self.assertGreaterEqual(len(sent), 6)
        self.assertNotIn('test-token-secret', message)
        self.assertTrue(all(item[0].startswith(b'\x89PNG') for item in sent))

    def test_telegram_failure_is_explicit_and_redacts_token(self):
        def fake_render(payload):
            return _png_bytes()

        def fake_send(png, *, filename, caption):
            raise RuntimeError('https://api.telegram.org/bottest-token-secret/sendPhoto boom')

        from games.telegram.daily_share_preview import preview_daily_share_cards

        ok, message = preview_daily_share_cards(
            include_social=False,
            render_fn=fake_render,
            send_fn=fake_send,
            intro_fn=lambda text: True,
        )
        self.assertFalse(ok)
        self.assertIn('Preview failed', message)
        self.assertNotIn('test-token-secret', message)
        self.assertIn('[redacted]', message)

    def test_missing_admin_config(self):
        from games.telegram.daily_share_preview import preview_daily_share_cards

        with override_settings(TELEGRAM_BOT_TOKEN='', TELEGRAM_ADMIN_CHAT_ID=''):
            ok, message = preview_daily_share_cards()
        self.assertFalse(ok)
        self.assertIn('not configured', message)

    def test_management_command_reports_success(self):
        with patch(
            'games.management.commands.preview_daily_share_cards.preview_daily_share_cards',
            return_value=(True, 'Sent 6 share cards'),
        ):
            call_command('preview_daily_share_cards', '--skip-social')

    def test_management_command_raises_on_failure(self):
        with patch(
            'games.management.commands.preview_daily_share_cards.preview_daily_share_cards',
            return_value=(False, 'Preview failed: nope'),
        ):
            with self.assertRaises(CommandError):
                call_command('preview_daily_share_cards')
