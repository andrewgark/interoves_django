import logging

from django.conf import settings
from django.core.mail import send_mail
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from games.forms import CorporateGameOrderForm
from games.models import CorporateGameOrder, OrderGameClient, OrderGameReview

logger = logging.getLogger('application')

CONTACT_METHOD_LABELS = dict(CorporateGameOrder.ContactMethod.choices)


def _contact_method_label(order) -> str:
    if order.contact_method == CorporateGameOrder.ContactMethod.OTHER and order.contact_other_label:
        return 'Другое ({})'.format(order.contact_other_label)
    return CONTACT_METHOD_LABELS.get(order.contact_method, order.contact_method)


def _format_order_email(order) -> str:
    lines = [
        'Новая заявка на корпоративную игру с interoves.com/order-game/',
        '',
        'Компания: {}'.format(order.company_name),
        'Контактное лицо: {}'.format(order.contact_name),
        'Способ связи: {}'.format(_contact_method_label(order)),
        'Контакт: {}'.format(order.contact_value),
    ]
    if order.team_size:
        lines.append('Размер команды: {}'.format(order.team_size))
    if order.preferred_date:
        lines.append('Когда провести: {}'.format(order.preferred_date))
    if order.message:
        lines.extend(['', 'Комментарий:', order.message])
    lines.extend(['', 'ID заявки: #{}'.format(order.id)])
    return '\n'.join(lines)


def _send_order_game_email(order) -> bool:
    recipient = getattr(settings, 'CORPORATE_ORDER_EMAIL', 'andrewgarkavyy@gmail.com')
    subject = 'Заявка на игру: {} — {}'.format(order.company_name, order.contact_name)
    body = _format_order_email(order)
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception('Failed to send order-game email for #%s', order.id)
        return False


@require_http_methods(['GET', 'POST'])
def order_game_landing(request):
    submitted = False
    if request.method == 'POST':
        form = CorporateGameOrderForm(request.POST)
        if form.is_valid():
            order = form.save()
            if _send_order_game_email(order):
                order.email_sent = True
                order.save(update_fields=['email_sent'])
            submitted = True
            form = CorporateGameOrderForm()
    else:
        form = CorporateGameOrderForm()

    clients = list(OrderGameClient.objects.filter(is_published=True))
    reviews = list(OrderGameReview.objects.filter(is_published=True))

    return render(request, 'new/order_game.html', {
        'form': form,
        'submitted': submitted,
        'page_title': 'Игры для компаний',
        'clients': clients,
        'reviews': reviews,
    })
