from unittest.mock import patch

from django.test import TestCase

from games.puzzles.censorly.article import reports as word_reports
from games.puzzles.censorly.lexical.match import rootbank
from games.models import BugReport
from games.telegram.shadow import run_admin_alert_live
from games.tests.test_censorly import _make_puzzle_task

_DIAGNOSTIC = {'runtime': 'OPEN', 'classification': 'OPEN', 'recommendation': 'no fix required'}


def _report(**metadata):
    game, task, _share_hash, _puzzle = _make_puzzle_task()
    return BugReport.objects.create(
        report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH,
        game=game,
        task=task,
        text='Жалоба на матчинг слов Цензурки',
        metadata={
            'reason': 'FALSE_POSITIVE',
            'target_surface': 'столица',
            'other_word': 'столичный',
            **metadata,
        },
        status=BugReport.STATUS_PENDING,
    )


class WordReportCreationTests(TestCase):
    def test_pending_report_is_queued_not_notified_from_web(self):
        with patch('games.telegram.signals.notify_new_bug_report') as notify, \
                patch('games.telegram.admin_alert_events.publish_admin_alert',
                      return_value=True) as publish, \
                patch.object(word_reports, 'pair_diagnostic') as diagnostic, \
                self.captureOnCommitCallbacks(execute=True):
            report = _report(diagnostic=word_reports.initial_diagnostic('столичный'))
            word_reports.request_completion(report.pk)

        notify.assert_not_called()
        diagnostic.assert_not_called()
        publish.assert_called_once_with(
            alert=word_reports.ADMIN_ALERT,
            payload={'report_id': report.pk},
            dedupe_key='telegram.admin_alert:censorly_word_report:{}'.format(report.pk),
        )

    def test_report_without_pair_is_notified_immediately(self):
        self.assertIsNone(word_reports.initial_diagnostic(''))
        with patch('games.telegram.signals.notify_new_bug_report') as notify, \
                self.captureOnCommitCallbacks(execute=True):
            _report(other_word='', diagnostic=None)

        notify.assert_called_once()

    def test_unpublishable_report_is_notified_without_diagnostic(self):
        with patch('games.telegram.admin_alert_events.publish_admin_alert', return_value=False), \
                patch('games.telegram.notify.notify_new_bug_report', return_value=True) as notify, \
                patch.object(word_reports, 'pair_diagnostic') as diagnostic, \
                self.captureOnCommitCallbacks(execute=True):
            report = _report(diagnostic=word_reports.initial_diagnostic('столичный'))
            word_reports.request_completion(report.pk)

        report.refresh_from_db()
        self.assertEqual(report.metadata['diagnostic'], {'status': 'unavailable'})
        diagnostic.assert_not_called()
        notify.assert_called_once()


class WordReportCompletionTests(TestCase):
    def test_worker_stores_diagnostic_then_notifies(self):
        report = _report(diagnostic={'status': 'pending'})
        with patch.object(word_reports, 'pair_diagnostic', return_value=_DIAGNOSTIC) as diagnostic, \
                patch('games.telegram.notify.notify_new_bug_report', return_value=True) as notify:
            self.assertTrue(run_admin_alert_live(
                alert=word_reports.ADMIN_ALERT, payload={'report_id': report.pk},
            ))

        diagnostic.assert_called_once_with('столичный', 'столица')
        report.refresh_from_db()
        self.assertEqual(report.metadata['diagnostic'], _DIAGNOSTIC)
        self.assertEqual(report.metadata['reason'], 'FALSE_POSITIVE')
        notified = notify.call_args.args[0]
        self.assertEqual(notified.metadata['diagnostic'], _DIAGNOSTIC)

    def test_failed_delivery_is_retried_without_recomputing(self):
        report = _report(diagnostic={'status': 'pending'})
        with patch.object(word_reports, 'pair_diagnostic', return_value=_DIAGNOSTIC) as diagnostic, \
                patch('games.telegram.notify.notify_new_bug_report',
                      side_effect=[False, True]) as notify:
            self.assertFalse(word_reports.complete_report(report.pk))
            self.assertTrue(word_reports.complete_report(report.pk))

        self.assertEqual(diagnostic.call_count, 1)
        self.assertEqual(notify.call_count, 2)

    def test_missing_report_is_acknowledged(self):
        with patch('games.telegram.notify.notify_new_bug_report') as notify:
            self.assertTrue(word_reports.complete_report(10 ** 9))
        notify.assert_not_called()

    def test_real_pair_diagnostic_releases_the_root_bank(self):
        diagnostic = word_reports.pair_diagnostic('столичный', 'столица')

        self.assertEqual(
            set(diagnostic),
            {'runtime', 'classification', 'recommendation', 'guess_structures',
             'target_structures', 'guess_assignment', 'target_assignment'},
        )
        self.assertEqual(rootbank._bank.cache_info().currsize, 0)
        self.assertEqual(rootbank._diagnostics.cache_info().currsize, 0)

    def test_diagnostic_failure_is_reported_as_unavailable(self):
        with patch('games.puzzles.censorly.lexical.tools.review.explain_pair.explain_pair',
                   side_effect=RuntimeError('boom')), \
                self.assertLogs('application', level='ERROR'):
            self.assertEqual(
                word_reports.pair_diagnostic('столичный', 'столица'),
                {'status': 'unavailable'},
            )
