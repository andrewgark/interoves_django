from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0246_clubentitlement_club_entitlement_yookassa_payment_uniq_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='RandomAlphabettyGame',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('word', models.CharField(max_length=64, unique=True)),
                ('share_hash', models.CharField(db_index=True, max_length=32, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('task_group', models.OneToOneField(on_delete=models.deletion.CASCADE, related_name='random_alphabetty_game', to='games.taskgroup')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
