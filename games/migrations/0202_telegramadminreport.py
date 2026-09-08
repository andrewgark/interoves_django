from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('games', '0201_next_game_vote')]

    operations = [
        migrations.CreateModel(
            name='TelegramAdminReport',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('report_type', models.CharField(choices=[('daily', 'Daily'), ('weekly', 'Weekly')], max_length=16)),
                ('period_start', models.DateTimeField()),
                ('period_end', models.DateTimeField()),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-period_start']},
        ),
        migrations.AddConstraint(
            model_name='telegramadminreport',
            constraint=models.UniqueConstraint(
                fields=('report_type', 'period_start', 'period_end'),
                name='uniq_telegram_admin_report_period',
            ),
        ),
    ]
