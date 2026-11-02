"""Context for main UI templates (root URLs)."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from games.ops.analytics.events import (
    consume_pending_goals,
    pending_next_game_vote_payment_goals,
    pending_signup_goals,
    pending_subscription_goals,
    pending_ticket_purchase_goals,
)


def unverified_telegram_prompt(request):
    """Typed Telegram nick that still needs a real login, for the claim popup."""
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return {'unverified_telegram_handle': ''}
    from games.models import Profile
    try:
        profile = user.profile
    except Profile.DoesNotExist:
        return {'unverified_telegram_handle': ''}
    if profile.telegram_verified:
        return {'unverified_telegram_handle': ''}
    handle = str(profile.telegram_handle or '').strip().lstrip('@')
    return {'unverified_telegram_handle': handle}


def ui_notices(request):
    """Notice keys the signed-in player has seen; queried only if a template asks."""
    from django.utils.functional import SimpleLazyObject

    from games.identity.ui_notices import seen_keys
    return {'ui_notices_seen': SimpleLazyObject(lambda: seen_keys(getattr(request, 'user', None)))}


def site_deploy_version(_request):
    """Expose SITE_DEPLOY_VERSION for deploy_version_check.js (HTML vs live API)."""
    v = getattr(settings, "SITE_DEPLOY_VERSION", "") or ""
    return {"site_deploy_version": str(v).strip()}


# A Club offer goes to regulars: players who have finished games on this many
# distinct days. Nearly all paying members played 20+ of the last 30 days.
CLUB_OFFER_MIN_ACTIVE_DAYS = 7
CLUB_OFFER_COOLDOWN_DAYS = 14
SIGNUP_PROMPT_MIN_ACTIVE_DAYS = 3


def club_archive_ui(request):
    """Feature-flagged archive upsell UI and subscriber community link."""
    user = getattr(request, 'user', None)
    if not getattr(settings, 'CLUB_ARCHIVE_GATING_ENABLED', False):
        from games.club.access.archive import has_club_access
        random_access = bool(
            user
            and user.is_authenticated
            and getattr(request, 'path', '').startswith('/alphabetty')
            and has_club_access(user)
        )
        return {
            'club_archive_gating_enabled': False,
            'random_alphabetty_has_access': random_access,
        }
    from games.club.access.archive import has_club_access
    from games.ops.telegram.transport.config import club_invite_url
    if not user or not user.is_authenticated:
        return {
            'club_archive_gating_enabled': True,
            'club_archive_offer_available': False,
            'club_archive_has_access': False,
            'random_alphabetty_has_access': False,
            'club_archive_telegram_url': '',
        }
    club_access = has_club_access(user)
    profile = getattr(user, 'profile', None)
    now = timezone.now()
    offer_snoozed = (
        getattr(profile, 'club_archive_offer_never', False)
        or (
            getattr(profile, 'club_archive_offer_deferred_until', None) is not None
            and profile.club_archive_offer_deferred_until > now
        )
        or (
            getattr(profile, 'club_archive_offer_last_shown_at', None) is not None
            and profile.club_archive_offer_last_shown_at > now - timedelta(days=CLUB_OFFER_COOLDOWN_DAYS)
        )
    )
    context = {
        'club_archive_gating_enabled': True,
        'club_archive_offer_available': False,
        'club_archive_has_access': club_access,
        'random_alphabetty_has_access': club_access,
        'club_archive_telegram_url': club_invite_url() if club_access else '',
    }
    if club_access or offer_snoozed or getattr(request, 'path', '') == '/subscription/':
        return context
    from games.club.metrics import club_member_count
    from games.daily.activity import active_days
    days = active_days(user=user)
    if days >= CLUB_OFFER_MIN_ACTIVE_DAYS:
        context.update({
            'club_archive_offer_available': True,
            'club_offer_active_days': days,
            'club_offer_member_count': club_member_count(),
        })
    return context


def signup_prompt(request):
    """Whether to offer an anonymous regular to sign up and keep their progress.

    Evaluated lazily: only daily game pages include the card.
    """
    from django.utils.functional import SimpleLazyObject

    def prompt():
        user = getattr(request, 'user', None)
        if user is None or user.is_authenticated:
            return {'show': False, 'days': 0}
        from games.daily.activity import active_days
        from games.ops.analytics.identity import gameplay_anon_key
        anon_key = gameplay_anon_key(request)
        days = active_days(anon_key=anon_key) if anon_key else 0
        return {'show': days >= SIGNUP_PROMPT_MIN_ACTIVE_DAYS, 'days': days}

    return {'signup_prompt': SimpleLazyObject(prompt)}


def analytics_bootstrap(request):
    counter_id = getattr(settings, 'YANDEX_METRIKA_COUNTER_ID', 0) or 0
    user = getattr(request, 'user', None)
    goals = (
        consume_pending_goals(request)
        + pending_signup_goals(user)
        + pending_ticket_purchase_goals(user)
        + pending_subscription_goals(user)
        + pending_next_game_vote_payment_goals(user)
    )
    deduped = {}
    for goal in goals:
        if isinstance(goal, dict) and goal.get('goal'):
            deduped[str(goal.get('key') or goal['goal'])] = goal
    return {
        'yandex_metrika_counter_id': int(counter_id),
        'pending_analytics_goals': list(deduped.values()),
    }


def ui_section_games(request):
    match = getattr(request, "resolver_match", None)
    if not match:
        return {}
    url_name = match.url_name or ""
    # Root section result routes are partly generated by a shared regex and
    # therefore do not all have a named URL. They still need the full section
    # navigation in the base template.
    is_results_path = '/results/' in (getattr(request, 'path', '') or '')
    if not (
        url_name.startswith("ui_")
        or url_name.startswith("new_")
        or url_name.startswith("project_")
        or is_results_path
    ):
        return {}
    from games.sections.hub import daily_nav_items
    from games.sections.paths import is_root_section_game
    from games.site.views.pages.ui import get_section_games
    tz = 'Europe/Moscow'
    try:
        profile = getattr(request.user, 'profile', None)
        if profile and getattr(profile, 'timezone', None):
            tz = profile.timezone
    except Exception:
        pass

    kwargs = match.kwargs or {}
    game_id = kwargs.get('game_id')
    nav_desyatochki_active = False
    if url_name in ('ui_folder', 'new_folder') and kwargs.get('slug') == 'games':
        nav_desyatochki_active = True
    elif url_name in (
        'ui_main_game', 'new_main_game',
        'ui_results', 'new_results',
        'ui_tournament_results', 'new_tournament_results',
        'ui_game_progress', 'new_game_progress',
    ):
        nav_desyatochki_active = not game_id or not is_root_section_game(game_id)
    elif url_name in ('ui_task_group', 'new_task_group') and game_id:
        nav_desyatochki_active = not is_root_section_game(game_id)

    return {
        'section_games': get_section_games(request),
        'daily_nav_sections': daily_nav_items(),
        'user_timezone': tz,
        'nav_desyatochki_active': nav_desyatochki_active,
    }


def feedback_nav(request):
    """Unread admin replies for the profile icon badge (not realtime)."""
    match = getattr(request, 'resolver_match', None)
    url_name = (match.url_name or '') if match else ''
    if not (
        url_name.startswith('ui_')
        or url_name.startswith('new_')
        or url_name.startswith('project_')
    ):
        return {}
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return {'has_unread_feedback': False}
    try:
        from games.site.pages.profile.feedback import user_has_unread_feedback

        return {'has_unread_feedback': user_has_unread_feedback(user)}
    except Exception:
        return {'has_unread_feedback': False}


def friends_nav(request):
    """Pending incoming friend requests for the nav badge (cached per user)."""
    match = getattr(request, 'resolver_match', None)
    url_name = (match.url_name or '') if match else ''
    if not (
        url_name.startswith('ui_')
        or url_name.startswith('new_')
        or url_name.startswith('project_')
    ):
        return {}
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return {'friend_requests_count': 0}
    try:
        from games.community.friends import incoming_count

        return {'friend_requests_count': incoming_count(user.pk)}
    except Exception:
        return {'friend_requests_count': 0}
