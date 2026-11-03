"""Blank production secrets that test settings would otherwise inherit.

settings.py loads the real Telegram bot token and admin chat id from
secrets/, so any test that triggers a notifier without mocking it would post
into the real admin chat.  Tests that exercise Telegram set their own fake
values via override_settings and mock the HTTP layer.
"""

TELEGRAM_BOT_TOKEN = ''
TELEGRAM_ADMIN_CHAT_ID = ''
TELEGRAM_NOTIFY_CHAT_ID = ''
TELEGRAM_ANNOUNCE_CHAT_IDS = []
TELEGRAM_CHANNEL_CHAT_ID = ''
TELEGRAM_WEBHOOK_SECRET = ''
TELEGRAM_API_ID = 0
TELEGRAM_API_HASH = ''
TELEGRAM_USER_SESSION = ''
