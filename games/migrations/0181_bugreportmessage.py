from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_opening_messages(apps, schema_editor):
    BugReport = apps.get_model('games', 'BugReport')
    BugReportMessage = apps.get_model('games', 'BugReportMessage')
    batch = []
    for report in BugReport.objects.all().iterator():
        batch.append(BugReportMessage(
            report_id=report.pk,
            author_user_id=report.user_id,
            author_role='user',
            text=report.text or '',
            created_at=report.time,
        ))
        if len(batch) >= 500:
            BugReportMessage.objects.bulk_create(batch)
            batch = []
    if batch:
        BugReportMessage.objects.bulk_create(batch)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('games', '0180_tribute_digital_products'),
    ]

    operations = [
        migrations.AddField(
            model_name='bugreport',
            name='user_last_read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='BugReportMessage',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('author_role', models.CharField(choices=[('user', 'User'), ('admin', 'Admin')], max_length=16)),
                ('text', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('telegram_message_id', models.BigIntegerField(blank=True, null=True)),
                ('author_user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='bug_report_messages',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('report', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages',
                    to='games.bugreport',
                )),
            ],
            options={
                'ordering': ['created_at', 'id'],
            },
        ),
        migrations.RunPython(copy_opening_messages, noop_reverse),
    ]
