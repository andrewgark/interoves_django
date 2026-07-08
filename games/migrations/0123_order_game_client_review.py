from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0122_hint_points_penalty_default'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderGameClient',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('company_name', models.CharField(max_length=200)),
                ('logo_url', models.URLField(max_length=500)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_published', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'клиент (заказ игры)',
                'verbose_name_plural': 'клиенты (заказ игры)',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='OrderGameReview',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('signature', models.CharField(help_text='Например: Рома, 31 годик, оператор ЭВМ', max_length=300)),
                ('photo_url', models.URLField(blank=True, default='', max_length=500)),
                ('text', models.TextField()),
                ('is_important', models.BooleanField(default=False, help_text='Важные отзывы показываются чуть чаще')),
                ('is_published', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'отзыв (заказ игры)',
                'verbose_name_plural': 'отзывы (заказ игры)',
                'ordering': ['-is_important', 'id'],
            },
        ),
    ]
