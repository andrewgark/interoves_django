import time

from django.core.management.base import BaseCommand, CommandError

from games.models import Game, GameResultsSnapshot
from games.results_snapshot import freeze_game_results


class Command(BaseCommand):
    help = "Freeze (create/update) results snapshots for games without blocking admin requests."

    def add_arguments(self, parser):
        parser.add_argument("--mode", default="tournament", choices=["tournament", "general"])
        parser.add_argument("--game-id", default=None, help="Freeze a single game by id.")
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only create snapshots that don't exist yet (default behavior for bulk runs).",
        )
        parser.add_argument("--overwrite", action="store_true", help="Overwrite existing snapshots.")
        parser.add_argument("--limit", type=int, default=0, help="Limit number of games processed (0 = no limit).")
        parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between games (throttle).")

    def handle(self, *args, **options):
        mode = options["mode"]
        game_id = options["game_id"]
        only_missing = bool(options["only_missing"])
        overwrite = bool(options["overwrite"])
        limit = int(options["limit"] or 0)
        sleep_s = float(options["sleep"] or 0.0)

        if overwrite and only_missing:
            raise CommandError("--overwrite and --only-missing are mutually exclusive.")

        if game_id:
            games = Game.objects.filter(id=game_id)
            if not games.exists():
                raise CommandError(f"Game not found: {game_id}")
        else:
            games = Game.objects.all().order_by("id")

        created = 0
        updated = 0
        skipped = 0
        errors = 0

        processed = 0
        for game in games.iterator():
            if limit and processed >= limit:
                break
            processed += 1

            try:
                existing = GameResultsSnapshot.objects.filter(game=game, mode=mode).first()
                if only_missing and existing:
                    skipped += 1
                    self.stdout.write(f"[{processed}] {game.id}: skip (exists)")
                    continue

                obj, did = freeze_game_results(game, mode=mode, overwrite=overwrite)
                if not existing and did:
                    created += 1
                    self.stdout.write(f"[{processed}] {game.id}: created snapshot id={obj.id}")
                elif existing and did:
                    updated += 1
                    self.stdout.write(f"[{processed}] {game.id}: updated snapshot id={obj.id}")
                else:
                    skipped += 1
                    self.stdout.write(f"[{processed}] {game.id}: skip (no-op)")
            except Exception as e:
                errors += 1
                self.stderr.write(f"[{processed}] {game.id}: ERROR {type(e).__name__}: {e}")

            if sleep_s > 0:
                time.sleep(sleep_s)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed} created={created} updated={updated} skipped={skipped} errors={errors}"
            )
        )

