from django.db import models


class ThreadsToken(models.Model):
    """Singleton store for the live long-lived Threads access token.

    Threads long-lived tokens expire after 60 days; refreshing one yields a *new*
    token string. Production seeds it from the THREADS_ACCESS_TOKEN SSM parameter,
    but the refresh job can't rewrite that without restarting the worker, so the
    rotated token is persisted here and read DB-first (settings is the seed).
    See games.ops.social.threads.api.current_access_token.
    """

    singleton_id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    access_token = models.TextField()
    # The settings token this row was last seeded from: a reissued token placed in
    # SSM differs from it and replaces the DB token (see threads.refresh).
    seed_token = models.TextField(blank=True, default='')
    refreshed_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Threads token'
        verbose_name_plural = 'Threads token'

    def save(self, *args, **kwargs):
        self.singleton_id = 1  # enforce single row
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.filter(pk=1).first()

    def __str__(self):
        return 'ThreadsToken(refreshed_at={})'.format(self.refreshed_at)
