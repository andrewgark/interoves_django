from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0263_daily_task_result_projection'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyresultprojection',
            name='fallback_duration_seconds',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Rebuildable first-to-last attempt duration when no timer row exists.',
            ),
        ),
    ]
