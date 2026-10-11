from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0271_bugreport_type_metadata'),
    ]

    operations = [
        migrations.CreateModel(
            name='CensorlyReviewCase',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('pair_key', models.CharField(max_length=260)),
                ('guess', models.CharField(max_length=128)),
                ('target', models.CharField(max_length=128)),
                ('expected', models.CharField(choices=[('OPEN', 'Ожидалось открытие'), ('CLOSED', 'Ожидалось закрытие')], max_length=6)),
                ('actual', models.CharField(blank=True, default='', max_length=6)),
                ('classification', models.CharField(blank=True, default='', max_length=128)),
                ('fix_layer', models.CharField(blank=True, default='', max_length=128)),
                ('status', models.CharField(choices=[('DIAGNOSED', 'DIAGNOSED'), ('NEEDS_REVIEW', 'NEEDS_REVIEW'), ('APPROVED', 'APPROVED'), ('REJECTED', 'REJECTED'), ('APPLIED', 'APPLIED')], default='DIAGNOSED', max_length=32)),
                ('decision', models.CharField(blank=True, default='', max_length=32)),
                ('note', models.TextField(blank=True, default='')),
                ('report_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-updated_at'],
                'constraints': [
                    models.UniqueConstraint(fields=('pair_key', 'expected'), name='games_censorly_case_pair_expected_uniq'),
                ],
            },
        ),
        migrations.AddField(
            model_name='bugreport',
            name='censorly_review_case',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports', to='games.censorlyreviewcase'),
        ),
    ]
