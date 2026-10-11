from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0269_renumber_deferred_schedule_links'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CensorlyOffer',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('sent', 'Отправлена'), ('accepted', 'Принята'), ('rejected', 'Не выбрана')], db_index=True, default='sent', max_length=16)),
                ('comment', models.TextField(blank=True, default='')),
                ('admin_note', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('accepted_link', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='accepted_censorly_offers', to='games.gametaskgroup')),
                ('party', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='offers', to='games.randomcensorlygame')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='censorly_offers', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'предложение Цензурки',
                'verbose_name_plural': 'предложения Цензурок',
                'ordering': ['-updated_at'],
            },
        ),
    ]
