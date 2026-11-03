"""Template helpers for notifications (see docs/notifications.md)."""

from django import template

register = template.Library()

# «Напомнить о следующей …?» / «Бот пришлёт …»
PROMPT_WORDS = {
    'ladder': ('следующей лесенке', 'её'),
    'salad': ('следующем салатике', 'его'),
    'alphabetty': ('следующей алфавитке', 'её'),
    'censorly': ('следующей цензурке', 'её'),
}


@register.simple_tag(takes_context=True)
def daily_reminder_offer(context, game_id):
    """Offer a Telegram reminder for this daily game, or None when it makes no sense."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not game_id:
        return None
    profile = getattr(user, 'profile', None)
    if profile is None or not profile.telegram_verified or not profile.telegram_user_id:
        return None
    try:
        from games.daily.page.section import MOSCOW
        from games.notifications import daily
        from games.notifications.models import DailyReminder, NotificationPrefs
        from games.notifications.prefs import user_timezone
        from games.site.pages.notifications.pages import _clock_label

        titles = dict(daily.reminder_games())
        if game_id not in titles or game_id not in PROMPT_WORDS:
            return None
        if DailyReminder.objects.filter(user=user, game_id=game_id).exists():
            return None
        if NotificationPrefs.objects.filter(user=user, telegram_blocked_at__isnull=False).exists():
            return None
        return {
            'game_id': game_id,
            'about': PROMPT_WORDS[game_id][0],
            'it': PROMPT_WORDS[game_id][1],
            'clock': _clock_label(daily.channel_clock(game_id), MOSCOW, user_timezone(profile)),
        }
    except Exception:
        return None
