import json
import os

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

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=1)

    def handle(self, *args, **options):
        if runtime_role() != 'worker' or os.environ.get('INTEROVES_VALIDATION_MODE') != '1':
            raise CommandError('validation seed requires worker runtime and INTEROVES_VALIDATION_MODE=1')
        count = options['count']
        if count < 1 or count > 150:
            raise CommandError('--count must be between 1 and 150')
        with transaction.atomic():
            for name in (
                'Правила Десяточки',
                'Правила турнирного режима',
                'Правила тренировочного режима',
            ):
                HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
            detail = create_word_salad()
            task = Task.objects.get(pk=detail['task_id'])
            game = Game.objects.get(pk='salad')
            rows = []
            for index in range(count):
                user, _ = User.objects.get_or_create(
                    username=f'sqsd-validation-user-{index}',
                )
                job = WordSaladRecheckJob.objects.create(
                    task=task,
                    game=game,
                    task_revision=task.attempt_revision,
                    total_actors=1,
                )
                item = WordSaladRecheckItem.objects.create(
                    job=job, actor_key=str(user.pk), user=user,
                )
                outbox = WordSaladRecheckOutbox.objects.create(
                    item=item, task_revision=job.task_revision,
                )
                rows.append({
                    'job_id': job.pk,
                    'item_id': item.pk,
                    'actor_id': item.actor_key,
                    'task_revision': str(job.task_revision),
                    'outbox_id': outbox.pk,
                })
        self.stdout.write(json.dumps(rows[0] if count == 1 else {
            'count': count,
            'first_item_id': rows[0]['item_id'],
            'last_item_id': rows[-1]['item_id'],
        }))
