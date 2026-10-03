"""Backward-compatible alias → review_censorly_pool obscure."""

from games.management.commands.review_censorly_pool import Command as PoolReviewCommand


class Command(PoolReviewCommand):
    def add_arguments(self, parser):
        super().add_arguments(parser)
        # force obscure mode when using the old command name
        parser.set_defaults(mode='obscure')
