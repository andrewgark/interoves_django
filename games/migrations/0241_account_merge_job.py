from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('games', '0240_profile_club_archive_offer_deferred'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountMergeJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('target_user_id_snapshot', models.PositiveIntegerField(db_index=True)),
                ('source_user_id_snapshot', models.PositiveIntegerField(db_index=True)),
                ('provider', models.CharField(blank=True, default='', max_length=32)),
                ('provider_uid', models.CharField(blank=True, default='', max_length=191)),
                ('status', models.CharField(choices=[('pending', 'Ожидает обработки'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=16)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('claim_token', models.UUIDField(blank=True, null=True)),
                ('claimed_until', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('account_merge', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='job', to='games.accountmerge')),
                ('source_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='account_merge_jobs_source', to=settings.AUTH_USER_MODEL)),
                ('target_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='account_merge_jobs_target', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['status', 'next_attempt_at'], name='games_acmj_due_idx'),
                    models.Index(fields=['target_user_id_snapshot', 'status'], name='games_acmj_target_status_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('target_user_id_snapshot', 'source_user_id_snapshot', 'provider', 'provider_uid'), name='games_amj_identity_uniq'),
                ],
            },
        ),
    ]
