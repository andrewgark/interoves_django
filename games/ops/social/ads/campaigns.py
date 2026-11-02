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

import base64
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
NOVEMBER_DIRECT_CAP_RUB = 5000

TITLE_MAX = 56
TEXT_MAX = 81
WORD_MAX = 23
MAX_ADS_PER_GROUP = 3
# Direct accepts letters, digits and a short list of punctuation in ad texts
# ("×" in «4×4» was rejected with code 5002).
AD_TEXT_ALLOWED = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 .,!?;:\-—«»\"'()/%№+&#]+$")
IMAGES_DIR = Path(__file__).resolve().parents[4] / 'artifacts' / 'ads' / 'images'


def image_path(name: str) -> Path:
    return IMAGES_DIR / f'{name}-1080.jpg'


@dataclass(frozen=True)
class AdCopy:
    title: str  # unified campaigns keep no second title (Direct drops Title2)
    text: str

    def validate(self) -> None:
        for label, value, limit in (
            ('title', self.title, TITLE_MAX),
            ('text', self.text, TEXT_MAX),
        ):
            if not value or len(value) > limit:
                raise ConfigError(f'Ad {label} must be 1..{limit} characters: {value!r} ({len(value)})')
            if not AD_TEXT_ALLOWED.match(value):
                bad = sorted({ch for ch in value if not AD_TEXT_ALLOWED.match(ch)})
                raise ConfigError(f'Ad {label} has characters Direct rejects: {bad} in {value!r}')
            long_words = [w for w in value.split() if len(w) > WORD_MAX]
            if long_words:
                raise ConfigError(f'Ad {label} has words longer than {WORD_MAX}: {long_words}')


