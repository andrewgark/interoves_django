import logging

from django.conf import settings
from django.core.mail import send_mail
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from games.forms import CorporateGameOrderForm

logger = logging.getLogger('application')


def _format_order_email(order) -> str:
    lines = [
        'Новая заявка на корпоративную игру с interoves.com/corporate/',
        '',
        'Компания: {}'.format(order.company_name),
        'Контакт: {}'.format(order.contact_name),
        'Email: {}'.format(order.email),
    ]
    if order.phone:
        lines.append('Телефон: {}'.format(order.phone))
    if order.team_size:
        lines.append('Размер команды: {}'.format(order.team_size))
    if order.preferred_date:
        lines.append('Когда провести: {}'.format(order.preferred_date))
    if order.message:
        lines.extend(['', 'Комментарий:', order.message])
    lines.extend(['', 'ID заявки: #{}'.format(order.id)])
    return '\n'.join(lines)


def _send_corporate_order_email(order) -> bool:
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
        logger.exception('Failed to send corporate order email for #%s', order.id)
        return False


@require_http_methods(['GET', 'POST'])
def corporate_landing(request):
    submitted = False
    if request.method == 'POST':
        form = CorporateGameOrderForm(request.POST)
        if form.is_valid():
            order = form.save()
            if _send_corporate_order_email(order):
                order.email_sent = True
                order.save(update_fields=['email_sent'])
            submitted = True
            form = CorporateGameOrderForm()
    else:
        form = CorporateGameOrderForm()

    return render(request, 'new/corporate.html', {
        'form': form,
        'submitted': submitted,
        'page_title': 'Игры для компаний',
    })
