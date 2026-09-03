from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from games.share_result import (
    elapsed_label_from_attempts,
    elapsed_seconds_from_attempts,
    format_archive_result_line,
    format_elapsed,
    format_elapsed_compact,
    format_share_link,
    share_host_from_value,
)


class ShareResultTests(SimpleTestCase):
    def test_format_elapsed(self):
        self.assertEqual(format_elapsed(5564), '1ч 32м 44с')
        self.assertEqual(format_elapsed(226), '3м 46с')
        self.assertEqual(format_elapsed(9), '9с')

    def test_format_elapsed_compact(self):
        self.assertEqual(format_elapsed_compact(272), '4:32')
        self.assertEqual(format_elapsed_compact(8), '0:08')
        self.assertEqual(format_elapsed_compact(5564), '1:32:44')

    def test_format_share_link_strips_slash_and_port(self):
        self.assertEqual(
            format_share_link('interoves.com:443', '/ladder/46/'),
            '🔗 interoves.com/ladder/46',
        )
        self.assertEqual(
            format_share_link('interoves.com', 'alphabetty/23/'),
            '🔗 interoves.com/alphabetty/23',
        )
        self.assertEqual(share_host_from_value('127.0.0.1:8765'), '127.0.0.1')

    def test_elapsed_from_first_to_last_attempt(self):
        t0 = datetime(2026, 8, 23, 10, 0, 0, tzinfo=dt_timezone.utc)
        t1 = t0 + timedelta(seconds=226)
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=10)),
            SimpleNamespace(time=t1),
        ]
        self.assertEqual(elapsed_seconds_from_attempts(attempts), 226)
        self.assertEqual(elapsed_label_from_attempts(attempts), '3м 46с')
        self.assertEqual(elapsed_seconds_from_attempts([SimpleNamespace(time=t0)]), 0)
        self.assertEqual(elapsed_seconds_from_attempts([]), 0)

    def test_archive_result_line(self):
        self.assertEqual(format_archive_result_line('🟩🟥', '3м 46с'), '🟩🟥  ⏱️ 3м 46с')
        self.assertEqual(format_archive_result_line('🟩', None), '🟩')
        self.assertEqual(format_archive_result_line(None, '9с'), '⏱️ 9с')
