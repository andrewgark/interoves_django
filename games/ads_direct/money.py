"""Yandex Direct money units: currency × 1_000_000."""

MICROS_PER_UNIT = 1_000_000


def rubles_to_micros(rubles: float | int) -> int:
    return int(round(float(rubles) * MICROS_PER_UNIT))


def micros_to_rubles(micros: int | float | str | None) -> float:
    if micros in (None, ""):
        return 0.0
    return float(micros) / MICROS_PER_UNIT


def parse_report_money(value) -> float:
    """Parse Direct Reports money when returnMoneyInMicros=false (rubles) or true."""
    if value in (None, "", "--"):
        return 0.0
    text = str(value).replace(",", ".").replace("\xa0", "").replace(" ", "")
    return float(text)
