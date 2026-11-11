"""Model admin registrations, grouped like the future model packages."""

from games.site.admin import (
    billing as _billing,  # noqa: F401
    content as _content,  # noqa: F401
    daily as _daily,  # noqa: F401
    jobs as _jobs,  # noqa: F401
    offline as _offline,  # noqa: F401
    people as _people,  # noqa: F401
)
from games.site.admin.attempts import AttemptAdmin
from games.site.admin.catalog import TaskAdmin, WordSaladTaskForm

__all__ = ['AttemptAdmin', 'TaskAdmin', 'WordSaladTaskForm']
