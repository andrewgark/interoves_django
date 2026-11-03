"""Personal notifications: one inbox on the site, optional copies in Telegram.

``service.notify`` is the only entry point for domain code. A notification
stores ids, not text: ``catalog`` renders it when shown or sent, so wording
changes apply retroactively and a cancelled request simply stops rendering.
Telegram copies are delivered by the integrations worker (``telegram``),
which re-reads the database before sending.
"""
