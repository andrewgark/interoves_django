import json

from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme


def parse_json_object_body(request):
    """Parse a request body only when it contains a JSON object."""
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def json_boolean_field(body, key, *, default=False):
    """Read one JSON boolean without treating strings like ``"false"`` as true."""
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ValueError('{} должен быть boolean'.format(key))
    return value


def get_public_task_or_404(task_id):
    """Задание для публичных вьюх: скрытые (is_removed) дают 404."""
    from games.models import Task

    return get_object_or_404(Task.objects.visible(), pk=task_id)


def redirect_to_referer(request):
    for target in (request.META.get('HTTP_REFERER'), request.POST.get('next'), request.GET.get('next')):
        if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            return HttpResponseRedirect(target)
    return HttpResponseRedirect('/')


def is_browser_form_submission(request):
    """True for a native HTML form POST, not an AJAX/fetch request."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return False
    return 'text/html' in request.headers.get('Accept', '').lower()


def redirect_after_browser_submission(request):
    """Return to a same-host page after a native browser mutation POST."""
    referer = request.META.get('HTTP_REFERER', '')
    if url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect('/')
