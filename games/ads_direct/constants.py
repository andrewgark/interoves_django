"""Non-secret advertising constants for Inter Oves Direct/Metrika."""

from datetime import date

OLD_MASTER_CAMPAIGN_ID = 713889884
METRIKA_COUNTER_ID = 108320022
MAX_EXPERIMENT_SPEND_RUB = 500
MIN_CUSTOM_PERIOD_BUDGET_RUB = 550
RESERVE_BUDGET_RUB = 900
REGION_RUSSIA = 225
CURRENCY = "RUB"
TIMEZONE = "Europe/Moscow"
LANDING_URL = "https://interoves.com/start/"
CAMPAIGN_NAME = "Inter Oves — API managed — Sep 2026"
AD_GROUP_NAME = "РСЯ desktop — Sep 2026"

# Product Metrika goals (counter 108320022). Preflight re-checks names against the API.
GOAL_GAME_START = 595331160
GOAL_GAME_COMPLETE = 595331071
GOAL_ACTIVATED = 595331078
GOAL_SIGNUP = 595331052
GOAL_TICKET_CHECKOUT = 595331002
GOAL_TICKET_PURCHASE = 595330918
GOAL_OPENED_START = 602431719
GOAL_SELECTED_GAME_FROM_START = 602432526
GOAL_FINISHED_FIRST_FROM_START = 602432624
GOAL_STARTED_SECOND_FROM_START = 602432629

PRODUCT_GOALS = {
    "game_start": GOAL_GAME_START,
    "game_complete": GOAL_GAME_COMPLETE,
    "activated": GOAL_ACTIVATED,
    "signup": GOAL_SIGNUP,
    "ticket_checkout": GOAL_TICKET_CHECKOUT,
    "ticket_purchase": GOAL_TICKET_PURCHASE,
    "opened_start": GOAL_OPENED_START,
    "selected_game_from_start": GOAL_SELECTED_GAME_FROM_START,
    "finished_first_from_start": GOAL_FINISHED_FIRST_FROM_START,
    "started_second_from_start": GOAL_STARTED_SECOND_FROM_START,
}

GOAL_NAME_ALIASES = {
    GOAL_GAME_START: ("Начал игру",),
    GOAL_GAME_COMPLETE: ("Завершил игру",),
    GOAL_ACTIVATED: ("Завершил 3 игры",),
    GOAL_TICKET_PURCHASE: ("Оплатил билет", "ticket_purchase"),
}

# Historical Master campaign, 27.08–08.09.2026. Direct Conversions are not a KPI.
BASELINE = {
    "period": {"from": "2026-08-27", "to": "2026-09-08"},
    "direct_spend_rub": 6797,
    "direct_clicks": 3354,
    "game_start_users": 286,
    "game_complete_users": 91,
    "activated_users": 35,
    "ticket_purchase": 0,
    "cpa_complete_rub": 75,
    "cpa_complete_desktop_rub": 65,
    "cpa_complete_smartphone_rub": 171,
    "rsya_spend_rub": 4385,
    "rsya_complete": 59,
    "search_spend_rub": 2412,
    "search_complete": 32,
    "game_yandex_ru_users": 255,
    "game_yandex_ru_complete": 30,
    "game_yandex_ru_activated": 12,
    "next_day_return_after_complete": 0.19,
}

# Minus-phrases from Search Query Performance of 713889884 (27.08–08.09).
# Specified without a leading minus, as required by Direct API.
# Intentionally omitted: тест, игры, онлайн, слова, головоломк* — too broad / would cut good traffic.
CAMPAIGN_NEGATIVE_KEYWORDS = [
    "айкью",
    "iq",
    "судоку",
    "сканворд",
    "сканворды",
    "кроссворд",
    "кроссворды",
    "филворд",
        "викторина",
        "букв",
    ]

SAFE_KEYWORD_STEMS = (
    "головолом",
    "вордли",
    "угадай",
    "лесен",
    "салат",
    "interoves",
    "логик",
    "интеллектуал",
)

# Contextual phrases for РСЯ. Autotargeting is left off: it produced IQ/quiz junk on the Master campaign.
CONTEXT_KEYWORDS = [
    "головоломки",
    "головоломки онлайн",
    "вордли",
    "угадай слово",
    "интеллектуальные игры",
    "игры на логику",
]

AD_TITLE = "Не квиз. Здесь нужно думать"
AD_TITLE2 = "Игры Inter Oves онлайн"
AD_TEXT = "Головоломки и лесенки. Без дурацких квизов."
AD_DISPLAY_PATH = "start"

EXPERIMENT_START = date(2026, 9, 8)
# Custom-period window ≈ 10 days so 500 ₽ is above RUB MinimumWeeklySpendLimit (300 ₽) if Direct prorates.
EXPERIMENT_END = date(2026, 9, 18)

STOP_CAMPAIGN_SPEND_RUB = 500
BAD_QUALITY_ZERO_COMPLETE_SPEND_RUB = 200
BAD_QUALITY_HIGH_CPA_SPEND_RUB = 300
BAD_QUALITY_CPA_COMPLETE_RUB = 120
