from dataclasses import replace
from datetime import date

from django.test import SimpleTestCase

from games.ops.social.ads import campaigns
from games.ops.social.ads.config import ConfigError
from games.ops.social.ads.money import micros_to_rubles
from games.ops.social.ads.schema.constants import GOAL_GAME_COMPLETE, METRIKA_COUNTER_ID


class FakeDirect:
    """Records writes; answers get calls from what was added."""

    def __init__(self, *, mutate_campaign=None):
        self.calls = []
        self.added = {}
        self.mutate_campaign = mutate_campaign
        self._next_id = 100

    def _ids(self, n):
        ids = list(range(self._next_id, self._next_id + n))
        self._next_id += n
        return ids

    def call(self, service, method, params=None):
        self.calls.append((service, method, params))
        if method == 'add':
            key = {'campaigns': 'Campaigns', 'adgroups': 'AdGroups', 'ads': 'Ads',
                   'keywords': 'Keywords', 'bidmodifiers': 'BidModifiers'}[service]
            items = params[key]
            ids = self._ids(len(items))
            for item_id, item in zip(ids, items):
                self.added.setdefault(service, []).append({'Id': item_id, **item})
            return {'AddResults': [{'Id': item_id} for item_id in ids]}
        if method == 'get':
            if service == 'campaigns':
                rows = [dict(row, Status='DRAFT', State='SUSPENDED') for row in self.added.get('campaigns', [])]
                if self.mutate_campaign:
                    for row in rows:
                        self.mutate_campaign(row)
                return {'Campaigns': rows}
            if service == 'adgroups':
                return {'AdGroups': self.added.get('adgroups', [])}
            if service == 'ads':
                return {'Ads': self.added.get('ads', [])}
            if service == 'keywords':
                return {'Keywords': [dict(k, State='ON') for k in self.added.get('keywords', [])]}
            if service == 'bidmodifiers':
                return {'BidModifiers': self.added.get('bidmodifiers', [])}
        return {}

    def writes(self):
        return [(s, m) for s, m, _ in self.calls if m != 'get']


class MemoryState:
    def __init__(self):
        self.state = {}

    def load(self):
        return self.state

    def save(self, state):
        self.state = state


class CampaignSpecTests(SimpleTestCase):
    def test_specs_validate_and_stay_within_the_november_cap(self):
        for slug in campaigns.CAMPAIGNS:
            campaigns.get_spec(slug)
        campaigns.assert_total_cap()
        self.assertEqual(sum(s.spend_limit_rub for s in campaigns.CAMPAIGNS.values()), 3000)

    def test_hosts_follow_the_region(self):
        ru = campaigns.get_spec('io-ru-nov26')
        intl = campaigns.get_spec('io-intl-nov26')
        self.assertEqual(ru.region_ids, (225,))
        self.assertTrue(ru.landing_url.startswith('https://ru.interoves.com/'))
        self.assertNotIn(225, intl.region_ids)
        self.assertTrue(intl.landing_url.startswith('https://interoves.com/'))

    def test_ad_copy_limits_are_enforced(self):
        with self.assertRaises(ConfigError):
            campaigns.AdCopy(title='x' * 57, title2='ok', text='ok').validate()
        spec = campaigns.get_spec('io-ru-nov26')
        with self.assertRaises(ConfigError):
            replace(spec, cpa_rub=spec.spend_limit_rub).validate()
        with self.assertRaises(ConfigError):
            replace(spec, end=date(2026, 11, 3)).validate()

    def test_payload_pays_per_completed_game_with_a_hard_period_cap(self):
        spec = campaigns.get_spec('io-ru-nov26')
        payload = campaigns.campaign_payload(spec)
        for placement in ('Search', 'Network'):
            strategy = payload['UnifiedCampaign']['BiddingStrategy'][placement]
            self.assertEqual(strategy['BiddingStrategyType'], 'PAY_FOR_CONVERSION')
            pfc = strategy['PayForConversion']
            self.assertEqual(pfc['GoalId'], GOAL_GAME_COMPLETE)
            self.assertEqual(micros_to_rubles(pfc['Cpa']), 70)
            self.assertEqual(micros_to_rubles(pfc['CustomPeriodBudget']['SpendLimit']), 2000)
            self.assertEqual(pfc['CustomPeriodBudget']['AutoContinue'], 'NO')
            self.assertNotIn('WeeklySpendLimit', pfc)
        self.assertEqual(payload['UnifiedCampaign']['CounterIds']['Items'], [METRIKA_COUNTER_ID])
        self.assertIn('utm_campaign=io-ru-nov26', payload['UnifiedCampaign']['TrackingParams'])
        modifiers = campaigns.bidmodifier_payloads(spec, 1)
        self.assertIn({'CampaignId': 1, 'TabletAdjustment': {'BidModifier': 0}}, modifiers)
        self.assertIn({'CampaignId': 1, 'MobileAdjustment': {'BidModifier': 50}}, modifiers)


class CampaignLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.spec = campaigns.get_spec('io-intl-nov26')
        self.audit_rows = []
        self.store = MemoryState()

    def audit(self, **row):
        self.audit_rows.append(row)

    def test_create_leaves_the_campaign_suspended_and_records_ids(self):
        direct = FakeDirect()
        created = campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        writes = direct.writes()
        self.assertEqual(writes[:2], [('campaigns', 'add'), ('campaigns', 'suspend')])
        self.assertNotIn(('campaigns', 'resume'), writes)
        self.assertNotIn(('ads', 'moderate'), writes)
        record = self.store.state['campaigns'][self.spec.slug]
        self.assertEqual(record['campaign_id'], created['campaign_id'])
        self.assertEqual(len(record['ad_ids']), len(self.spec.ads))
        self.assertFalse(record['launched'])
        with self.assertRaises(ConfigError):
            campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)

    def test_launch_verifies_then_moderates_and_resumes(self):
        direct = FakeDirect()
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        result = campaigns.launch(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertTrue(result['launched'], result)
        self.assertEqual(direct.writes()[-2:], [('ads', 'moderate'), ('campaigns', 'resume')])

    def test_launch_refuses_a_campaign_that_drifted_from_the_spec(self):
        def raise_budget(row):
            budget = row['UnifiedCampaign']['BiddingStrategy']['Network']['PayForConversion']['CustomPeriodBudget']
            budget['SpendLimit'] *= 2

        direct = FakeDirect(mutate_campaign=raise_budget)
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        with self.assertRaises(ConfigError) as error:
            campaigns.launch(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertIn('SpendLimit', str(error.exception))
        self.assertNotIn(('campaigns', 'resume'), direct.writes())
