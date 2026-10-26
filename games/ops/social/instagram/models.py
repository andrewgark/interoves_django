from django.db import models


class InstagramToken(models.Model):
    """Singleton store for the live long-lived Instagram access token.

    Instagram long-lived tokens expire after 60 days and must be refreshed (which yields a
    *new* token string). On Elastic Beanstalk the token is seeded from the
    INSTAGRAM_ACCESS_TOKEN env var, but a cron can't rewrite env vars without a disruptive
    redeploy — so the refreshed token is persisted here and read DB-first at runtime
    (env/settings is only the fallback / initial seed). See games.instagram.api.
    """

    singleton_id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    access_token = models.TextField()
    refreshed_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Instagram token'
        verbose_name_plural = 'Instagram token'

    def save(self, *args, **kwargs):
        self.singleton_id = 1  # enforce single row
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.filter(pk=1).first()

    def __str__(self):
        return 'InstagramToken(refreshed_at={})'.format(self.refreshed_at)
