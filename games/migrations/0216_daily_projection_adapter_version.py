from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0215_daily_projection_coverage'),
    ]

    operations = [
        migrations.RenameField(
            model_name='dailyresultprojectionstate',
            old_name='source_revision',
            new_name='adapter_version',
        ),
    ]
