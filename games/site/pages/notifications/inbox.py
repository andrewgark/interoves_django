"""The notification inbox: bell dropdown, full page, read state."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from games.notifications import service
from games.notifications.kinds import GROUPS
from games.site.pages import NEW_UI_PROJECT, _project_urls_context

DROPDOWN_LIMIT = 10


@login_required
@require_http_methods(['GET'])
def notifications_page(request):
    group = request.GET.get('group') or ''
    if group and group not in GROUPS:
        group = ''
    ctx = {
        'page_title': 'Уведомления',
        'notifications': service.inbox(request.user, group=group or None),
        'notification_groups': list(GROUPS.values()),
        'notification_group': group,
    }
    service.mark_seen(request.user)
    ctx.update(_project_urls_context(NEW_UI_PROJECT))
    return render(request, 'site/notifications.html', ctx)


@login_required
@require_http_methods(['GET'])
def notifications_dropdown(request):
    """HTML for the bell panel; opening it clears the badge."""
    items = service.inbox(request.user, limit=DROPDOWN_LIMIT)
    service.mark_seen(request.user)
    return render(request, 'site/partials/notifications_list.html', {
        'notifications': items,
        'notifications_compact': True,
    })


@login_required
@require_http_methods(['GET'])
def notification_open(request, notification_id):
    row = service.get_for_user(request.user, notification_id)
    if row is None:
        raise Http404()
    service.mark_read(request.user, [row.pk])
    item = service.render(row)
    target = (item or {}).get('url') or ''
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = ''
    return redirect(target or 'new_notifications')


@login_required
@require_http_methods(['POST'])
def notifications_read_all(request):
    count = service.mark_read(request.user)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('accept', ''):
        return JsonResponse({'read': count, 'badge': 0})
    return redirect('new_notifications')
