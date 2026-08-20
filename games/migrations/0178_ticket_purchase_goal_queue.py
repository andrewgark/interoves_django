from datetime import datetime, timezone

from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q
import django.db.models.deletion


PRODUCT_ANALYTICS_LAUNCHED_AT = datetime(2026, 8, 10, tzinfo=timezone.utc)


def queue_recent_accepted_purchases(apps, schema_editor):
    TicketRequest = apps.get_model('games', 'TicketRequest')
    automated_payment = (
        Q(yookassa_id__isnull=False) & ~Q(yookassa_id='')
        | Q(nowpayments_id__isnull=False) & ~Q(nowpayments_id='')
    )
    TicketRequest.objects.filter(
        status='Accepted',
        time__gte=PRODUCT_ANALYTICS_LAUNCHED_AT,
        purchase_goal_sent_at__isnull=True,
    ).filter(automated_payment).update(purchase_goal_queued_at=F('time'))


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('games', '0177_socialqueuepost_social_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketrequest',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_ticket_requests',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='metrika_client_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='purchase_goal_queued_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(
            queue_recent_accepted_purchases,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
