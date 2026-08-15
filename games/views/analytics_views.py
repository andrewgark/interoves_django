from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from games.analytics import acknowledge_analytics_goal


@csrf_exempt
@require_POST
def analytics_goal_ack(request):
    """Idempotent signed acknowledgement from the Metrika reachGoal callback."""
    token = request.POST.get('token')
    if not token or not acknowledge_analytics_goal(token):
        return JsonResponse({'ok': False}, status=400)
    return JsonResponse({'ok': True})
