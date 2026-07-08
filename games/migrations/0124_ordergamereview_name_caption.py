from django.db import migrations, models


def split_signature(apps, schema_editor):
    OrderGameReview = apps.get_model('games', 'OrderGameReview')
    for review in OrderGameReview.objects.all():
        signature = review.signature
        if ', ' in signature:
            name, caption = signature.split(', ', 1)
            review.name = name.strip()
            review.caption = caption.strip()
        else:
            review.name = signature.strip()
            review.caption = ''
        review.save(update_fields=['name', 'caption'])


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0123_order_game_client_review'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordergamereview',
            name='name',
            field=models.CharField(default='', max_length=120, verbose_name='имя'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='ordergamereview',
            name='caption',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Например: 31 годик, оператор ЭВМ',
                max_length=200,
                verbose_name='как подписать',
            ),
        ),
        migrations.RunPython(split_signature, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='ordergamereview',
            name='signature',
        ),
    ]