@dataclass(frozen=True)
class GameGroup:
    """One ad group per daily game: its own phrases, ads and landing.

    A concrete puzzle is easier to sell than a puzzle site: «угадайте статью
    Википедии по скрытым словам» says what the player will do.
    """

    game: str
    title: str
    path: str  # landing path on the campaign host, the game's latest puzzle
    keywords: tuple[str, ...]
    ads: tuple[AdCopy, ...]
    image: str  # artifacts/ads/images/<image>-1080.jpg

    def validate(self) -> None:
        if not self.path.startswith('/') or not self.path.endswith('/last/'):
            raise ConfigError(f'{self.game}: landing path must be /<game>/last/')
        if not 1 <= len(self.ads) <= MAX_ADS_PER_GROUP:
            raise ConfigError(f'{self.game}: needs 1..{MAX_ADS_PER_GROUP} ads')
        for ad in self.ads:
            ad.validate()
        if not image_path(self.image).is_file():
            raise ConfigError(f'{self.game}: missing image {image_path(self.image)}')


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
    game_groups: tuple[GameGroup, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def origin(self) -> str:
        return self.landing_url.split('/start/')[0]

    def group_landing(self, group: GameGroup) -> str:
        return self.origin + group.path

    def group_name(self, group: GameGroup) -> str:
        return f'{self.name} — {group.title}'

    @property
    def allowed_hrefs(self) -> set[str]:
        return {self.landing_url} | {self.group_landing(g) for g in self.game_groups}

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
        if not 1 <= len(self.ads) <= MAX_ADS_PER_GROUP:
            raise ConfigError(f'{self.slug}: needs 1..{MAX_ADS_PER_GROUP} ads (Direct limit per group)')
        for ad in self.ads:
            ad.validate()
        assert_minus_phrases_safe(list(self.negative_keywords))
        for group in self.game_groups:
            group.validate()
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
        text='Лесенки, Салатики, Алфавитки и Цензурки. Новые задания каждый день, бесплатно.',
    ),
    AdCopy(
        title='Не квиз. Здесь нужно думать',
        text='Логика, слова и ресёрч вместо угадывания фактов. Новые задания каждый день.',
    ),
    AdCopy(
        title='Лесенки слов и другие головоломки',
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

GAME_GROUPS = (
    GameGroup(
        game='censorly',
        title='Цензурка',
        path='/censorly/last/',
        keywords=(
            'угадай статью википедии',
            'игра по википедии',
            'игры с википедией',
            'redactle',
            'pedantix',
            'педантикс',
            'угадай слово по тексту',
        ),
        ads=(
            AdCopy(
                title='Угадайте статью Википедии по скрытым словам',
                text='Все слова статьи скрыты. Называйте слова, и они проявляются. Угадаете тему?',
            ),
            AdCopy(
                title='Википедия, в которой спрятаны все слова',
                text='Открывайте текст слово за словом и угадайте, о чём статья. Бесплатно, в браузере.',
            ),
        ),
        image='censorly',
    ),
    GameGroup(
        game='ladder',
        title='Лесенка',
        path='/ladder/last/',
        keywords=(
            'цепочка слов',
            'игра цепочка слов',
            'словесная лесенка',
            'word ladder',
            'raddle',
            'игра ассоциации слов',
            'словесные головоломки',
        ),
        ads=(
            AdCopy(
                title='Лесенка: восстановите цепочку связанных слов',
                text='Каждое слово связано с соседним. По перемешанным подсказкам соберите цепочку.',
            ),
            AdCopy(
                # Never name a concrete puzzle: /last/ changes daily and old
                # puzzles leave the free window after a week.
                title='Соберите цепочку слов по подсказкам',
                text='Восстановите цепочку слов по подсказкам. Новая Лесенка каждый день, бесплатно.',
            ),
        ),
        image='ladder',
    ),
    GameGroup(
        game='salad',
        title='Салатик',
        path='/salad/last/',
        keywords=(
            'найди слова в сетке',
            'игра найди слова',
            'игра составь слова',
            'боггл',
            'boggle',
            'strands',
            'игры со словами онлайн',
        ),
        ads=(
            AdCopy(
                title='Салатик: найдите слова на тему в сетке 4 на 4',
                text='Соединяйте соседние буквы и ищите слова на общую тему. Правила понятны за минуту.',
            ),
            AdCopy(
                title='Словесная головоломка на 10 минут',
                text='Сетка 4 на 4, тема дня и спрятанные слова. Ежедневная игра Inter Oves, бесплатно.',
            ),
        ),
        image='salad',
    ),
    GameGroup(
        game='alphabetty',
        title='Алфавитка',
        path='/alphabetty/last/',
        keywords=(
            'угадай слово',
            'игра угадай слово',
            'угадай слово онлайн',
            'alphaguess',
            'вордли',
            'вордли на русском',
        ),
        ads=(
            AdCopy(
                title='Алфавитка: угадайте слово дня по алфавиту',
                text='Называйте слова и узнавайте, загаданное раньше или позже по алфавиту.',
            ),
            AdCopy(
                title='Угадайте загаданное слово, сужая алфавит',
                text='Каждая догадка сужает диапазон по алфавиту. Новое слово каждый день, бесплатно.',
            ),
        ),
        image='alphabetty',
    ),
)

CAMPAIGNS = {
    spec.slug: spec
    for spec in (
        CampaignSpec(
            slug='io-ru-nov26',
            name='Inter Oves — Россия → ru.interoves.com — ноябрь 2026',
            landing_url='https://ru.interoves.com/start/',
            region_ids=(REGION_RUSSIA,),
            spend_limit_rub=3000,
            cpa_rub=70,
            start=date(2026, 11, 3),
            end=date(2026, 11, 16),
            keywords=SHARED_KEYWORDS,
            ads=SHARED_ADS,
            game_groups=GAME_GROUPS,
            notes=('ru.interoves.com opens in Russia without VPN',),
        ),
        CampaignSpec(
            slug='io-intl-nov26',
            name='Inter Oves — заграница → interoves.com — ноябрь 2026',
            landing_url='https://interoves.com/start/',
            region_ids=INTERNATIONAL_REGIONS,
            spend_limit_rub=2000,
            cpa_rub=80,
            start=date(2026, 11, 3),
            end=date(2026, 11, 16),
            keywords=SHARED_KEYWORDS,
            # Direct allows three text ads per unified group: the third shared
            # ad gives way to one written for players abroad.
            ads=SHARED_ADS[:2] + (
                AdCopy(
                    title='Головоломки на русском языке',
                    text='Ежедневные словесные игры и задачи на логику от клуба Inter Oves. Бесплатно.',
                ),
            ),
            game_groups=GAME_GROUPS,
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
                # A unified campaign has one strategy; the network follows search.
                'Network': {'BiddingStrategyType': 'NETWORK_DEFAULT'},
            },
            'Settings': [
                {'Option': 'ADD_METRICA_TAG', 'Value': 'YES'},
                {'Option': 'ENABLE_SITE_MONITORING', 'Value': 'YES'},
                {'Option': 'ENABLE_AREA_OF_INTEREST_TARGETING', 'Value': 'NO'},
                {'Option': 'ALTERNATIVE_TEXTS_ENABLED', 'Value': 'NO'},
            ],
            'CounterIds': {'Items': [METRIKA_COUNTER_ID]},
            'TrackingParams': spec.tracking_params,
            'AttributionModel': 'AUTO',
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
        'game_groups': {
            group.game: {
                'name': spec.group_name(group),
                'landing': spec.group_landing(group),
                'image': str(image_path(group.image).relative_to(IMAGES_DIR.parents[2])),
                'keywords': list(group.keywords),
                'ads.add': game_ad_payloads(spec, group, 0, '<image hash>'),
            }
            for group in spec.game_groups
        },
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
    autotargeting_ids = narrow_autotargeting(ads, campaign_id, audit=audit)
    bidmodifier_ids = extract_add_ids(
        ads.call('bidmodifiers', 'add', {'BidModifiers': bidmodifier_payloads(spec, campaign_id)})
    )
    record.update({
        'adgroup_ids': [adgroup_id],
        'ad_ids': ad_ids,
        'keyword_ids': keyword_ids,
        'autotargeting_ids': autotargeting_ids,
        'bidmodifier_ids': bidmodifier_ids,
    })
    state_store.save(state)
    audit(action='create', object_type='campaign_tree', object_id=campaign_id,
          new_value={k: v for k, v in record.items() if k.endswith('_ids')},
          reason='spec create', api_status='created')
    return {'spec': spec.slug, **record}


# Autotargeting cannot be stopped in a unified campaign ("Автотаргетинг не
# может быть остановлен"). Only target and narrower queries stay on: on the
# August Master campaign the wide categories brought IQ-test, quiz and
# crossword traffic.
AUTOTARGETING_SETTINGS = {
    'Categories': {'Exact': 'YES', 'Narrow': 'YES', 'Alternative': 'NO', 'Accessory': 'NO', 'Broader': 'NO'},
    'BrandOptions': {'WithoutBrands': 'YES', 'WithAdvertiserBrand': 'YES', 'WithCompetitorsBrand': 'NO'},
}


def _autotargeting(keywords):
    return [k for k in keywords if (k.get('Keyword') or '').startswith('---autotargeting')]


def narrow_autotargeting(ads, campaign_id: int, *, audit) -> list[int]:
    from games.ops.social.ads import ops

    ids = [k['Id'] for k in _autotargeting(ops.keywords_get(ads, campaign_ids=[campaign_id]))]
    if ids:
        result = ads.call('keywords', 'update', {
            'Keywords': [{'Id': item_id, 'AutotargetingSettings': AUTOTARGETING_SETTINGS} for item_id in ids],
        })
        for row in result.get('UpdateResults') or []:
            if row.get('Errors'):
                raise ConfigError(f'Autotargeting update failed: {row["Errors"]}')
        audit(action='update', object_type='keywords', object_id=ids,
              new_value=AUTOTARGETING_SETTINGS, reason='autotargeting: exact and narrow only',
              api_status='updated')
    return ids


def verify(ads, spec: CampaignSpec, campaign_id: int) -> dict:
    """Compare the live campaign with the spec; ``ok`` gates launch."""
    from games.ops.social.ads import ops

    failures = []
    camp = (ops.campaigns_get(ads, [campaign_id]) or [None])[0]
    if not camp:
        return {'ok': False, 'failures': ['campaign not found']}
    unified = camp.get('UnifiedCampaign') or {}
    bidding = unified.get('BiddingStrategy') or {}
    network_type = (bidding.get('Network') or {}).get('BiddingStrategyType')
    if network_type != 'NETWORK_DEFAULT':
        failures.append(f'Network strategy is {network_type}, expected NETWORK_DEFAULT')
    for placement in ('Search',):
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
        if href not in spec.allowed_hrefs:
            failures.append(f'Ad {ad.get("Id")} href {href!r} is not a spec landing')
    for keyword in _autotargeting(ops.keywords_get(ads, campaign_ids=[campaign_id])):
        live = keyword.get('AutotargetingSettings') or {}
        for group, wanted in AUTOTARGETING_SETTINGS.items():
            for option, value in wanted.items():
                if value == 'NO' and (live.get(group) or {}).get(option) != 'NO':
                    failures.append(f'autotargeting {keyword.get("Id")} {group}.{option} is not NO')

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


# --- Game groups ------------------------------------------------------------


def upload_images(ads, names, *, audit, state_store) -> dict[str, str]:
    """Upload each image once per account; hashes are kept in state."""
    state = state_store.load()
    hashes = state.setdefault('ad_images', {})
    for name in names:
        if name in hashes:
            continue
        data = base64.b64encode(image_path(name).read_bytes()).decode('ascii')
        result = ads.call('adimages', 'add', {'AdImages': [{'ImageData': data, 'Name': f'interoves-{name}'}]})
        row = (result.get('AddResults') or [{}])[0]
        if row.get('Errors') or not row.get('AdImageHash'):
            raise ConfigError(f'AdImages.add {name}: {row}')
        hashes[name] = row['AdImageHash']
        state_store.save(state)
        audit(action='create', object_type='ad_image', object_id=hashes[name],
              new_value={'name': name}, reason='game screenshot for text-and-image ads', api_status='created')
    return {name: hashes[name] for name in names}


def game_ad_payloads(spec: CampaignSpec, group: GameGroup, adgroup_id: int, image_hash: str) -> list[dict]:
    return [
        {
            'AdGroupId': adgroup_id,
            'TextAd': {
                'Title': ad.title,
                'Text': ad.text,
                'Href': spec.group_landing(group),
                'Mobile': 'NO',
                'DisplayUrlPath': group.game,
                'AdImageHash': image_hash,
            },
        }
        for ad in group.ads
    ]


def add_game_groups(ads, spec: CampaignSpec, *, audit, state_store) -> dict:
    """Add the spec's game groups that the live campaign does not have yet.

    New ads go to moderation right away when the campaign is already launched.
    """
    state = state_store.load()
    record = (state.get('campaigns') or {}).get(spec.slug)
    if not record or not record.get('campaign_id'):
        raise ConfigError(f'{spec.slug} has not been created')
    campaign_id = record['campaign_id']
    hashes = upload_images(ads, [g.image for g in spec.game_groups], audit=audit, state_store=state_store)
    state = state_store.load()
    record = state['campaigns'][spec.slug]
    groups = record.setdefault('game_groups', {})
    added = []
    for group in spec.game_groups:
        if group.game in groups:
            continue
        adgroup_id = extract_add_id(ads.call('adgroups', 'add', {'AdGroups': [{
            'Name': spec.group_name(group),
            'CampaignId': campaign_id,
            'RegionIds': list(spec.region_ids),
            'UnifiedAdGroup': {'OfferRetargeting': 'NO'},
        }]}))
        try:
            ad_ids = extract_add_ids(ads.call('ads', 'add', {
                'Ads': game_ad_payloads(spec, group, adgroup_id, hashes[group.image]),
            }))
            keyword_ids = extract_add_ids(ads.call('keywords', 'add', {
                'Keywords': [{'Keyword': phrase, 'AdGroupId': adgroup_id} for phrase in group.keywords],
            }))
        except Exception:
            # Do not leave an empty group (with wide autotargeting) behind.
            ads.call('adgroups', 'delete', {'SelectionCriteria': {'Ids': [adgroup_id]}})
            audit(action='delete', object_type='adgroup', object_id=adgroup_id,
                  reason=f'rollback of failed {group.game} group', api_status='deleted')
            narrow_autotargeting(ads, campaign_id, audit=audit)
            raise
        groups[group.game] = {'adgroup_id': adgroup_id, 'ad_ids': ad_ids, 'keyword_ids': keyword_ids}
        state_store.save(state)
        audit(action='create', object_type='game_group', object_id=adgroup_id,
              new_value={'spec': spec.slug, 'game': group.game, 'ad_ids': ad_ids},
              reason='per-game ad group', api_status='created')
        added.append(group.game)
        # Per group, so a later failure does not leave earlier ads in draft.
        narrow_autotargeting(ads, campaign_id, audit=audit)
        if record.get('launched'):
            ads.call('ads', 'moderate', {'SelectionCriteria': {'Ids': ad_ids}})
            audit(action='moderate', object_type='ads', object_id=ad_ids,
                  reason='per-game ads of a launched campaign', api_status='submitted')
    narrow_autotargeting(ads, campaign_id, audit=audit)
    return {'spec': spec.slug, 'campaign_id': campaign_id, 'added': added, 'game_groups': groups}


def sync(ads, spec: CampaignSpec, *, audit, state_store) -> dict:
    """Bring a live campaign's strategy and game-ad texts back to the spec."""
    from games.ops.social.ads import ops

    record = (state_store.load().get('campaigns') or {}).get(spec.slug)
    if not record or not record.get('campaign_id'):
        raise ConfigError(f'{spec.slug} has not been created')
    campaign_id = record['campaign_id']
    payload = campaign_payload(spec)
    result = ads.call('campaigns', 'update', {'Campaigns': [{
        'Id': campaign_id,
        'UnifiedCampaign': {'BiddingStrategy': payload['UnifiedCampaign']['BiddingStrategy']},
    }]})
    for row in result.get('UpdateResults') or []:
        if row.get('Errors'):
            raise ConfigError(f'Campaigns.update: {row["Errors"]}')
    audit(action='update', object_type='campaign', object_id=campaign_id,
          new_value={'spend_limit_rub': spec.spend_limit_rub, 'cpa_rub': spec.cpa_rub},
          reason='sync strategy to spec', api_status='updated')

    live = {ad['Id']: ad for ad in ops.ads_get(ads, campaign_ids=[campaign_id])}
    updates = []
    for group in spec.game_groups:
        ids = ((record.get('game_groups') or {}).get(group.game) or {}).get('ad_ids') or []
        for ad_id, copy in zip(ids, group.ads):
            text_ad = (live.get(ad_id) or {}).get('TextAd') or {}
            wanted = {'Title': copy.title, 'Text': copy.text}
            if any(text_ad.get(k) != v for k, v in wanted.items()):
                updates.append({'Id': ad_id, 'TextAd': wanted})
    if updates:
        result = ads.call('ads', 'update', {'Ads': updates})
        for row in result.get('UpdateResults') or []:
            if row.get('Errors'):
                raise ConfigError(f'Ads.update: {row["Errors"]}')
        audit(action='update', object_type='ads', object_id=[u['Id'] for u in updates],
              reason='sync game-ad texts to spec', api_status='updated')
    return {'spec': spec.slug, 'campaign_id': campaign_id, 'updated_ads': [u['Id'] for u in updates],
            'verify': verify(ads, spec, campaign_id)}
