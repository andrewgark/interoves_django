from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0249_gametaskgroup_is_deferred')]

    operations = [
        migrations.AddField(
            model_name='gametaskgroup',
            name='deferred_number',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
