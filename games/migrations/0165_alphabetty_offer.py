from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0164_profile_telegram_and_ladder_offer'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AlphabettyOffer',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('draft', 'Черновик'), ('sent', 'Отправлена'), ('accepted', 'Принята')], db_index=True, default='draft', max_length=16)),
                ('share_hash', models.CharField(db_index=True, max_length=32, unique=True)),
                ('word', models.CharField(blank=True, default='', max_length=64)),
                ('comment', models.TextField(blank=True, default='', verbose_name='комментарий для Андрея')),
                ('admin_note', models.TextField(blank=True, default='', verbose_name='заметка Андрея при возврате на доработку')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('accepted_link', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='accepted_alphabetty_offers', to='games.gametaskgroup')),
                ('task_group', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='alphabetty_offer', to='games.taskgroup')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alphabetty_offers', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'предложение алфавитки',
                'verbose_name_plural': 'предложения алфавиток',
                'ordering': ['-updated_at'],
            },
        ),
    ]
