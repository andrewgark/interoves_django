"""Ensure user 'admin' has a games.Profile (run after createsuperuser without createsu)."""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from games.models import Profile


class Command(BaseCommand):
    help = "Create Profile for admin user if missing."

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username="admin")
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR("No user with username 'admin'."))
            return
        profile, created = Profile.objects.get_or_create(
            user=user,
            defaults={
                "first_name": user.first_name or "Admin",
                "last_name": user.last_name or "User",
                "email": user.email or "",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created Profile for admin."))
        else:
            self.stdout.write("admin already has a Profile.")
