from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def live(_request):
    """ALB liveness: prove Django/Daphne responds, without external dependencies."""
    return HttpResponse('ok', content_type='text/plain')
