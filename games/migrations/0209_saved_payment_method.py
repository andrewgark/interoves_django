from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def move_legacy_tokens(apps, schema_editor):
    Subscription = apps.get_model('games', 'ClubSubscription')
    Method = apps.get_model('games', 'SavedPaymentMethod')
    Payment = apps.get_model('games', 'ClubYooKassaPayment')
    alias = schema_editor.connection.alias
    for sub in Subscription.objects.using(alias).exclude(yookassa_payment_method_id='').iterator():
        # The old model did not record type/card details. Do not invent them or
        # enable recurring charges for an unverified payment method type.
        method = Method.objects.using(alias).create(
            user_id=sub.user_id, provider='yookassa',
            provider_payment_method_id=sub.yookassa_payment_method_id, method_type='',
        )
        Subscription.objects.using(alias).filter(pk=sub.pk).update(
            saved_payment_method_id=method.pk, yookassa_payment_method_id='',
            auto_renew=False, next_charge_at=None,
        )
    Payment.objects.using(alias).filter(kind='recurring_monthly', status='pending').update(
        submitted_at=models.F('created_at'),
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('games', '0208_club_yookassa_subscription'),
    ]
    operations = [
        migrations.CreateModel(
            name='SavedPaymentMethod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(default='yookassa', max_length=16)),
                ('provider_payment_method_id', models.CharField(blank=True, max_length=64, null=True)),
                ('method_type', models.CharField(default='bank_card', max_length=32)),
                ('card_last4', models.CharField(blank=True, default='', max_length=4)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('detached_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='saved_payment_methods', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name='clubsubscription', name='saved_payment_method',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='subscriptions', to='games.savedpaymentmethod'),
        ),
        migrations.AddField(
            model_name='clubsubscription', name='payment_method_detached_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='clubyookassapayment', name='submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Irreversible by design: rollback must not reconstruct erased credentials.
        migrations.RunPython(move_legacy_tokens),
        migrations.RemoveField(model_name='clubsubscription', name='yookassa_payment_method_id'),
    ]
