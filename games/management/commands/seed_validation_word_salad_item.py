import json

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from games.models import (
    Game,
    HTMLPage,
    Task,
    WordSaladRecheckItem,
    WordSaladRecheckJob,
    WordSaladRecheckOutbox,
)
from games.runtime import runtime_role
from games.support.services.word_salad import create_word_salad


class Command(BaseCommand):
    help = 'Create one synthetic Word Salad item for an isolated validation stack.'

    def handle(self, *args, **options):
        if runtime_role() != 'worker' or __import__('os').environ.get('INTEROVES_VALIDATION_MODE') != '1':
            raise CommandError('validation seed requires worker runtime and INTEROVES_VALIDATION_MODE=1')
        with transaction.atomic():
            for name in (
                'Правила Десяточки',
                'Правила турнирного режима',
                'Правила тренировочного режима',
            ):
                HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
            user, _ = User.objects.get_or_create(username='sqsd-validation-user')
            detail = create_word_salad()
            task = Task.objects.get(pk=detail['task_id'])
            game = Game.objects.get(pk='salad')
            job, _ = WordSaladRecheckJob.objects.get_or_create(
                task=task, game=game, status=WordSaladRecheckJob.STATUS_PENDING,
                defaults={'total_actors': 1},
            )
            item, _ = WordSaladRecheckItem.objects.get_or_create(
                job=job, actor_key=str(user.pk),
                defaults={'user': user},
            )
            outbox, _ = WordSaladRecheckOutbox.objects.get_or_create(
                item=item, task_revision=job.task_revision,
            )
        self.stdout.write(json.dumps({
            'job_id': job.pk,
            'item_id': item.pk,
            'actor_id': item.actor_key,
            'task_revision': str(job.task_revision),
            'outbox_id': outbox.pk,
        }))
