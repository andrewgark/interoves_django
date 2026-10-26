"""Django admin autodiscover entry. Registrations live in games.site.admin."""
from games.site.admin.attempts import AttemptAdmin
from games.site.admin.catalog import TaskAdmin, WordSaladTaskForm

from games.site.admin import billing as _billing
from games.site.admin import content as _content
from games.site.admin import daily as _daily
from games.site.admin import jobs as _jobs
from games.site.admin import people as _people

__all__ = ['AttemptAdmin', 'TaskAdmin', 'WordSaladTaskForm']
