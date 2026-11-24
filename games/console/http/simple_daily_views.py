"""Support console: простые ежедневные игры (адвент-календарь, криптики).

Каждый view получает ``section`` из URLConf. Доступ: support или группа раздела.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from games.console.games.simple_daily import SimpleDailyError
from games.console.games.simple_daily_sections import console_for
from games.console.http.access import limited_console_sections, section_console_required
from games.site.http.request_util import parse_json_object_body


def _error(message, status=400):
    return JsonResponse({'ok': False, 'error': str(message)}, status=status)


def _rows_payload(console):
    return [row.to_dict() for row in console.rows()]


def _fields(console, body):
    raw = body.get('fields')
    if not isinstance(raw, dict):
        return None
    keys = {day_field.key for day_field in console.day_fields()}
    return {key: str(value) for key, value in raw.items() if key in keys and value is not None}


@section_console_required
@require_GET
def dashboard(request, *, section):
    console = console_for(section)
    try:
        ctx = console.dashboard_context()
    except SimpleDailyError as exc:
        return _error(exc, status=404)
    ctx['page_title'] = console.section.title
    ctx['support_limited_sections'] = limited_console_sections(request.user)
    ctx['day_fields'] = console.day_fields()
    return render(request, 'support/simple_daily.html', ctx)


@section_console_required
@require_POST
def settings_save(request, *, section):
    console = console_for(section)
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        settings = console.save_settings(
            title=str(body.get('title') or ''),
            description=str(body.get('description') or ''),
            start_date=str(body.get('start_date') or ''),
            end_date=str(body.get('end_date') or ''),
            show_answer=body.get('show_answer') is True,
        )
    except SimpleDailyError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'settings': settings, 'rows': _rows_payload(console)})


@section_console_required
@require_GET
def detail(request, link_id, *, section):
    try:
        item = console_for(section).detail(int(link_id))
    except SimpleDailyError as exc:
        return _error(exc, status=404)
    return JsonResponse({'ok': True, 'item': item})


@section_console_required
@require_POST
def create(request, *, section):
    console = console_for(section)
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        at_number = int(body.get('at_number'))
    except (TypeError, ValueError):
        return _error('Нужен at_number')
    try:
        item = console.create(
            at_number=at_number,
            name=body.get('name'),
            fields=_fields(console, body) or {},
        )
    except SimpleDailyError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'item': item, 'rows': _rows_payload(console)})


@section_console_required
@require_POST
def update(request, link_id, *, section):
    console = console_for(section)
    body = parse_json_object_body(request)
    if body is None:
        return _error('Некорректный JSON')
    try:
        item = console.update(int(link_id), name=body.get('name'), fields=_fields(console, body))
    except SimpleDailyError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'item': item, 'rows': _rows_payload(console)})


@section_console_required
@require_POST
def reorder(request, *, section):
    body = parse_json_object_body(request)
    order = body.get('order') if body else None
    if not isinstance(order, list):
        return _error('Нужен order: [link_id, …]')
    try:
        rows = console_for(section).reorder([int(value) for value in order])
    except (TypeError, ValueError):
        return _error('order должен быть списком int')
    except SimpleDailyError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


def _rows_response(operation, link_id):
    try:
        rows = operation(int(link_id))
    except Exception as exc:
        return _error(exc)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


@section_console_required
@require_POST
def delete(request, link_id, *, section):
    return _rows_response(console_for(section).delete, link_id)


@section_console_required
@require_POST
def defer(request, link_id, *, section):
    return _rows_response(console_for(section).defer, link_id)


@section_console_required
@require_POST
def restore(request, link_id, *, section):
    return _rows_response(console_for(section).restore, link_id)


@section_console_required
@require_POST
def upload(request, *, section):
    uploaded = request.FILES.get('file')
    if uploaded is None:
        return _error('Нет файла')
    try:
        media = console_for(section).upload_media(uploaded)
    except SimpleDailyError as exc:
        return _error(exc)
    return JsonResponse({'ok': True, **media})


def urlpatterns_for(section):
    from django.urls import path

    kw = {'section': section}
    return [
        path(f'{section}/', dashboard, kw, name=section),
        path(f'{section}/settings/', settings_save, kw, name=f'{section}_settings'),
        path(f'{section}/create/', create, kw, name=f'{section}_create'),
        path(f'{section}/reorder/', reorder, kw, name=f'{section}_reorder'),
        path(f'{section}/upload/', upload, kw, name=f'{section}_upload'),
        path(f'{section}/<int:link_id>/', detail, kw, name=f'{section}_detail'),
        path(f'{section}/<int:link_id>/save/', update, kw, name=f'{section}_update'),
        path(f'{section}/<int:link_id>/delete/', delete, kw, name=f'{section}_delete'),
        path(f'{section}/<int:link_id>/defer/', defer, kw, name=f'{section}_defer'),
        path(f'{section}/<int:link_id>/restore/', restore, kw, name=f'{section}_restore'),
    ]
