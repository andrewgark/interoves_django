"""Model admin registrations, grouped like the future model packages."""

from games.site.admin.attempts import AttemptAdmin
from games.site.admin.catalog import TaskAdmin, WordSaladTaskForm

from games.site.admin import billing as _billing  # noqa: F401
from games.site.admin import content as _content  # noqa: F401
from games.site.admin import daily as _daily  # noqa: F401
from games.site.admin import jobs as _jobs  # noqa: F401
from games.site.admin import people as _people  # noqa: F401

__all__ = ['AttemptAdmin', 'TaskAdmin', 'WordSaladTaskForm']
