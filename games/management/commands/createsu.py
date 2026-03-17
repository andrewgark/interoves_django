from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from interoves_django.settings import load_secret
from games.models import Profile


class Command(BaseCommand):

    def handle(self, *args, **options):
        if not User.objects.filter(username="admin").exists():
            user = User.objects.create_superuser(
                "admin", "andrewgarkavyy@gmail.com", load_secret("django_admin_password.txt")
            )
            Profile.objects.create(
                user=user,
                first_name="Admin",
                last_name="User",
                email=user.email or "",
            )
        else:
            user = User.objects.get(username="admin")
            Profile.objects.get_or_create(
                user=user,
                defaults={
                    "first_name": user.first_name or "Admin",
                    "last_name": user.last_name or "User",
                    "email": user.email or "",
                },
            )
