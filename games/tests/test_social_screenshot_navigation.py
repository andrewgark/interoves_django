import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from playwright.sync_api import Error, TimeoutError

from games.ops.telegram.images.ladder import (
    _goto_screenshot_page,
    _navigate_screenshot_page,
    screenshot_ladder_last_png,
)


class SocialScreenshotNavigationTests(SimpleTestCase):
    def test_timeout_logs_pending_resources_and_failures_without_query_secrets(self):
        page = Mock()
        listeners = {}
        page.on.side_effect = lambda event, callback: listeners.update({event: callback})
        document = Mock(url='https://interoves.com/ladder/114/', resource_type='document')
        script = Mock(url='https://cdn.example/game.js?token=secret#private', resource_type='script')
        font = Mock(url='https://cdn.example/font.woff2', resource_type='font',
                    failure='net::ERR_CONNECTION_RESET')

        def navigate(*args, **kwargs):
            for request in (document, script, font):
                listeners['request'](request)
            listeners['response'](Mock(request=document, status=200))
            listeners['requestfinished'](document)
            listeners['response'](Mock(request=script, status=503))
            listeners['requestfailed'](font)
            raise TimeoutError('timeout')

        page.goto.side_effect = navigate
        with self.assertLogs('application', level='WARNING') as logs:
            with self.assertRaises(TimeoutError):
                _goto_screenshot_page(page, 'https://user:password@interoves.com/ladder/114/?secret=1',
                                      attempt=1)

        message = logs.records[0].getMessage()
        data = json.loads(message.split(' ', 1)[1])
        self.assertEqual(data['url'], 'https://interoves.com/ladder/114/')
        self.assertNotIn('secret', message)
        self.assertNotIn('password', message)
        self.assertNotIn('private', message)
        self.assertEqual(data['pending_count'], 1)
        self.assertEqual(data['pending'][0]['url'], 'https://cdn.example/game.js')
        self.assertEqual(data['pending'][0]['status'], 503)
        self.assertGreaterEqual(data['pending'][0]['elapsed_ms'], 0)
        self.assertEqual(data['recent_failures'][1]['failure'], 'net::ERR_CONNECTION_RESET')
        self.assertEqual(page.remove_listener.call_count, 4)

    def test_success_cleans_up_listeners_without_warning(self):
        page = Mock()
        page.goto.return_value = Mock(ok=True)
        with self.assertNoLogs('application', level='WARNING'):
            _goto_screenshot_page(page, 'https://interoves.com/ladder/114/', attempt=1)
        self.assertEqual(page.remove_listener.call_count, 4)

    def test_transient_timeout_retries_and_waits_for_initialized_document(self):
        page = Mock()
        page.goto.side_effect = [TimeoutError('slow resource'), Mock(ok=True)]

        _navigate_screenshot_page(page, 'https://interoves.com/ladder/114/')

        self.assertEqual(page.goto.call_count, 2)
        for call in page.goto.call_args_list:
            self.assertEqual(call.kwargs['wait_until'], 'domcontentloaded')
            self.assertEqual(call.kwargs['timeout'], 30000)

    def test_repeated_timeout_propagates_without_unbounded_retry(self):
        page = Mock()
        page.goto.side_effect = TimeoutError('unreachable')

        with self.assertRaises(TimeoutError):
            _navigate_screenshot_page(page, 'https://interoves.com/ladder/114/')

        self.assertEqual(page.goto.call_count, 2)

    def test_other_browser_errors_are_not_retried(self):
        page = Mock()
        page.goto.side_effect = Error('browser closed')

        with self.assertRaises(Error):
            _navigate_screenshot_page(page, 'https://interoves.com/ladder/114/')

        page.goto.assert_called_once()

    def test_http_error_cannot_become_a_screenshot(self):
        page = Mock()
        page.goto.return_value = Mock(ok=False, status=503)

        with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
            _navigate_screenshot_page(page, 'https://interoves.com/ladder/114/')

        page.goto.assert_called_once()

    @patch('games.ops.telegram.images.ladder.screenshot_page_element_png')
    def test_ladder_capture_requires_the_game_board(self, screenshot):
        screenshot_ladder_last_png(url='https://interoves.com/ladder/114/')

        self.assertEqual(screenshot.call_args.kwargs['wait_for_selector'], '.new-raddle-task')
        self.assertEqual(screenshot.call_args.kwargs['require_selector'], '.new-raddle-task')
