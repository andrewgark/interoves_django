from django.core.management.base import BaseCommand
from games.ops.analytics.hub_entry_experiment import RETURN_WINDOW_DAYS, experiment_summary


def _pct(part, whole):
    return f'{100 * part / whole:.0f}%' if whole else '—'


class Command(BaseCommand):
    help = 'Hub A/B test for first-time players: today\'s puzzles vs /<game>/first/ (read-only).'

    def handle(self, *args, **options):
        summary = experiment_summary()
        self.stdout.write(
            f'{"arm":<6} {"assigned":>8} {"started":>8} {"finished 1st":>13} '
            f'{"returned ≤" + str(RETURN_WINDOW_DAYS) + "d":>14}'
        )
        for variant, row in summary.items():
            self.stdout.write(
                f'{variant:<6} {row["assigned"]:>8} '
                f'{row["started"]:>4} {_pct(row["started"], row["assigned"]):>4} '
                f'{row["finished_first"]:>8} {_pct(row["finished_first"], row["started"]):>4} '
                f'{row["returned"]:>4}/{row["return_eligible"]:<4} {_pct(row["returned"], row["return_eligible"]):>4}'
            )
