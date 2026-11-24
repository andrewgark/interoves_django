"""Support console: адвент-календарь. Доступ: support + группа Advent Calendar."""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from games.console.games.advent import (
    AdventSupportError,
    advent_dashboard_context,
    create_advent_day,
    defer_advent_day,
    delete_advent_day,
    get_advent_detail,
    list_advent_rows,
    reorder_advent,
    restore_advent_day,
    save_advent_settings,
    update_advent_day,
)
from games.console.http.access import advent_console_required, user_is_advent_only
from games.site.http.request_util import parse_json_object_body

_DAY_FIELDS = ('name', 'text', 'answers', 'answer', 'answer_comment')


def _error(message, status=400):
    return JsonResponse({'ok': False, 'error': str(message)}, status=status)


def _rows_payload():
    return [row.to_dict() for row in list_advent_rows()]


def _day_fields(body):
    fields = {}
    for key in _DAY_FIELDS:
        if key in body and body[key] is not None:
            fields[key] = str(body[key])
    return fields


@advent_console_required
@require_GET
def advent_dashboard(request):
    try:
        ctx = advent_dashboard_context()
    except AdventSupportError as exc:
        return _error(exc, status=404)
    ctx['page_title'] = 'Адвент-календарь'
    ctx['support_advent_only'] = user_is_advent_only(request.user)
    return render(request, 'support/advent.html', ctx)


@advent_console_required
@require_POST
def advent_settings_save(request):
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        settings = save_advent_settings(
            title=str(body.get('title') or ''),
            description=str(body.get('description') or ''),
            start_date=str(body.get('start_date') or ''),
            end_date=str(body.get('end_date') or ''),
            show_answer=body.get('show_answer') is True,
        )
    except AdventSupportError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'settings': settings, 'rows': _rows_payload()})


@advent_console_required
@require_GET
def advent_detail(request, link_id):
    try:
        item = get_advent_detail(int(link_id))
    except AdventSupportError as exc:
        return _error(exc, status=404)
    return JsonResponse({'ok': True, 'item': item})


@advent_console_required
@require_POST
def advent_create(request):
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        at_number = int(body.get('at_number'))
    except (TypeError, ValueError):
        return _error('Нужен at_number')
    try:
        item = create_advent_day(at_number=at_number, **_day_fields(body))
    except AdventSupportError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'item': item, 'rows': _rows_payload()})


@advent_console_required
@require_POST
def advent_update(request, link_id):
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        item = update_advent_day(int(link_id), **_day_fields(body))
    except AdventSupportError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'item': item, 'rows': _rows_payload()})


@advent_console_required
@require_POST
def advent_reorder(request):
    body = parse_json_object_body(request)
    order = body.get('order') if body else None
    if not isinstance(order, list):
        return _error('Нужен order: [link_id, …]')
    try:
        rows = reorder_advent([int(value) for value in order])
    except (TypeError, ValueError):
        return _error('order должен быть списком int')
    except AdventSupportError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


def _rows_response(operation, link_id):
    try:
        rows = operation(int(link_id))
    except Exception as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


@advent_console_required
@require_POST
def advent_delete(request, link_id):
    return _rows_response(delete_advent_day, link_id)


@advent_console_required
@require_POST
def advent_defer(request, link_id):
    return _rows_response(defer_advent_day, link_id)


@advent_console_required
@require_POST
def advent_restore(request, link_id):
    return _rows_response(restore_advent_day, link_id)
