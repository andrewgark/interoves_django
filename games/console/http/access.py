from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from games.console.http.constants import ADVENT_CONSOLE_GROUP, SUPPORT_CONSOLE_GROUP

SUPPORT_LOGIN_URL = '/support/login/'
ADVENT_CONSOLE_URL = '/support/advent/'


def user_has_support_access(user):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=SUPPORT_CONSOLE_GROUP).exists()


def user_has_advent_access(user):
    """Full support staff, plus the advent calendar editors."""
    if user_has_support_access(user):
        return True
    if not user.is_authenticated or not user.is_active:
        return False
    return user.groups.filter(name=ADVENT_CONSOLE_GROUP).exists()


def user_is_advent_only(user):
    """An advent editor who must not see the rest of the console."""
    return user_has_advent_access(user) and not user_has_support_access(user)


def support_console_required(view_func):
    @login_required(login_url=SUPPORT_LOGIN_URL)
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_has_support_access(request.user):
            if request.method == 'GET' and user_is_advent_only(request.user):
                return redirect(ADVENT_CONSOLE_URL)
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


def advent_console_required(view_func):
    # Advent editors sign in through the site (VK, Telegram, …), not with a
    # console password, so an anonymous visitor goes to the site login.
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_has_advent_access(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped
