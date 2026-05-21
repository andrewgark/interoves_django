from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0116_alter_game_section_default_rules'),
    ]

    operations = [
        migrations.CreateModel(
            name='HiddenAnonKey',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anon_key', models.CharField(db_index=True, max_length=64, unique=True)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
            ],
            options={
                'verbose_name': 'Скрытый аноним',
                'verbose_name_plural': 'Скрытые анонимы',
            },
        ),
    ]
