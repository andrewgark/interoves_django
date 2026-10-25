"""Public merchant, purchase and privacy documents."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


LEGAL_CONTEXT = {
    'ru_seller': {
        'name': 'Андрей Александрович Гаркавый',
        'status': 'Самозанятый / плательщик НПД',
        'country': 'Российская Федерация',
        'tax_id': '771585877401',
        'address': '127273, Москва, ул. Отрадная, д. 1, кв. 37',
        'phone': '+7 916 599-47-69',
        'email': 'andrewgarkavyy@gmail.com',
    },
    'am_seller': {
        'name': 'Andrei Garkavyi IE',
        'status': 'Individual Entrepreneur',
        'country': 'Republic of Armenia',
        'tax_id': '40106411',
        'address': '28 Vardanants St., Apt. 90, Kentron, Yerevan 0070, Armenia',
        'phone': '+374 77 558440',
        'email': 'andrewgarkavyy@gmail.com',
    },
    'support_email': 'andrewgarkavyy@gmail.com',
}


@never_cache
@require_GET
def legal_page(request, document):
    templates = {
        'sellers': ('new/legal/sellers.html', 'Продавцы и реквизиты'),
        'terms': ('new/legal/terms_index.html', 'Условия покупки'),
        'terms_russia': ('new/legal/terms_russia.html', 'Условия покупки — российские карты'),
        'terms_armenia': ('new/legal/terms_armenia.html', 'Условия покупки — международные карты'),
        'terms_crypto': ('new/legal/terms_crypto.html', 'Условия покупки — криптовалюта'),
        'terms_tribute': ('new/legal/terms_tribute.html', 'Условия покупки — Tribute'),
        'refunds': ('new/legal/refunds.html', 'Оплата, отмена и возврат'),
        'privacy': ('new/legal/privacy.html', 'Политика конфиденциальности'),
        'contacts': ('new/legal/contacts.html', 'Контакты'),
    }
    template_name, page_title = templates[document]
    from games.tribute_config import merchant

    return render(request, template_name, {
        **LEGAL_CONTEXT,
        'page_title': page_title,
        'tribute_merchant': merchant(),
    })
