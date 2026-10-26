"""Convert selected transient database failures into safe HTTP responses.

Django 4.2 converts exceptions raised by each middleware layer into a 500
response before control returns to outer middleware. ``got_request_exception``
is therefore used to carry only a bounded MySQL errno to this outer response
normalizer. Django still emits its normal exception log and traceback.
"""

from __future__ import annotations

import logging
import sys

from django.db import DatabaseError, ProgrammingError
from django.core.signals import got_request_exception
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger('application')

_RETRYABLE_MYSQL_CODES = {1040, 1205, 1213, 2006, 2013}
_ROLLBACK_RETRY_CODES = {1205, 1213}
_UNKNOWN_COMMIT_CODES = {2006, 2013}


def mysql_error_code(exc: BaseException) -> int | None:
    """Return a MySQL errno from Django/driver exception wrappers, if present."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        # A ProgrammingError is an application/query defect even if its first
        # argument happens to resemble one of the transient MySQL errnos.
        # Do not follow its context into an earlier transient exception.
        if isinstance(current, ProgrammingError):
            return None
        args = getattr(current, 'args', ()) or ()
        code = getattr(current, 'errno', None)
        if code is None and args:
            try:
                code = int(args[0])
            except (TypeError, ValueError):
                code = None
        is_database_exception = (
            isinstance(current, DatabaseError)
            or current.__class__.__module__.startswith(('MySQLdb', 'pymysql'))
            or getattr(current, 'errno', None) is not None
        )
        if is_database_exception and code in _RETRYABLE_MYSQL_CODES:
            return code
        current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
    return None


def _remember_database_exception(sender, request, **kwargs):
    """Attach safe classification to this request; never retain exception text."""
    exception = kwargs.get('exception')
    if exception is None:
        # Django 4.2's signal does not include the exception argument. The
        # active exception is available while response_for_exception runs.
        exception = sys.exc_info()[1]
    code = mysql_error_code(exception) if exception is not None else None
    # A later, nested exception may replace an earlier database exception.
    # Always overwrite the request marker so the outer normalizer classifies
    # the exception Django is converting now, rather than stale request state.
    request._interoves_mysql_error_code = code
    request._interoves_mysql_retry_context = {
        'timing_event_idempotent': bool(
            getattr(request, '_interoves_timing_event_idempotent', False)
        ),
        'timing_pre_mutation': bool(
            getattr(request, '_interoves_timing_pre_mutation', False)
        ),
        'timing_read_only': bool(
            getattr(request, '_interoves_timing_read_only', False)
        ),
    }


got_request_exception.connect(
    _remember_database_exception,
    dispatch_uid='games.middleware.db_transient_response.remember_errno',
    weak=False,
)


class TransientDatabaseResponseMiddleware:
    """Return 503 for exhausted connections and idempotent timing retries."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        code = getattr(request, '_interoves_mysql_error_code', None)
        if code is None:
            return response
        status = getattr(response, 'status_code', None)
        if status == 503:
            logger.warning(
                'db_transient_response status=503 mysql_errno=%s request_id=%s '
                'method=%s path=%s route=%s',
                code,
                getattr(request, 'interoves_request_id', 'unavailable'),
                getattr(request, 'method', 'unknown'),
                getattr(request, 'path', '/') or '/',
                getattr(getattr(request, 'resolver_match', None), 'url_name', None) or 'unavailable',
            )
            return response
        if status != 500:
            return response

        retry_context = getattr(request, '_interoves_mysql_retry_context', {})
        timing_retry_is_safe = retry_context.get('timing_event_idempotent', False)
        timing_pre_mutation = retry_context.get('timing_pre_mutation', False)
        timing_read_only = retry_context.get('timing_read_only', False)
        timing_post = (
            request.method == 'POST'
            and (request.path or '').rstrip('/').endswith('/timing')
        )
        # A failed DB connect during session/auth middleware occurs before the
        # view can perform a business mutation. For requests that reached the
        # view, only the timing endpoint has an explicit idempotency contract.
        pre_view_connection_failure = code == 1040 and not getattr(
            request, 'resolver_match', None
        )
        # Once URL resolution has completed, a generic request may already
        # have performed a business mutation. Only explicitly marked timing
        # events have an idempotency contract that permits retry after view
        # execution. Never infer read-only behavior from the HTTP method.
        is_connection_exhaustion = code == 1040 and (
            pre_view_connection_failure or timing_retry_is_safe or timing_pre_mutation
            or timing_read_only
        )
        is_timing_transient = (
            code in _ROLLBACK_RETRY_CODES and timing_post
        ) or (
            code in _UNKNOWN_COMMIT_CODES and (timing_retry_is_safe or timing_pre_mutation)
        )
        if not (is_connection_exhaustion or is_timing_transient):
            return response

        logger.warning(
            'db_transient_response status=503 mysql_errno=%s request_id=%s '
            'method=%s path=%s route=%s',
            code,
            getattr(request, 'interoves_request_id', 'unavailable'),
            getattr(request, 'method', 'unknown'),
            getattr(request, 'path', '/') or '/',
            getattr(getattr(request, 'resolver_match', None), 'url_name', None) or 'unavailable',
        )
        if (request.path or '').rstrip('/').endswith('/timing'):
            response = JsonResponse(
                {'ok': False, 'error': 'temporarily_unavailable'}, status=503,
            )
        else:
            response = HttpResponse(
                'Service temporarily unavailable', status=503,
                content_type='text/plain; charset=utf-8',
            )
        response['Retry-After'] = '2'
        response['Cache-Control'] = 'no-store'
        return response
