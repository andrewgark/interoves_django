from django.core.files.base import ContentFile
from django.db import migrations, models


def forwards_migrate_ladder_posts(apps, schema_editor):
    Old = apps.get_model('games', 'TelegramLadderChannelPost')
    New = apps.get_model('games', 'SocialQueuePost')
    for row in Old.objects.all().order_by('ladder_date'):
        tg_status = 'pending'
        tg_error = row.error or ''
        if row.status == 'scheduled':
            tg_status = 'scheduled'
        elif row.status == 'sent':
            tg_status = 'sent'
        elif row.status == 'failed':
            tg_status = 'failed'
            if row.error == 'preparing':
                tg_status = 'pending'

        tw_status = 'pending'
        if row.twitter_tweet_id:
            tw_status = 'sent'
        elif row.twitter_error:
            tw_status = 'failed'

        ig_status = 'pending'
        if row.instagram_media_id:
            ig_status = 'sent'
        elif row.instagram_error:
            ig_status = 'failed'

        post = New(
            caption=row.caption or '',
            source='ladder',
            ladder_date=row.ladder_date,
            ladder_number=row.ladder_number,
            play_url=row.play_url or '',
            created_at=row.prepared_at,
            telegram_status=tg_status,
            telegram_external_id=str(row.telegram_message_id or '') if row.telegram_message_id else '',
            telegram_error=tg_error,
            telegram_at=row.sent_at,
            telegram_scheduled_for=row.scheduled_for,
            twitter_status=tw_status,
            twitter_external_id=row.twitter_tweet_id or '',
            twitter_error=row.twitter_error or '',
            instagram_status=ig_status,
            instagram_external_id=row.instagram_media_id or '',
            instagram_error=row.instagram_error or '',
        )
        # Avoid auto_now_add overriding created_at on first save.
        post.save()
        New.objects.filter(pk=post.pk).update(created_at=row.prepared_at)

        png = bytes(row.image_png or b'')
        if png:
            post.image.save(
                'ladder-{}.png'.format(row.ladder_number),
                ContentFile(png),
                save=True,
            )


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0143_instagramtoken'),
    ]

    operations = [
        migrations.CreateModel(
            name='SocialQueuePost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('caption', models.TextField(blank=True, default='')),
                ('image', models.ImageField(blank=True, null=True, upload_to='social_queue/')),
                ('source', models.CharField(
                    choices=[('manual', 'Manual'), ('ladder', 'Ladder')],
                    default='manual',
                    max_length=16,
                )),
                ('ladder_date', models.DateField(
                    blank=True,
                    help_text='MSK calendar date when source=ladder (idempotency key for cron)',
                    null=True,
                    unique=True,
                )),
                ('ladder_number', models.PositiveIntegerField(blank=True, null=True)),
                ('play_url', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('telegram_status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('scheduled', 'Scheduled in Telegram'),
                        ('sent', 'Sent'),
                        ('failed', 'Failed'),
                        ('skipped', 'Skipped'),
                    ],
                    default='pending',
                    max_length=16,
                )),
                ('telegram_external_id', models.CharField(blank=True, default='', max_length=64)),
                ('telegram_error', models.TextField(blank=True, default='')),
                ('telegram_at', models.DateTimeField(blank=True, null=True)),
                ('telegram_scheduled_for', models.DateTimeField(blank=True, null=True)),
                ('twitter_status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('sent', 'Sent'),
                        ('failed', 'Failed'),
                        ('skipped', 'Skipped'),
                    ],
                    default='pending',
                    max_length=16,
                )),
                ('twitter_external_id', models.CharField(blank=True, default='', max_length=64)),
                ('twitter_error', models.TextField(blank=True, default='')),
                ('twitter_at', models.DateTimeField(blank=True, null=True)),
                ('instagram_status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('sent', 'Sent'),
                        ('failed', 'Failed'),
                        ('skipped', 'Skipped'),
                    ],
                    default='pending',
                    max_length=16,
                )),
                ('instagram_external_id', models.CharField(blank=True, default='', max_length=64)),
                ('instagram_error', models.TextField(blank=True, default='')),
                ('instagram_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ('-created_at',),
            },
        ),
        migrations.RunPython(forwards_migrate_ladder_posts, backwards_noop),
        migrations.DeleteModel(
            name='TelegramLadderChannelPost',
        ),
    ]
