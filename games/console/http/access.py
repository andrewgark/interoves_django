from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from games.console.http.constants import LIMITED_CONSOLE_SECTIONS, SUPPORT_CONSOLE_GROUP

SUPPORT_LOGIN_URL = '/support/login/'


def user_has_support_access(user):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=SUPPORT_CONSOLE_GROUP).exists()


def limited_console_sections(user):
    """Sections a user outside full support may open: [{id, url, title}]."""
    if not user.is_authenticated or not user.is_active or user_has_support_access(user):
        return []
    groups = set(user.groups.values_list('name', flat=True))
    return [
        {'id': section_id, 'url': url, 'title': title}
        for section_id, group, url, title in LIMITED_CONSOLE_SECTIONS
        if group in groups
    ]


def user_has_section_access(user, section_id):
    """Full support staff, plus the editors of this one section."""
    if user_has_support_access(user):
        return True
    return any(item['id'] == section_id for item in limited_console_sections(user))


def support_console_required(view_func):
    @login_required(login_url=SUPPORT_LOGIN_URL)
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_has_support_access(request.user):
            sections = limited_console_sections(request.user)
            if request.method == 'GET' and sections:
                return redirect(sections[0]['url'])
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


def section_console_required(view_func):
    """For views taking ``section``: support staff or that section's editors.

    Section editors sign in through the site (VK, Telegram, …), not with a
    console password, so an anonymous visitor goes to the site login.
    """
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, section, **kwargs):
        if not user_has_section_access(request.user, section):
            raise PermissionDenied
        return view_func(request, *args, section=section, **kwargs)

    return _wrapped
