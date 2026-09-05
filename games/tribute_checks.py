from django.conf import settings
from django.core.checks import Error, Warning, register

from games.tribute_config import (
    club_configuration_errors,
    club_product_configuration,
    configuration_errors,
    product_configuration,
)


@register()
def tribute_configuration_check(app_configs, **kwargs):
    errors = []
    if getattr(settings, 'TRIBUTE_ENABLED', False):
        errors.extend(
            Error(message, id='games.E_TRIBUTE_CONFIG')
            for message in configuration_errors()
        )

    _products, product_errors = product_configuration()
    any_product_value = any(
        str(getattr(settings, name, '') or '').strip()
        for name in (
            'TRIBUTE_REGULAR_PRODUCT_ID',
            'TRIBUTE_REGULAR_PRODUCT_WEB_URL',
            'TRIBUTE_REGULAR_PRODUCT_AMOUNT',
            'TRIBUTE_REGULAR_PRODUCT_CURRENCY',
            'TRIBUTE_DISCOUNT_PRODUCT_ID',
            'TRIBUTE_DISCOUNT_PRODUCT_WEB_URL',
            'TRIBUTE_DISCOUNT_PRODUCT_AMOUNT',
            'TRIBUTE_DISCOUNT_PRODUCT_CURRENCY',
        )
    )
    if any_product_value and product_errors and not getattr(settings, 'TRIBUTE_ENABLED', False):
        errors.extend(Warning(message, id='games.W_TRIBUTE_CONFIG') for message in product_errors)

    if getattr(settings, 'CLUB_SUBSCRIPTION_ENABLED', False):
        errors.extend(
            Error(message, id='games.E_CLUB_TRIBUTE_CONFIG')
            for message in club_configuration_errors()
        )
    else:
        _club_products, club_errors = club_product_configuration()
        any_club_value = any(
            str(getattr(settings, name, '') or '').strip()
            for name in (
                'TRIBUTE_CLUB_SUBSCRIPTION_RUB_ID',
                'TRIBUTE_CLUB_SUBSCRIPTION_RUB_URL',
                'TRIBUTE_CLUB_SUBSCRIPTION_USD_ID',
                'TRIBUTE_CLUB_SUBSCRIPTION_USD_URL',
            )
        )
        if any_club_value and club_errors:
            errors.extend(Warning(message, id='games.W_CLUB_TRIBUTE_CONFIG') for message in club_errors)
    return errors
