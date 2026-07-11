"""Контракт raddle: матрица сценариев согласована и полна."""
from django.test import SimpleTestCase

from games.raddle_response_contract import (
    RADDLE_PR_CHECKLIST,
    RADDLE_RESPONSE_SCENARIOS,
    RADDLE_UI_PRINCIPLE,
)


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
        for sid in ('wrong', 'duplicate_unsolved'):
            scenario = next(s for s in RADDLE_RESPONSE_SCENARIOS if s['id'] == sid)
            self.assertFalse(scenario['ui']['advance_focus'])
            self.assertFalse(scenario['ui']['replace_html'])

    def test_principle_and_checklist_documented(self):
        self.assertIn('raddle_correct', RADDLE_UI_PRINCIPLE)
        self.assertGreaterEqual(len(RADDLE_PR_CHECKLIST), 4)
