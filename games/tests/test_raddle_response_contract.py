"""Контракт raddle: матрица сценариев согласована и полна."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from games.raddle_response_contract import (
    RADDLE_PR_CHECKLIST,
    RADDLE_RESPONSE_SCENARIOS,
    RADDLE_UI_PRINCIPLE,
)

JS_STATE = Path(settings.BASE_DIR) / 'static' / 'js' / 'raddle_state.js'
JS_STATE_TEST = Path(settings.BASE_DIR) / 'static' / 'js' / 'raddle_state.test.js'
UI_FLAGS = ('replace_html', 'advance_focus', 'mark_wrong', 'keep_input')


class RaddleResponseContractTests(SimpleTestCase):
    def test_scenarios_have_required_keys(self):
        required_response = {'status'}
        required_ui = {'replace_html', 'advance_focus', 'mark_wrong', 'keep_input'}
        ids = []
        for scenario in RADDLE_RESPONSE_SCENARIOS:
            self.assertIn('id', scenario)
            self.assertIn('description', scenario)
            self.assertIn('response', scenario)
            self.assertIn('ui', scenario)
            ids.append(scenario['id'])
            for key in required_response:
                self.assertIn(key, scenario['response'])
            for key in required_ui:
                self.assertIn(key, scenario['ui'])
        self.assertEqual(len(ids), len(set(ids)))

    def test_advance_implies_replace_html(self):
        for scenario in RADDLE_RESPONSE_SCENARIOS:
            ui = scenario['ui']
            if ui['advance_focus']:
                self.assertTrue(
                    ui['replace_html'],
                    msg='{}: advance_focus requires replace_html'.format(scenario['id']),
                )

    def test_mark_wrong_implies_keep_input(self):
        for scenario in RADDLE_RESPONSE_SCENARIOS:
            ui = scenario['ui']
            if ui['mark_wrong']:
                self.assertTrue(
                    ui['keep_input'],
                    msg='{}: mark_wrong requires keep_input'.format(scenario['id']),
                )

    def test_wrong_and_duplicate_unsolved_do_not_advance(self):
        for sid in ('wrong', 'duplicate_unsolved', 'stale_ui'):
            scenario = next(s for s in RADDLE_RESPONSE_SCENARIOS if s['id'] == sid)
            self.assertFalse(scenario['ui']['advance_focus'])
            if sid != 'stale_ui':
                self.assertFalse(scenario['ui']['replace_html'])
            else:
                self.assertTrue(scenario['ui']['replace_html'])

    def test_principle_and_checklist_documented(self):
        self.assertIn('raddle_correct', RADDLE_UI_PRINCIPLE)
        self.assertGreaterEqual(len(RADDLE_PR_CHECKLIST), 4)


class RaddleClientMatrixInSyncTests(SimpleTestCase):
    """Клиентская матрица (static/js/raddle_state.js) не расходится с серверной.

    Иначе новый флаг ответа добавляется на сервер, а UI молча продолжает
    обрабатывать его как «неверно» — ровно тот класс багов, от которого
    защищает RADDLE_UI_PRINCIPLE.
    """

    def setUp(self):
        self.js = JS_STATE.read_text(encoding='utf-8')
        self.js_test = JS_STATE_TEST.read_text(encoding='utf-8')

    def _js_scenario_ids(self):
        block = re.search(
            r'var RESPONSE_MATRIX = \[(.*?)\n  \];', self.js, re.S,
        )
        self.assertIsNotNone(block, 'RESPONSE_MATRIX not found in raddle_state.js')
        return re.findall(r"id:\s*'([a-z_]+)'", block.group(1))

    def test_client_matrix_has_the_same_scenarios(self):
        server_ids = [scenario['id'] for scenario in RADDLE_RESPONSE_SCENARIOS]
        self.assertEqual(sorted(self._js_scenario_ids()), sorted(server_ids))

    def test_client_matrix_uses_the_same_effect_flags(self):
        for flag in UI_FLAGS:
            self.assertIn(
                '{}:'.format(flag), self.js,
                msg='raddle_state.js must express the {} effect'.format(flag),
            )

    def test_every_scenario_is_asserted_in_the_js_test(self):
        for scenario in RADDLE_RESPONSE_SCENARIOS:
            self.assertIn(
                '{}:'.format(scenario['id']), self.js_test,
                msg='raddle_state.test.js does not cover scenario {}'.format(
                    scenario['id'],
                ),
            )

    def test_js_matrix_order_resolves_stale_ui_before_needs_sync(self):
        ids = self._js_scenario_ids()
        self.assertLess(
            ids.index('stale_ui'), ids.index('needs_sync'),
            msg='raddle_stale_ui must be matched before the plain needs_sync case',
        )
        self.assertLess(
            ids.index('duplicate_solved'), ids.index('duplicate_unsolved'),
            msg='raddle_duplicate_solved must be matched before plain duplicate',
        )
        self.assertLess(
            ids.index('correct'), ids.index('wrong'),
            msg='raddle_correct must be matched before the catch-all ok case',
        )
