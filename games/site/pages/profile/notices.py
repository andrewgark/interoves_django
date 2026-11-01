"""Main UI: remember one-off notices (see games.identity.ui_notices)."""

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from games.identity import ui_notices


@require_POST
def ui_notice_seen(request):
    key = (request.POST.get('key') or '').strip()
    if key not in ui_notices.KNOWN_KEYS:
        return JsonResponse({'status': 'invalid_key'}, status=400)
    # Anonymous players keep the key in localStorage only.
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'local_only'})
    ui_notices.mark_seen(request.user, key)
    return JsonResponse({'status': 'ok'})
