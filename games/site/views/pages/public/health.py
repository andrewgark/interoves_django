from django.http import HttpResponse
from django.db import connection, DatabaseError
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def live(_request):
    """ALB liveness: prove Django/Daphne responds, without external dependencies."""
    return HttpResponse('ok', content_type='text/plain')


@require_GET
@never_cache
def ready(_request):
    """Readiness: prove the app can reach the production database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return HttpResponse('unready', status=503, content_type='text/plain')
    return HttpResponse('ok', content_type='text/plain')
