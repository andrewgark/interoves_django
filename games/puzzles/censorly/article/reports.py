"""Censorly word-match reports: lexical diagnostic and admin notification.

The pair diagnostic rebuilds the root bank together with its provenance
(~4 s CPU, ~100 MB that would stay cached for the life of the process), so a
web request must never compute it. The web view stores the report with a
pending diagnostic and hands it to the integrations worker, which already
sends admin Telegram messages: it computes the diagnostic, stores it in the
report metadata and only then notifies the admin chat.
"""

from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger('application')

ADMIN_ALERT = 'censorly_word_report'
DIAGNOSTIC_PENDING = 'pending'
DIAGNOSTIC_UNAVAILABLE = 'unavailable'


def diagnostic_is_pending(report) -> bool:
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    diagnostic = metadata.get('diagnostic')
    return isinstance(diagnostic, dict) and diagnostic.get('status') == DIAGNOSTIC_PENDING


def initial_diagnostic(other_word: str) -> dict | None:
    """Metadata value stored with a new report; filled in by the worker."""
    return {'status': DIAGNOSTIC_PENDING} if other_word else None


def request_completion(report_id: int) -> None:
    """After commit, queue the diagnostic; without a queue, notify right away."""
    transaction.on_commit(lambda: _publish_or_notify(report_id))


def _publish_or_notify(report_id: int) -> None:
    from games.telegram.admin_alert_events import publish_admin_alert

    published = publish_admin_alert(
        alert=ADMIN_ALERT,
        payload={'report_id': int(report_id)},
        dedupe_key='telegram.admin_alert:{}:{}'.format(ADMIN_ALERT, report_id),
    )
    if published:
        return
    # The admin still has to hear about the report; it just arrives without
    # the lexical diagnostic, which is never computed in the web process.
    from games.models import BugReport

    report = BugReport.objects.filter(pk=report_id).first()
    if report is None:
        return
    if diagnostic_is_pending(report):
        _store_diagnostic(report, {'status': DIAGNOSTIC_UNAVAILABLE})
    _notify(report)


def complete_report(report_id: int) -> bool:
    """Worker side: compute the pending diagnostic, store it, notify the admin.

    Returns False when the admin message was not delivered, so the message is
    retried. A retry finds the stored diagnostic and only notifies again.
    """
    from games.models import BugReport

    report = BugReport.objects.filter(pk=report_id).first()
    if report is None:
        logger.warning('event=censorly_word_report_missing report_id=%s', report_id)
        return True
    if diagnostic_is_pending(report):
        metadata = report.metadata
        _store_diagnostic(report, pair_diagnostic(
            metadata.get('other_word') or '', metadata.get('target_surface') or '',
        ))
    return _notify(report)


def pair_diagnostic(other_word: str, target_surface: str) -> dict:
    """Lexical explanation of why ``other_word`` did or did not open the target."""
    try:
        from games.censorly.lexical import rootbank
        from games.censorly.lexical.tools.explain_pair import explain_pair

        try:
            explained = explain_pair(other_word, target_surface)
        finally:
            # The worker shares its 1 GiB with Chromium; reports are rare, so
            # rebuilding the bank next time is cheaper than keeping it.
            rootbank.reset_cache()
        return {
            'runtime': explained['runtime_reason'],
            'classification': explained['classification'],
            'recommendation': explained['recommendation'],
            'guess_structures': [list(item) for item in explained['guess']['root_structures']],
            'target_structures': [list(item) for item in explained['target']['root_structures']],
            'guess_assignment': str(explained['guess']['assignment']),
            'target_assignment': str(explained['target']['assignment']),
        }
    except Exception:
        logger.exception(
            'event=censorly_word_report_diagnostic_failed other=%r target=%r',
            other_word, target_surface,
        )
        return {'status': DIAGNOSTIC_UNAVAILABLE}


def _store_diagnostic(report, diagnostic: dict) -> None:
    metadata = dict(report.metadata or {})
    metadata['diagnostic'] = diagnostic
    report.metadata = metadata
    report.save(update_fields=['metadata'])


def _notify(report) -> bool:
    from games.telegram.notify import notify_new_bug_report

    return bool(notify_new_bug_report(report))
