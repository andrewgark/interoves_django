"""Shared profile formatting and readiness checks for player submissions."""

from games.telegram_linking import offer_telegram_gaps


def normalize_telegram_handle(raw: str) -> str:
    value = (raw or '').strip()
    if value.startswith('@'):
        value = value[1:].strip()
    return value


def profile_ready_for_offers(profile) -> tuple[bool, list[str]]:
    missing = []
    if not (profile.first_name or '').strip():
        missing.append('first_name')
    if not (profile.last_name or '').strip():
        missing.append('last_name')
    missing.extend(offer_telegram_gaps(profile))
    return (not missing, missing)


def profile_display_name(profile) -> str:
    return '{} {}'.format(
        (profile.first_name or '').strip(),
        (profile.last_name or '').strip(),
    ).strip()
