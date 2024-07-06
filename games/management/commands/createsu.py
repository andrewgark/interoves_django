from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from interoves_django.settings import load_secret


class Command(BaseCommand):

    def handle(self, *args, **options):
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "andrewgarkavyy@gmail.com", load_secret("django_admin_password.txt"))
