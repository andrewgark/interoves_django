from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from games.support.constants import SUPPORT_CONSOLE_GROUP


def user_has_support_access(user):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=SUPPORT_CONSOLE_GROUP).exists()


def support_console_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_has_support_access(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped
