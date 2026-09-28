"""Inspect worker runtime configuration without printing secret values."""

from django.core.management.base import BaseCommand, CommandError

from games.worker_config import load_worker_config
from games.worker_contract import WORKER_REGISTRY


class Command(BaseCommand):
    help = "Validate the selected worker's runtime configuration safely."

    def add_arguments(self, parser):
        parser.add_argument("subcommand", nargs="?", default="check", choices=("check", "list"))
        parser.add_argument("--worker", choices=tuple(spec.name for spec in WORKER_REGISTRY.all()))
        parser.add_argument("--mode")
        parser.add_argument("--profile")
        parser.add_argument("--strict", action="store_true", help="Fail when worker-specific env is missing.")

    def handle(self, *args, **options):
        if options["subcommand"] == "list":
            for spec in WORKER_REGISTRY.all():
                self.stdout.write(
                    "{} queue={} modes={}".format(
                        spec.name, spec.queue_name, ",".join(spec.allowed_modes),
                    )
                )
            return
        try:
            config = load_worker_config(
                worker_name=options.get("worker"),
                mode=options.get("mode"),
                profile=options.get("profile"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write("worker={}".format(config.worker.name))
        self.stdout.write("role={}".format(config.worker.runtime_role))
        self.stdout.write("queue={}".format(config.worker.queue_name))
        self.stdout.write("mode={}".format(config.mode))
        self.stdout.write("profile={}".format(config.profile))
        if config.missing:
            self.stderr.write("missing_env={}".format(",".join(config.missing)))
            if options["strict"]:
                raise CommandError("worker configuration is incomplete")
        else:
            self.stdout.write(self.style.SUCCESS("configuration=ok"))
