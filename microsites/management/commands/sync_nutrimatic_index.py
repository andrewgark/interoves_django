from django.core.management.base import BaseCommand

from microsites.nutrimatic_s3_index import ensure_nutrimatic_index_from_s3


class Command(BaseCommand):
    help = (
        "Download Nutrimatic .index from S3 (env NUTRIMATIC_INDEX_S3_BUCKET, "
        "NUTRIMATIC_INDEX_S3_KEY). Use in EB postdeploy or before traffic."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-download even if cached file exists.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        path = ensure_nutrimatic_index_from_s3(force=force)
        if path is None:
            self.stdout.write(
                self.style.WARNING(
                    "NUTRIMATIC_INDEX_S3_BUCKET / NUTRIMATIC_INDEX_S3_KEY not set; nothing to do."
                )
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Nutrimatic index at {path}"))
