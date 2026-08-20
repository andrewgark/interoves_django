from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('games', '0178_ticket_purchase_goal_queue'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountMerge',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('target_user_id_snapshot', models.PositiveIntegerField(db_index=True)),
                ('source_user_id_snapshot', models.PositiveIntegerField(db_index=True)),
                ('provider', models.CharField(blank=True, default='', max_length=32)),
                ('provider_uid', models.CharField(blank=True, default='', max_length=191)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('source_user', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='account_merge_as_source', to=settings.AUTH_USER_MODEL)),
                ('target_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='account_merges_received', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'объединение аккаунтов',
                'verbose_name_plural': 'объединения аккаунтов',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='AnonAccountClaim',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('anon_key', models.CharField(db_index=True, max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='anon_account_claims', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'привязка гостевого прогресса',
                'verbose_name_plural': 'привязки гостевого прогресса',
                'ordering': ['-created_at'],
            },
        ),
    ]
