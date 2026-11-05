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
                   'keywords': 'Keywords', 'bidmodifiers': 'BidModifiers',
                   'retargetinglists': 'RetargetingLists', 'audiencetargets': 'AudienceTargets'}[service]
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
        self.assertEqual(sum(s.spend_limit_rub for s in campaigns.CAMPAIGNS.values()), 5000)

    def test_hosts_follow_the_region(self):
        ru = campaigns.get_spec('io-ru-nov26')
        intl = campaigns.get_spec('io-intl-nov26')
        self.assertEqual(ru.region_ids, (225,))
        self.assertTrue(ru.landing_url.startswith('https://ru.interoves.com/'))
        self.assertNotIn(225, intl.region_ids)
        self.assertTrue(intl.landing_url.startswith('https://interoves.com/'))

    def test_ad_copy_limits_are_enforced(self):
        with self.assertRaises(ConfigError):
            campaigns.AdCopy(title='x' * 57, text='ok').validate()
        spec = campaigns.get_spec('io-ru-nov26')
        with self.assertRaises(ConfigError):
            replace(spec, cpa_rub=spec.spend_limit_rub).validate()
        with self.assertRaises(ConfigError):
            replace(spec, end=date(2026, 11, 3)).validate()

    def test_payload_pays_per_completed_game_with_a_hard_period_cap(self):
        spec = campaigns.get_spec('io-ru-nov26')
        payload = campaigns.campaign_payload(spec)
        bidding = payload['UnifiedCampaign']['BiddingStrategy']
        self.assertEqual(bidding['Network'], {'BiddingStrategyType': 'NETWORK_DEFAULT'})
        for placement in ('Search',):
            strategy = bidding[placement]
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
        self.assertIn({'CampaignId': 1, 'MobileAdjustment': {'BidModifier': 100}}, modifiers)


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
            budget = row['UnifiedCampaign']['BiddingStrategy']['Search']['PayForConversion']['CustomPeriodBudget']
            budget['SpendLimit'] *= 2

        direct = FakeDirect(mutate_campaign=raise_budget)
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        with self.assertRaises(ConfigError) as error:
            campaigns.launch(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertIn('SpendLimit', str(error.exception))
        self.assertNotIn(('campaigns', 'resume'), direct.writes())

    def test_autotargeting_is_narrowed_and_verified(self):
        direct = FakeDirect()
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        campaign_id = self.store.state['campaigns'][self.spec.slug]['campaign_id']
        wide = {'Categories': {'Exact': 'YES', 'Narrow': 'YES', 'Alternative': 'YES', 'Accessory': 'YES',
                               'Broader': 'YES'},
                'BrandOptions': {'WithoutBrands': 'YES', 'WithAdvertiserBrand': 'YES', 'WithCompetitorsBrand': 'YES'}}
        direct.added['keywords'].append({'Id': 9, 'Keyword': '---autotargeting', 'AutotargetingSettings': wide})
        self.assertIn('Alternative', str(campaigns.verify(direct, self.spec, campaign_id)['failures']))
        self.assertEqual(campaigns.narrow_autotargeting(direct, campaign_id, audit=self.audit), [9])
        update = [p for s, m, p in direct.calls if (s, m) == ('keywords', 'update')][-1]
        self.assertEqual(update['Keywords'][0]['AutotargetingSettings'], campaigns.AUTOTARGETING_SETTINGS)
        direct.added['keywords'][-1]['AutotargetingSettings'] = campaigns.AUTOTARGETING_SETTINGS
        self.assertTrue(campaigns.verify(direct, self.spec, campaign_id)['ok'])


class GameGroupTests(SimpleTestCase):
    def setUp(self):
        self.spec = campaigns.get_spec('io-ru-nov26')
        self.store = MemoryState()
        self.audit_rows = []

    def audit(self, **row):
        self.audit_rows.append(row)

    def test_every_daily_game_has_a_group_landing_on_the_campaign_host(self):
        games = {g.game for g in self.spec.game_groups}
        self.assertEqual(games, {'censorly', 'ladder', 'salad', 'alphabetty'})
        for group in self.spec.game_groups:
            self.assertTrue(self.spec.group_landing(group).startswith('https://ru.interoves.com/'))
            self.assertEqual(len(group.ads), 3)
            for name in group.images:
                self.assertTrue(campaigns.image_path(name).is_file(), name)
        intl = campaigns.get_spec('io-intl-nov26')
        self.assertEqual(intl.group_landing(intl.game_groups[0]), 'https://interoves.com/censorly/first/')

    def test_add_groups_uploads_images_once_and_moderates_only_launched_campaigns(self):
        direct = FakeDirect()
        uploads = []
        original = direct.call

        def call(service, method, params=None):
            if (service, method) == ('adimages', 'add'):
                uploads.append(params['AdImages'][0]['Name'])
                return {'AddResults': [{'AdImageHash': 'hash-' + params['AdImages'][0]['Name']}]}
            return original(service, method, params)

        direct.call = call
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        result = campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertEqual(sorted(result['added']), ['alphabetty', 'censorly', 'ladder', 'return', 'salad'])
        self.assertEqual(len(uploads), 12)  # audience groups reuse the game images
        # The return group targets an audience, not phrases.
        rules = direct.added['retargetinglists'][0]['Rules']
        self.assertEqual([r['Operator'] for r in rules], ['ALL', 'NONE'])
        return_group = self.store.state['campaigns'][self.spec.slug]['game_groups']['return']
        self.assertEqual(return_group['keyword_ids'], [])
        self.assertEqual(direct.added['audiencetargets'][0]['AdGroupId'], return_group['adgroup_id'])
        self.assertNotIn(('ads', 'moderate'), direct.writes())
        censorly_ads = [a for a in direct.added['ads'] if a['TextAd'].get('Href', '').endswith('/censorly/first/')]
        self.assertEqual(len(censorly_ads), 3)
        self.assertEqual(
            [a['TextAd']['AdImageHash'] for a in censorly_ads],
            ['hash-interoves-censorly-b-1x1', 'hash-interoves-censorly-a-1x1', 'hash-interoves-censorly-b-16x9'],
        )
        self.assertTrue(campaigns.verify(direct, self.spec, result['campaign_id'])['ok'])

        # Running again adds nothing and uploads nothing.
        again = campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertEqual(again['added'], [])
        self.assertEqual(len(uploads), 12)

    def test_add_groups_sends_new_ads_of_a_launched_campaign_to_moderation(self):
        direct = FakeDirect()
        original = direct.call
        direct.call = lambda s, m, p=None: (
            {'AddResults': [{'AdImageHash': 'h'}]} if (s, m) == ('adimages', 'add') else original(s, m, p)
        )
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        self.store.state['campaigns'][self.spec.slug]['launched'] = True
        campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        moderated = [p for s, m, p in direct.calls if (s, m) == ('ads', 'moderate')]
        # The return group's ads wait until its autotargeting is checked.
        self.assertEqual(sum(len(p['SelectionCriteria']['Ids']) for p in moderated), 12)

    def test_ad_text_rejects_characters_direct_does_not_accept(self):
        with self.assertRaises(ConfigError):
            campaigns.AdCopy(title='Сетка 4×4', text='ok').validate()
        campaigns.AdCopy(title='Цензурка — игра дня, «Слово»!', text='4 на 4, 100%.').validate()

    def test_failed_group_is_rolled_back_and_autotargeting_still_narrowed(self):
        direct = FakeDirect()
        original = direct.call

        def call(service, method, params=None):
            if (service, method) == ('adimages', 'add'):
                return {'AddResults': [{'AdImageHash': 'h'}]}
            text_ad = (params or {}).get('Ads', [{}])[0].get('TextAd', {})
            if (service, method) == ('ads', 'add') and text_ad.get('AdImageHash') and text_ad['Href'].endswith('/ladder/first/'):
                return {'AddResults': [{'Errors': [{'Code': 5002}]}]}
            return original(service, method, params)

        direct.call = call
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        with self.assertRaises(ConfigError):
            campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        deletes = [p for s, m, p in direct.calls if (s, m) == ('adgroups', 'delete')]
        self.assertEqual(len(deletes), 1)
        self.assertIn('censorly', self.store.state['campaigns'][self.spec.slug]['game_groups'])
        self.assertNotIn('ladder', self.store.state['campaigns'][self.spec.slug]['game_groups'])


    def test_sync_updates_strategy_and_only_changed_game_ads(self):
        direct = FakeDirect()
        original = direct.call
        direct.call = lambda s, m, p=None: (
            {'AddResults': [{'AdImageHash': 'h'}]} if (s, m) == ('adimages', 'add') else original(s, m, p)
        )
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        ladder_ad = self.store.state['campaigns'][self.spec.slug]['game_groups']['ladder']['ad_ids'][1]
        for ad in direct.added['ads']:
            if ad['Id'] == ladder_ad:
                ad['TextAd']['Title'] = 'old title'
        result = campaigns.sync(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertEqual(result['updated_ads'], [ladder_ad])
        # Strategy and minus-phrases already match: the campaign is left alone.
        self.assertNotIn(('campaigns', 'update'), direct.writes())

    def test_sync_sends_only_the_campaign_fields_that_changed(self):
        direct = FakeDirect()
        original = direct.call
        direct.call = lambda s, m, p=None: (
            {'AddResults': [{'AdImageHash': 'h'}]} if (s, m) == ('adimages', 'add') else original(s, m, p)
        )
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        spec = replace(self.spec, negative_keywords=self.spec.negative_keywords + ('шашки',))
        self.assertIn("minus-phrases missing: ['шашки']",
                      campaigns.verify(direct, spec, direct.added['campaigns'][0]['Id'])['failures'])
        campaigns.sync(direct, spec, audit=self.audit, state_store=self.store)
        updates = [p for s, m, p in direct.calls if (s, m) == ('campaigns', 'update')]
        self.assertEqual(len(updates), 1)
        sent = updates[0]['Campaigns'][0]
        self.assertNotIn('UnifiedCampaign', sent)
        self.assertIn('шашки', sent['NegativeKeywords']['Items'])

        campaigns.sync(direct, replace(self.spec, cpa_rub=self.spec.cpa_rub - 10),
                       audit=self.audit, state_store=self.store)
        sent = [p for s, m, p in direct.calls if (s, m) == ('campaigns', 'update')][-1]['Campaigns'][0]
        self.assertIn('UnifiedCampaign', sent)
        self.assertNotIn('NegativeKeywords', sent)

    def test_sync_moves_phrases_landing_and_phone_bids_to_the_spec(self):
        direct = FakeDirect()
        original = direct.call
        direct.call = lambda s, m, p=None: (
            {'AddResults': [{'AdImageHash': 'h'}]} if (s, m) == ('adimages', 'add') else original(s, m, p)
        )
        old = replace(self.spec, keywords=self.spec.keywords + ('вордли',), generic_path='/start/',
                      mobile_bid_modifier=50)
        campaigns.create(direct, old, audit=self.audit, state_store=self.store)
        campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        for modifier in direct.added['bidmodifiers']:
            modifier.setdefault('Level', 'CAMPAIGN')
        result = campaigns.sync(direct, self.spec, audit=self.audit, state_store=self.store)

        record = self.store.state['campaigns'][self.spec.slug]
        generic_vordli = [k['Id'] for k in direct.added['keywords']
                          if k['Keyword'] == 'вордли' and k['AdGroupId'] == record['adgroup_ids'][0]]
        self.assertEqual(result['keywords'], {'deleted': generic_vordli, 'added': []})
        updated = [p for s, m, p in direct.calls if (s, m) == ('ads', 'update')][-1]['Ads']
        generic = [u for u in updated if u['Id'] in record['ad_ids']]
        self.assertEqual({u['TextAd']['Href'] for u in generic}, {'https://ru.interoves.com/salad/first/'})
        self.assertEqual({u['TextAd']['DisplayUrlPath'] for u in generic}, {'salad'})
        sets = [p for s, m, p in direct.calls if (s, m) == ('bidmodifiers', 'set')]
        self.assertEqual(sets[0]['BidModifiers'][0]['BidModifier'], 100)

    def test_sync_suspends_the_ads_of_an_inactive_group(self):
        direct = FakeDirect()
        original = direct.call
        direct.call = lambda s, m, p=None: (
            {'AddResults': [{'AdImageHash': 'h'}]} if (s, m) == ('adimages', 'add') else original(s, m, p)
        )
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        for ad in direct.added['ads']:
            ad['State'] = 'ON'
        campaign_id = direct.added['campaigns'][0]['Id']
        failures = campaigns.verify(direct, self.spec, campaign_id)['failures']
        self.assertEqual(sum('inactive group is still on' in f for f in failures), 3)

        result = campaigns.sync(direct, self.spec, audit=self.audit, state_store=self.store)
        alphabetty = self.store.state['campaigns'][self.spec.slug]['game_groups']['alphabetty']['ad_ids']
        self.assertEqual(result['paused_ads'], alphabetty)
        self.assertEqual(result['resumed_ads'], [])
        suspended = [p for s, m, p in direct.calls if (s, m) == ('ads', 'suspend')]
        self.assertEqual(suspended[0]['SelectionCriteria']['Ids'], alphabetty)

    def test_audience_segment_rules_carry_no_lifespan(self):
        audience = campaigns.Audience(name='x', rules=(('ALL', 123, None), ('NONE', 456, 2)))
        self.assertEqual(
            [r['Arguments'][0] for r in audience.payload()['Rules']],
            [{'ExternalId': 123}, {'ExternalId': 456, 'MembershipLifeSpan': 2}],
        )

    def test_a_group_targets_either_phrases_or_an_audience(self):
        with self.assertRaises(ConfigError):
            replace(campaigns.RETURN_GROUP, keywords=('головоломки',)).validate()
        with self.assertRaises(ConfigError):
            replace(campaigns.RETURN_GROUP, audience=None).validate()

    def test_word_forms_folded_by_direct_count_as_present(self):
        spec = replace(self.spec, negative_keywords=('кроссворды', 'шашки'))
        live = {'NegativeKeywords': {'Items': ['кроссворд']}}
        self.assertEqual(campaigns._missing_negatives(spec, live), {'шашки'})


    def test_sync_adds_a_missing_ad_and_replaces_images(self):
        direct = FakeDirect()
        original = direct.call
        direct.call = lambda s, m, p=None: (
            {'AddResults': [{'AdImageHash': 'hash-' + p['AdImages'][0]['Name']}]}
            if (s, m) == ('adimages', 'add') else original(s, m, p)
        )
        campaigns.create(direct, self.spec, audit=self.audit, state_store=self.store)
        campaigns.add_game_groups(direct, self.spec, audit=self.audit, state_store=self.store)
        record = self.store.state['campaigns'][self.spec.slug]
        record['launched'] = True
        salad = record['game_groups']['salad']
        dropped = salad['ad_ids'].pop()  # as if the group had been made with two ads
        direct.added['ads'] = [a for a in direct.added['ads'] if a['Id'] != dropped]
        first = salad['ad_ids'][0]
        for ad in direct.added['ads']:
            if ad['Id'] == first:
                ad['TextAd']['AdImageHash'] = 'old-hash'
        result = campaigns.sync(direct, self.spec, audit=self.audit, state_store=self.store)
        self.assertEqual(result['updated_ads'], [first])
        self.assertEqual(len(result['added_ads']), 1)
        self.assertEqual(len(self.store.state['campaigns'][self.spec.slug]['game_groups']['salad']['ad_ids']), 3)
        moderated = [p for s, m, p in direct.calls if (s, m) == ('ads', 'moderate')]
        self.assertEqual(moderated[-1]['SelectionCriteria']['Ids'], result['added_ads'])
