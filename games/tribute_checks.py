from django.conf import settings
from django.core.checks import Error, Warning, register

from games.tribute_config import configuration_errors, product_configuration


@register()
def tribute_configuration_check(app_configs, **kwargs):
    if getattr(settings, 'TRIBUTE_ENABLED', False):
        return [
            Error(message, id='games.E_TRIBUTE_CONFIG')
            for message in configuration_errors()
        ]

    _products, errors = product_configuration()
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
    if any_product_value and errors:
        return [Warning(message, id='games.W_TRIBUTE_CONFIG') for message in errors]
    return []
