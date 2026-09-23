from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0220_raddleuistate'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyresultprojectionstate',
            name='coverage_complete',
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name='dailyresultprojectionstate',
            name='full_refresh_required',
            field=models.BooleanField(default=True, db_index=True),
        ),
        migrations.AddField(
            model_name='dailyresultprojectionstate',
            name='is_valid',
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name='dailyresultprojectionstate',
            name='pending_actor_refreshes',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dailyresultprojectionstate',
            name='source_revision',
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
