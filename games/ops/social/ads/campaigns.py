"""Declarative Direct campaigns for Inter Oves, created and launched via API v501.

Each ``CampaignSpec`` is one UNIFIED campaign with its own landing host,
regions, hard budget and ads. Creation leaves the campaign suspended; launch
is a separate step that re-verifies the live campaign against the spec, so a
campaign that drifted from it never starts spending.

Bidding is PAY_FOR_CONVERSION on «Завершил игру»: Direct charges per
conversion, not per click, which makes junk clicks (the September tablet bot
traffic: 358 clicks at 21% CTR for 610 ₽) free. CustomPeriodBudget with
AutoContinue=NO caps the total spend of each campaign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from games.ops.social.ads.config import ConfigError, assert_minus_phrases_safe, extract_add_id, extract_add_ids
from games.ops.social.ads.money import micros_to_rubles, rubles_to_micros
from games.ops.social.ads.schema.constants import (
    CAMPAIGN_NEGATIVE_KEYWORDS,
    GOAL_GAME_COMPLETE,
    METRIKA_COUNTER_ID,
    MIN_CUSTOM_PERIOD_BUDGET_RUB,
    REGION_RUSSIA,
    TIMEZONE,
)

# Owner-approved Direct spend for November 2026 (Telegram posts are separate).
NOVEMBER_DIRECT_CAP_RUB = 3000

TITLE_MAX = 56
TITLE2_MAX = 30
TEXT_MAX = 81
WORD_MAX = 23


@dataclass(frozen=True)
class AdCopy:
    title: str
    title2: str
    text: str

    def validate(self) -> None:
        for label, value, limit in (
            ('title', self.title, TITLE_MAX),
            ('title2', self.title2, TITLE2_MAX),
            ('text', self.text, TEXT_MAX),
        ):
            if not value or len(value) > limit:
                raise ConfigError(f'Ad {label} must be 1..{limit} characters: {value!r} ({len(value)})')
            long_words = [w for w in value.split() if len(w) > WORD_MAX]
            if long_words:
                raise ConfigError(f'Ad {label} has words longer than {WORD_MAX}: {long_words}')


@dataclass(frozen=True)
class CampaignSpec:
    slug: str
    name: str
    landing_url: str
    region_ids: tuple[int, ...]
    spend_limit_rub: int
    cpa_rub: int
    start: date
    end: date
    keywords: tuple[str, ...]
    ads: tuple[AdCopy, ...]
    negative_keywords: tuple[str, ...] = tuple(CAMPAIGN_NEGATIVE_KEYWORDS) + ('квиз',)
    mobile_bid_modifier: int = 50  # percent of the base bid; 50 = −50%
    goal_id: int = GOAL_GAME_COMPLETE
    notes: tuple[str, ...] = field(default=())

    @property
    def adgroup_name(self) -> str:
        return f'{self.name} — группа'

    @property
    def tracking_params(self) -> str:
        return (
            f'utm_source=yandex&utm_medium=cpc&utm_campaign={self.slug}'
            '&utm_content={ad_id}&utm_term={keyword}'
        )

    def validate(self) -> None:
        if self.end <= self.start:
            raise ConfigError(f'{self.slug}: end date must be after start date')
        if self.spend_limit_rub < MIN_CUSTOM_PERIOD_BUDGET_RUB:
            raise ConfigError(f'{self.slug}: Direct needs at least {MIN_CUSTOM_PERIOD_BUDGET_RUB} ₽ per period')
        if not 0 < self.cpa_rub < self.spend_limit_rub:
            raise ConfigError(f'{self.slug}: CPA must be positive and below the spend limit')
        if not self.landing_url.startswith('https://') or '/start/' not in self.landing_url:
            raise ConfigError(f'{self.slug}: landing must be an https /start/ URL')
        if not self.ads:
            raise ConfigError(f'{self.slug}: needs at least one ad')
        for ad in self.ads:
            ad.validate()
        assert_minus_phrases_safe(list(self.negative_keywords))
        if not 0 <= self.mobile_bid_modifier <= 1300:
            raise ConfigError(f'{self.slug}: mobile bid modifier out of range')


SHARED_KEYWORDS = (
    'головоломки',
    'головоломки онлайн',
    'вордли',
    'вордли на русском',
    'угадай слово',
    'игры со словами',
    'словесные игры онлайн',
    'интеллектуальные игры',
    'игры на логику',
    'задачи на логику',
    'лингвистические задачи',
    'puzzle hunt',
)

SHARED_ADS = (
    AdCopy(
        title='Головоломки, где можно гуглить',
        title2='Ежедневные игры Inter Oves',
        text='Лесенки, Салатики, Алфавитки и Цензурки. Новые задания каждый день, бесплатно.',
    ),
    AdCopy(
        title='Не квиз. Здесь нужно думать',
        title2='Игры Inter Oves онлайн',
        text='Логика, слова и ресёрч вместо угадывания фактов. Новые задания каждый день.',
    ),
    AdCopy(
        title='Лесенки слов и другие головоломки',
        title2='Новые задания каждый день',
        text='Авторские словесные головоломки от клуба Inter Oves. Играйте прямо в браузере.',
    ),
)

# Countries with most of the Russian-speaking players and paying members
# outside Russia (Metrika, Sep–Oct 2026).
INTERNATIONAL_REGIONS = (
    118,  # Нидерланды
    96,  # Германия
    102,  # Великобритания
    168,  # Армения
    169,  # Грузия
    180,  # Сербия
    21610,  # Черногория
    20574,  # Кипр
    181,  # Израиль
    983,  # Турция
    159,  # Казахстан
    210,  # ОАЭ
    84,  # США
    10063,  # Ирландия
    123,  # Финляндия
    124,  # Франция
    126,  # Швейцария
    113,  # Австрия
    120,  # Польша
    125,  # Чехия
    204,  # Испания
    10074,  # Португалия
    205,  # Италия
    114,  # Бельгия
    127,  # Швеция
    119,  # Норвегия
    203,  # Дания
    115,  # Болгария
    117,  # Литва
)

CAMPAIGNS = {
    spec.slug: spec
    for spec in (
        CampaignSpec(
            slug='io-ru-nov26',
            name='Inter Oves — Россия → ru.interoves.com — ноябрь 2026',
            landing_url='https://ru.interoves.com/start/',
            region_ids=(REGION_RUSSIA,),
            spend_limit_rub=2000,
            cpa_rub=70,
            start=date(2026, 11, 3),
            end=date(2026, 11, 16),
            keywords=SHARED_KEYWORDS,
            ads=SHARED_ADS,
            notes=('ru.interoves.com opens in Russia without VPN',),
        ),
        CampaignSpec(
            slug='io-intl-nov26',
            name='Inter Oves — заграница → interoves.com — ноябрь 2026',
            landing_url='https://interoves.com/start/',
            region_ids=INTERNATIONAL_REGIONS,
            spend_limit_rub=1000,
            cpa_rub=80,
            start=date(2026, 11, 3),
            end=date(2026, 11, 16),
            keywords=SHARED_KEYWORDS,
            ads=SHARED_ADS + (
                AdCopy(
                    title='Головоломки на русском языке',
                    title2='Где бы вы ни были',
                    text='Ежедневные словесные игры и задачи на логику от клуба Inter Oves. Бесплатно.',
                ),
            ),
            notes=('test: Yandex inventory abroad is thin',),
        ),
    )
}


def get_spec(slug: str) -> CampaignSpec:
    try:
        spec = CAMPAIGNS[slug]
    except KeyError:
        raise ConfigError(f'Unknown campaign spec {slug!r}; known: {sorted(CAMPAIGNS)}') from None
    spec.validate()
    return spec


def assert_total_cap() -> None:
    total = sum(spec.spend_limit_rub for spec in CAMPAIGNS.values())
    if total > NOVEMBER_DIRECT_CAP_RUB:
        raise ConfigError(f'Direct campaigns total {total} ₽ exceeds the approved {NOVEMBER_DIRECT_CAP_RUB} ₽')


# --- Payloads -------------------------------------------------------------


def _strategy(spec: CampaignSpec) -> dict:
    return {
        'Cpa': rubles_to_micros(spec.cpa_rub),
        'GoalId': spec.goal_id,
        'CustomPeriodBudget': {
            'SpendLimit': rubles_to_micros(spec.spend_limit_rub),
            'StartDate': spec.start.isoformat(),
            'EndDate': spec.end.isoformat(),
            'AutoContinue': 'NO',
        },
    }


def campaign_payload(spec: CampaignSpec) -> dict:
    return {
        'Name': spec.name,
        'StartDate': spec.start.isoformat(),
        'EndDate': spec.end.isoformat(),
        'TimeZone': TIMEZONE,
        'NegativeKeywords': {'Items': list(spec.negative_keywords)},
        'UnifiedCampaign': {
            'BiddingStrategy': {
                'Search': {
                    'BiddingStrategyType': 'PAY_FOR_CONVERSION',
                    'PayForConversion': _strategy(spec),
                    'PlacementTypes': {
                        'SearchResults': 'YES',
                        'ProductGallery': 'NO',
                        'DynamicPlaces': 'NO',
                        'Maps': 'NO',
                        'SearchOrganizationList': 'NO',
                    },
                },
                'Network': {
                    'BiddingStrategyType': 'PAY_FOR_CONVERSION',
                    'PayForConversion': _strategy(spec),
                },
            },
            'Settings': [
                {'Option': 'ADD_METRICA_TAG', 'Value': 'YES'},
                {'Option': 'ENABLE_SITE_MONITORING', 'Value': 'YES'},
                {'Option': 'ENABLE_AREA_OF_INTEREST_TARGETING', 'Value': 'NO'},
                {'Option': 'ALTERNATIVE_TEXTS_ENABLED', 'Value': 'NO'},
            ],
            'CounterIds': {'Items': [METRIKA_COUNTER_ID]},
            'TrackingParams': spec.tracking_params,
            'AttributionModel': 'LYDC',
        },
    }


def adgroup_payload(spec: CampaignSpec, campaign_id: int) -> dict:
    return {
        'Name': spec.adgroup_name,
        'CampaignId': campaign_id,
        'RegionIds': list(spec.region_ids),
        'UnifiedAdGroup': {'OfferRetargeting': 'NO'},
    }


def ad_payloads(spec: CampaignSpec, adgroup_id: int) -> list[dict]:
    return [
        {
            'AdGroupId': adgroup_id,
            'TextAd': {
                'Title': ad.title,
                'Title2': ad.title2,
                'Text': ad.text,
                'Href': spec.landing_url,
                'Mobile': 'NO',
                'DisplayUrlPath': 'start',
            },
        }
        for ad in spec.ads
    ]


def keyword_payloads(spec: CampaignSpec, adgroup_id: int) -> list[dict]:
    return [{'Keyword': phrase, 'AdGroupId': adgroup_id} for phrase in spec.keywords]


def bidmodifier_payloads(spec: CampaignSpec, campaign_id: int) -> list[dict]:
    return [
        {'CampaignId': campaign_id, 'MobileAdjustment': {'BidModifier': spec.mobile_bid_modifier}},
        {'CampaignId': campaign_id, 'TabletAdjustment': {'BidModifier': 0}},
    ]


def plan(spec: CampaignSpec) -> dict:
    """Every write ``create`` would send, with zero ids; no API calls."""
    assert_total_cap()
    return {
        'spec': spec.slug,
        'write_requests': 0,
        'campaigns.add': {'Campaigns': [campaign_payload(spec)]},
        'adgroups.add': {'AdGroups': [adgroup_payload(spec, 0)]},
        'ads.add': {'Ads': ad_payloads(spec, 0)},
        'keywords.add': {'Keywords': keyword_payloads(spec, 0)},
        'bidmodifiers.add': {'BidModifiers': bidmodifier_payloads(spec, 0)},
    }


# --- Writes ---------------------------------------------------------------


def create(ads, spec: CampaignSpec, *, audit, state_store) -> dict:
    """Create the campaign tree suspended. Never starts spending."""
    assert_total_cap()
    state = state_store.load()
    existing = (state.get('campaigns') or {}).get(spec.slug)
    if existing and existing.get('campaign_id'):
        raise ConfigError(f'{spec.slug} already exists as campaign {existing["campaign_id"]}')

    campaign_id = extract_add_id(ads.call('campaigns', 'add', {'Campaigns': [campaign_payload(spec)]}))
    record = {'campaign_id': campaign_id, 'created_on': date.today().isoformat(), 'launched': False}
    state.setdefault('campaigns', {})[spec.slug] = record
    state_store.save(state)
    audit(action='create', object_type='campaign', object_id=campaign_id,
          new_value={'spec': spec.slug, 'name': spec.name}, reason='spec create', api_status='created')
    ads.call('campaigns', 'suspend', {'SelectionCriteria': {'Ids': [campaign_id]}})
    audit(action='suspend', object_type='campaign', object_id=campaign_id,
          reason='suspended until verify + launch', api_status='suspended')

    adgroup_id = extract_add_id(ads.call('adgroups', 'add', {'AdGroups': [adgroup_payload(spec, campaign_id)]}))
    ad_ids = extract_add_ids(ads.call('ads', 'add', {'Ads': ad_payloads(spec, adgroup_id)}))
    keyword_ids = extract_add_ids(ads.call('keywords', 'add', {'Keywords': keyword_payloads(spec, adgroup_id)}))
    bidmodifier_ids = extract_add_ids(
        ads.call('bidmodifiers', 'add', {'BidModifiers': bidmodifier_payloads(spec, campaign_id)})
    )
    record.update({
        'adgroup_ids': [adgroup_id],
        'ad_ids': ad_ids,
        'keyword_ids': keyword_ids,
        'bidmodifier_ids': bidmodifier_ids,
    })
    state_store.save(state)
    audit(action='create', object_type='campaign_tree', object_id=campaign_id,
          new_value={k: v for k, v in record.items() if k.endswith('_ids')},
          reason='spec create', api_status='created')
    return {'spec': spec.slug, **record}


def verify(ads, spec: CampaignSpec, campaign_id: int) -> dict:
    """Compare the live campaign with the spec; ``ok`` gates launch."""
    from games.ops.social.ads import ops

    failures = []
    camp = (ops.campaigns_get(ads, [campaign_id]) or [None])[0]
    if not camp:
        return {'ok': False, 'failures': ['campaign not found']}
    unified = camp.get('UnifiedCampaign') or {}
    bidding = unified.get('BiddingStrategy') or {}
    for placement in ('Search', 'Network'):
        strategy = bidding.get(placement) or {}
        if strategy.get('BiddingStrategyType') != 'PAY_FOR_CONVERSION':
            failures.append(f'{placement} strategy is {strategy.get("BiddingStrategyType")}')
            continue
        pfc = strategy.get('PayForConversion') or {}
        budget = pfc.get('CustomPeriodBudget') or {}
        limit = micros_to_rubles(budget.get('SpendLimit') or 0)
        if not 0 < limit <= spec.spend_limit_rub:
            failures.append(f'{placement} SpendLimit {limit} ₽ exceeds {spec.spend_limit_rub} ₽')
        if str(budget.get('AutoContinue') or '').upper() != 'NO':
            failures.append(f'{placement} AutoContinue is {budget.get("AutoContinue")}')
        if micros_to_rubles(pfc.get('Cpa') or 0) > spec.cpa_rub:
            failures.append(f'{placement} CPA {micros_to_rubles(pfc.get("Cpa") or 0)} ₽ exceeds {spec.cpa_rub} ₽')
        if pfc.get('GoalId') != spec.goal_id:
            failures.append(f'{placement} goal {pfc.get("GoalId")} != {spec.goal_id}')
    if METRIKA_COUNTER_ID not in ((unified.get('CounterIds') or {}).get('Items') or []):
        failures.append('Metrika counter missing')

    for group in ops.adgroups_get(ads, campaign_ids=[campaign_id]):
        if sorted(group.get('RegionIds') or []) != sorted(spec.region_ids):
            failures.append(f'AdGroup {group.get("Id")} regions differ from spec')
    live_ads = ops.ads_get(ads, campaign_ids=[campaign_id])
    if not live_ads:
        failures.append('no ads')
    for ad in live_ads:
        href = (ad.get('TextAd') or {}).get('Href') or ''
        if href != spec.landing_url:
            failures.append(f'Ad {ad.get("Id")} href {href!r} != {spec.landing_url!r}')
    for keyword in ops.keywords_get(ads, campaign_ids=[campaign_id]):
        if (keyword.get('Keyword') or '').startswith('---autotargeting') and keyword.get('State') == 'ON':
            failures.append(f'autotargeting {keyword.get("Id")} is ON')

    modifiers = ops.bidmodifiers_get(ads, campaign_ids=[campaign_id])
    tablet_off = any((m.get('TabletAdjustment') or {}).get('BidModifier') == 0 for m in modifiers)
    if not tablet_off:
        failures.append('tablets are not switched off')
    return {'ok': not failures, 'failures': failures, 'status': camp.get('Status'), 'state': camp.get('State')}


def launch(ads, spec: CampaignSpec, *, audit, state_store) -> dict:
    """Verify, send ads to moderation and resume the campaign."""
    state = state_store.load()
    record = (state.get('campaigns') or {}).get(spec.slug)
    if not record or not record.get('campaign_id'):
        raise ConfigError(f'{spec.slug} has not been created')
    campaign_id = record['campaign_id']
    check = verify(ads, spec, campaign_id)
    if not check['ok']:
        raise ConfigError(f'{spec.slug} failed verification: {check["failures"]}')
    ads.call('ads', 'moderate', {'SelectionCriteria': {'Ids': record['ad_ids']}})
    audit(action='moderate', object_type='ads', object_id=record['ad_ids'],
          reason='spec launch', api_status='submitted')
    ads.call('campaigns', 'resume', {'SelectionCriteria': {'Ids': [campaign_id]}})
    audit(action='resume', object_type='campaign', object_id=campaign_id,
          reason='spec launch after verify', api_status='resumed')
    record['launched'] = True
    record['launched_on'] = date.today().isoformat()
    state_store.save(state)
    return {'spec': spec.slug, 'campaign_id': campaign_id, 'launched': True, 'verify': check}
