import datetime as dt
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from games.models import Attempt


def _day_bounds(day: dt.date, tz) -> tuple[dt.datetime, dt.datetime]:
    start_naive = dt.datetime.combine(day, dt.time.min)
    end_naive = start_naive + dt.timedelta(days=1)
    return timezone.make_aware(start_naive, tz), timezone.make_aware(end_naive, tz)


def calculate_stat(*, days: int = 30, tz=None) -> list[dict]:
    """
    For each of the last N days (including today), count unique users + teams
    that sent at least one Attempt (skip=False).
    """
    if tz is None:
        tz = timezone.get_current_timezone()

    today = timezone.localdate()
    days = int(days or 0)
    if days <= 0:
        return []

    result: list[dict] = []
    for i in range(days):
        day = today - dt.timedelta(days=(days - 1 - i))
        start, end = _day_bounds(day, tz)
        qs = Attempt.manager.filter(time__gte=start, time__lt=end, skip=False)
        users = qs.filter(user__isnull=False).values("user_id").distinct().count()
        teams = qs.filter(team__isnull=False).values("team_id").distinct().count()
        result.append(
            {
                "day": day.isoformat(),
                "users": users,
                "teams": teams,
            }
        )
    return result


class Command(BaseCommand):
    help = "Count unique users and teams that submitted at least one attempt for each of the last N days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Number of days to report (default: 30).")
        parser.add_argument(
            "--format",
            choices=["csv", "json"],
            default="csv",
            help="Output format (default: csv).",
        )

    def handle(self, *args, **options):
        days = int(options["days"] or 0)
        fmt = options["format"]

        rows = calculate_stat(days=days)

        if fmt == "json":
            self.stdout.write(json.dumps(rows, ensure_ascii=False))
            return

        self.stdout.write("day,users,teams")
        for r in rows:
            self.stdout.write(f'{r["day"]},{r["users"]},{r["teams"]}')
