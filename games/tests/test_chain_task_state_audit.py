import io
import re
from unittest import skipUnless

from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from games.management.commands.audit_chain_task_state_completeness import AUDIT_SQL
from games.recheck import recheck_chain_task
from games.tests.test_chain_task_state import (
    _ChainFixture,
    _make_attempt,
    _repl_text,
)
from games.site.views.game.attempt import check_attempt


@skipUnless(connection.vendor == 'mysql', 'the audit SQL is MySQL-specific')
class ChainTaskStateCompletenessAuditTests(_ChainFixture, TestCase):
    def test_read_only_audit_finds_missing_mode_row(self):
        attempt = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(attempt)
        recheck_chain_task(
            self.repl_task, team=self.team, game=self.game, notify=False,
        )

        complete_output = io.StringIO()
        call_command(
            'audit_chain_task_state_completeness', '--execute',
            stdout=complete_output,
        )
        self.assertIn('missing_rows=0', complete_output.getvalue())
        self.assertIn('extra_rows=0', complete_output.getvalue())
        self.assertIn('verdict=COMPLETE', complete_output.getvalue())

        self.repl_task.chain_task_states.filter(game_mode='tournament').delete()
        incomplete_output = io.StringIO()
        call_command(
            'audit_chain_task_state_completeness', '--execute',
            stdout=incomplete_output,
        )
        self.assertIn('missing_rows=1', incomplete_output.getvalue())
        self.assertIn('verdict=INCOMPLETE', incomplete_output.getvalue())
        self.assertEqual(re.findall(r'UPDATE|DELETE|INSERT', AUDIT_SQL, re.I), [])
