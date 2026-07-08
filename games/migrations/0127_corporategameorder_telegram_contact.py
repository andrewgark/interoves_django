from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0126_ladder_game_is_ready'),
    ]

    operations = [
        migrations.RenameField(
            model_name='corporategameorder',
            old_name='phone',
            new_name='telegram',
        ),
        migrations.AlterField(
            model_name='corporategameorder',
            name='telegram',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Telegram'),
        ),
        migrations.AddField(
            model_name='corporategameorder',
            name='preferred_contact',
            field=models.CharField(
                choices=[('email', 'Email'), ('telegram', 'Telegram'), ('other', 'Другое')],
                default='email',
                max_length=20,
                verbose_name='способ связи',
            ),
        ),
    ]
