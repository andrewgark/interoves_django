"""Shared author checks for scheduled daily task pages."""


def is_task_author(user, task_group):
    """Return whether an authenticated user is an author of this release."""
    return bool(
        user is not None
        and getattr(user, 'is_authenticated', False)
        and task_group is not None
        and task_group.authors.filter(user_id=user.pk).exists()
    )


def is_author_auto_completion_active(
    *, user, task_group, play_mode, is_daily_single_task,
    is_official_release, published_at, now,
):
    """Whether the page should expose the personal author auto-credit."""
    return bool(
        is_daily_single_task
        and is_official_release
        and play_mode == 'personal'
        and is_task_author(user, task_group)
        and published_at is not None
        and published_at <= now
    )
