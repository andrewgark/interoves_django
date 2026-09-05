import json
import re
from datetime import timedelta
from pathlib import Path
from threading import Thread
from unittest import SkipTest, skipUnless
from unittest.mock import patch

from channels.testing import ChannelsLiveServerTestCase
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import close_old_connections
from django.test import Client, override_settings
from django.utils import timezone

from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.support.constants import SUPPORT_CONSOLE_GROUP


LONG_LADDER = {
    'lengths': [3, 3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D', '____ E'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'],
}


@override_settings(
    TRACK_WS_IDLE_TIMEOUT=0,
)
@skipUnless(
    getattr(settings, 'INTEROVES_LIVE_BROWSER_TESTS', False),
    'run with --settings=interoves_django.test_live_settings',
)
class RealtimeTwoBrowserTests(ChannelsLiveServerTestCase):
    """Real HTTP + WebSocket convergence in two isolated browser contexts."""

    def start_browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise SkipTest('Playwright Python package is not installed') from exc
        self.playwright = sync_playwright().start()
        if not Path(self.playwright.chromium.executable_path).exists():
            self.playwright.stop()
            self.playwright = None
            raise SkipTest('Playwright Chromium is not installed')
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
        except Exception:
            self.playwright.stop()
            self.playwright = None
            raise
        self.browser_contexts = []

    def tearDown(self):
        for context in getattr(self, 'browser_contexts', []):
            context.close()
        browser = getattr(self, 'browser', None)
        if browser is not None:
            browser.close()
        playwright = getattr(self, 'playwright', None)
        if playwright is not None:
            playwright.stop()

    def setUp(self):
        Project.objects.get_or_create(pk='main', defaults={})
        equals, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        raddle, _ = CheckerType.objects.get_or_create(pk='raddle')
        replacements, _ = CheckerType.objects.get_or_create(pk='replacements_lines')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

        now = timezone.now()
        self.game = Game.objects.create(
            id='realtime_browser',
            name='Realtime browser',
            author='test',
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
        )
        self.pending_game = Game.objects.create(
            id='realtime_browser_pending',
            name='Realtime browser pending',
            author='test',
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        self.clock_game = Game.objects.create(
            id='realtime_browser_clock',
            name='Realtime browser clock',
            author='test',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            is_registrable=False,
            requires_ticket=False,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
        )
        ordinary_group = TaskGroup.objects.create(label='Browser ordinary')
        raddle_group = TaskGroup.objects.create(label='Browser raddle')
        replacements_group = TaskGroup.objects.create(label='Browser replacements')
        pending_group = TaskGroup.objects.create(label='Browser pending')
        clock_group = TaskGroup.objects.create(label='Browser clock')
        GameTaskGroup.objects.create(game=self.game, task_group=ordinary_group, number=1)
        GameTaskGroup.objects.create(game=self.game, task_group=raddle_group, number=2)
        GameTaskGroup.objects.create(game=self.game, task_group=replacements_group, number=3)
        GameTaskGroup.objects.create(
            game=self.pending_game, task_group=pending_group, number=1,
        )
        GameTaskGroup.objects.create(game=self.clock_game, task_group=clock_group, number=1)
        with patch('games.views.track.track_task_change'):
            self.ordinary_task = Task.objects.create(
                task_group=ordinary_group,
                number='1',
                text='Browser ordinary task',
                answer='RIGHT',
                checker=equals,
                points=1,
            )
            self.raddle_task = Task.objects.create(
                task_group=raddle_group,
                number='1',
                task_type='raddle',
                checker=raddle,
                checker_data=json.dumps(LONG_LADDER),
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
                points=1,
            )
            self.replacements_task = Task.objects.create(
                task_group=replacements_group,
                number='1',
                task_type='replacements_lines',
                text='FOO\nBAR',
                checker=replacements,
                checker_data=json.dumps({'lines': [['FOO'], ['BAR']]}),
                points=2,
            )
            self.pending_task = Task.objects.create(
                task_group=pending_group,
                number='1',
                text='Browser pending task',
                answer='RIGHT',
                checker=equals,
                points=1,
            )
            self.clock_task = Task.objects.create(
                task_group=clock_group,
                number='1',
                text='Browser clock task',
                answer='RIGHT',
                checker=equals,
                points=1,
            )

        self.team = Team.objects.create(name='realtime_browser_team')
        self.user_one = User.objects.create_user('browser_one', password='pw')
        self.user_two = User.objects.create_user('browser_two', password='pw')
        self.staff = User.objects.create_superuser(
            'browser_staff', 'browser_staff@example.com', 'pw',
        )
        Profile.objects.create(user=self.user_one, team_on=self.team)
        Profile.objects.create(user=self.user_two, team_on=self.team)
        Profile.objects.create(user=self.staff)
        support_group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        support_group.user_set.add(self.staff)
        self.session_ids = {}
        for user in (self.user_one, self.user_two, self.staff):
            client = Client()
            client.force_login(user)
            self.session_ids[user.pk] = client.cookies['sessionid'].value
        self.start_browser()

    def browser_page_for(self, user):
        context = self.browser.new_context()
        self.browser_contexts.append(context)
        context.add_init_script(
            "window.__interovesDocumentId = String(Date.now()) + ':' + String(Math.random());"
            "try { localStorage.setItem('interoves_repl_line_confirm_skip', '1'); } catch (e) {}"
        )
        context.add_cookies([{
            'name': 'sessionid',
            'value': self.session_ids[user.pk],
            'url': self.live_server_url,
        }])
        page = context.new_page()
        return page

    def move_clock_game_boundary_near_now(self, boundary):
        """Move a clock boundary after Playwright starts, using a sync-safe DB thread."""
        clock_now = timezone.now()
        errors = []

        if boundary == 'start':
            start_time = clock_now + timedelta(seconds=6)
            end_time = clock_now + timedelta(hours=1)
        elif boundary == 'end':
            start_time = clock_now - timedelta(hours=1)
            end_time = clock_now + timedelta(seconds=6)
        else:  # pragma: no cover - test helper contract
            raise ValueError('Unsupported clock boundary')

        def update_clock_game():
            close_old_connections()
            try:
                Game.objects.filter(pk=self.clock_game.pk).update(
                    start_time=start_time,
                    end_time=end_time,
                )
            except Exception as exc:  # pragma: no cover - surfaced in the test thread
                errors.append(exc)
            finally:
                close_old_connections()

        thread = Thread(target=update_clock_game)
        thread.start()
        thread.join()
        if errors:
            raise errors[0]

    def open_pending_attempt(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.pending_game.pk}/1/'
        second.goto(url)
        first.goto(url)
        first_document_id = first.evaluate('window.__interovesDocumentId')
        second_document_id = second.evaluate('window.__interovesDocumentId')

        first.locator(
            f'form.new-attempt-form[data-task-id="{self.pending_task.pk}"] input[name="text"]'
        ).fill('WRONG')
        first.locator(
            f'form.new-attempt-form[data-task-id="{self.pending_task.pk}"] button[type="submit"]'
        ).click()
        for page in (first, second):
            expect(page.locator(
                f'#new-task-{self.pending_task.pk} [data-attempt-mark="pending"]'
            )).to_have_count(1, timeout=10_000)
        return first, second, first_document_id, second_document_id

    def test_teammate_solves_ordinary_task_without_reload(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/1/'
        second.goto(url)
        first.goto(url)
        second_document_id = second.evaluate('window.__interovesDocumentId')

        second_card = second.locator(f'#new-task-{self.ordinary_task.pk}')
        expect(second_card).to_have_attribute('data-solved', '0')
        first.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] input[name="text"]'
        ).fill('RIGHT')
        first.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] button[type="submit"]'
        ).click()

        expect(first.locator(f'#new-task-{self.ordinary_task.pk}')).to_have_attribute(
            'data-solved', '1', timeout=10_000,
        )
        expect(second_card).to_have_attribute('data-solved', '1', timeout=10_000)
        self.assertEqual(
            second.evaluate('window.__interovesDocumentId'),
            second_document_id,
        )

    def test_simultaneous_same_answer_converges_to_one_attempt(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/1/'
        first.goto(url)
        second.goto(url)
        first_document_id = first.evaluate('window.__interovesDocumentId')
        second_document_id = second.evaluate('window.__interovesDocumentId')
        selector = (
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"]'
        )
        first.locator(f'{selector} input[name="text"]').fill('RIGHT')
        second.locator(f'{selector} input[name="text"]').fill('RIGHT')

        target_ms = first.evaluate('Date.now() + 1000')
        submit_at = """({selector, target}) => {
            const form = document.querySelector(selector);
            setTimeout(() => form.requestSubmit(), Math.max(0, target - Date.now()));
        }"""
        first.evaluate(submit_at, {'selector': selector, 'target': target_ms})
        second.evaluate(submit_at, {'selector': selector, 'target': target_ms})

        for page in (first, second):
            card = page.locator(f'#new-task-{self.ordinary_task.pk}')
            expect(card).to_have_attribute('data-solved', '1', timeout=12_000)
            expect(card.locator('.new-attempts__row')).to_have_count(1)
        self.assertEqual(first.evaluate('window.__interovesDocumentId'), first_document_id)
        self.assertEqual(second.evaluate('window.__interovesDocumentId'), second_document_id)

    def test_visible_team_rename_keeps_open_socket_subscription(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/1/'
        first.goto(url)
        second.goto(url)
        first_document_id = first.evaluate('window.__interovesDocumentId')
        second_document_id = second.evaluate('window.__interovesDocumentId')

        team_page = first.context.new_page()
        team_page.goto(f'{self.live_server_url}/team/')
        team_page.locator('[data-team-rename-open]').click()
        team_page.locator('form[data-team-rename-form] input[name="visible_name"]').fill(
            'Renamed browser team'
        )
        team_page.locator('form[data-team-rename-form] button[type="submit"]').click()
        expect(team_page.get_by_text('Renamed browser team', exact=True).first).to_be_visible()

        second.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] input[name="text"]'
        ).fill('RIGHT')
        second.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] button[type="submit"]'
        ).click()
        for page in (first, second):
            expect(page.locator(f'#new-task-{self.ordinary_task.pk}')).to_have_attribute(
                'data-solved', '1', timeout=10_000,
            )
        self.assertEqual(first.evaluate('window.__interovesDocumentId'), first_document_id)
        self.assertEqual(second.evaluate('window.__interovesDocumentId'), second_document_id)

    def test_task_edit_and_recheck_update_both_open_team_pages(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/1/'
        first.goto(url)
        second.goto(url)
        first_document_id = first.evaluate('window.__interovesDocumentId')
        second_document_id = second.evaluate('window.__interovesDocumentId')

        first.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] input[name="text"]'
        ).fill('FIXED')
        first.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] button[type="submit"]'
        ).click()
        for page in (first, second):
            expect(page.locator(
                f'#new-task-{self.ordinary_task.pk} [data-attempt-mark="wrong"]'
            )).to_have_count(1, timeout=10_000)

        staff = self.browser_page_for(self.staff)
        staff.goto(
            f'{self.live_server_url}/admin/games/task/{self.ordinary_task.pk}/change/'
        )
        staff.locator('#id_text').fill('Browser corrected task text')
        staff.locator('#id_answer').fill('FIXED')
        staff.locator('input[name="_save"]').click()

        for page in (first, second):
            expect(page.locator(f'#new-task-{self.ordinary_task.pk}')).to_contain_text(
                'Browser corrected task text', timeout=10_000,
            )

        staff.goto(f'{self.live_server_url}/support/actor/team/{self.team.pk}/')
        staff.locator(
            'form:has(input[name="action"][value="recheck"]) button[type="submit"]'
        ).first.click()

        for page in (first, second):
            card = page.locator(f'#new-task-{self.ordinary_task.pk}')
            expect(card).to_have_attribute('data-solved', '1', timeout=10_000)
            expect(card.locator('[data-attempt-mark="ok"]')).to_have_count(1)
        self.assertEqual(first.evaluate('window.__interovesDocumentId'), first_document_id)
        self.assertEqual(second.evaluate('window.__interovesDocumentId'), second_document_id)

    def test_reconnect_snapshot_repairs_attempt_missed_while_offline(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/1/'
        second.goto(url)
        first.goto(url)
        second_document_id = second.evaluate('window.__interovesDocumentId')
        second_card = second.locator(f'#new-task-{self.ordinary_task.pk}')
        expect(second_card).to_have_attribute('data-solved', '0')

        second.context.set_offline(True)
        first.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] input[name="text"]'
        ).fill('RIGHT')
        first.locator(
            f'form.new-attempt-form[data-task-id="{self.ordinary_task.pk}"] button[type="submit"]'
        ).click()
        expect(first.locator(f'#new-task-{self.ordinary_task.pk}')).to_have_attribute(
            'data-solved', '1', timeout=10_000,
        )
        expect(second_card).to_have_attribute('data-solved', '0')

        second.context.set_offline(False)
        expect(second_card).to_have_attribute('data-solved', '1', timeout=15_000)
        self.assertEqual(
            second.evaluate('window.__interovesDocumentId'),
            second_document_id,
        )

    def test_raddle_remote_advance_preserves_active_draft_and_focus(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/2/'
        second.goto(url)
        first.goto(url)
        second_document_id = second.evaluate('window.__interovesDocumentId')

        draft = second.locator(
            '.new-raddle-row[data-word-index="2"]:not([data-raddle-pin-row]) '
            'input[data-raddle-draft="1"]'
        )
        expect(draft).to_be_visible()
        draft.click()
        draft.type('CCC')
        expect(draft).to_have_value('CCC')

        first.locator(
            '.new-raddle-row[data-word-index="1"]:not([data-raddle-pin-row]) '
            'input.new-raddle-input:not([data-raddle-pin-proxy])'
        ).fill('BBB')
        expect(first.locator(
            '.new-raddle-row[data-word-index="1"]:not([data-raddle-pin-row])'
        )).to_have_class(re.compile(r'.*new-raddle-row--solved.*'), timeout=10_000)

        promoted = second.locator(
            '.new-raddle-row[data-word-index="2"]:not([data-raddle-pin-row]) '
            'input.new-raddle-input:not([data-raddle-pin-proxy])'
        )
        expect(second.locator(
            '.new-raddle-row[data-word-index="1"]:not([data-raddle-pin-row])'
        )).to_have_class(re.compile(r'.*new-raddle-row--solved.*'), timeout=10_000)
        expect(promoted).to_have_value('CCC')
        expect(promoted).to_be_focused()
        self.assertEqual(
            second.evaluate('window.__interovesDocumentId'),
            second_document_id,
        )

    def test_teammate_can_submit_next_replacements_line_after_live_update(self):
        from playwright.sync_api import expect

        first = self.browser_page_for(self.user_one)
        second = self.browser_page_for(self.user_two)
        url = f'{self.live_server_url}/games/{self.game.pk}/3/'
        second.goto(url)
        first.goto(url)
        first_document_id = first.evaluate('window.__interovesDocumentId')
        second_document_id = second.evaluate('window.__interovesDocumentId')
        card = f'#new-task-{self.replacements_task.pk}'
        first_row = f'{card} tr.new-replacements-row[data-line-index="0"]'
        second_row = f'{card} tr.new-replacements-row[data-line-index="1"]'

        first.locator(f'{first_row} input.new-replacements-input').fill('FOO')
        first.locator(f'{first_row} button[type="submit"]').click()
        for page in (first, second):
            expect(page.locator(first_row)).to_have_class(
                re.compile(r'.*new-replacements-row--solved.*'), timeout=10_000,
            )

        second.locator(f'{second_row} input.new-replacements-input').fill('BAR')
        second.locator(f'{second_row} button[type="submit"]').click()
        for page in (first, second):
            expect(page.locator(second_row)).to_have_class(
                re.compile(r'.*new-replacements-row--solved.*'), timeout=10_000,
            )
            expect(page.get_by_text('Ошибка сети')).to_have_count(0)
        self.assertEqual(first.evaluate('window.__interovesDocumentId'), first_document_id)
        self.assertEqual(second.evaluate('window.__interovesDocumentId'), second_document_id)

    def test_pending_set_ok_updates_both_team_pages(self):
        from playwright.sync_api import expect

        first, second, first_document_id, second_document_id = self.open_pending_attempt()
        staff = self.browser_page_for(self.staff)
        staff.goto(f'{self.live_server_url}/support/pending/')
        staff.locator(
            'form:has(input[name="action"][value="set_ok"]) button[type="submit"]'
        ).first.click()

        for page in (first, second):
            card = page.locator(f'#new-task-{self.pending_task.pk}')
            expect(card).to_have_attribute('data-solved', '1', timeout=10_000)
            expect(card.locator('[data-attempt-mark="ok"]')).to_have_count(1)
        self.assertEqual(first.evaluate('window.__interovesDocumentId'), first_document_id)
        self.assertEqual(second.evaluate('window.__interovesDocumentId'), second_document_id)

    def test_pending_confirm_wrong_updates_both_team_pages(self):
        from playwright.sync_api import expect

        first, second, first_document_id, second_document_id = self.open_pending_attempt()
        staff = self.browser_page_for(self.staff)
        staff.goto(f'{self.live_server_url}/support/pending/')
        staff.locator(
            'form:has(input[name="action"][value="confirm_prestatus"]) button[type="submit"]'
        ).first.click()

        for page in (first, second):
            card = page.locator(f'#new-task-{self.pending_task.pk}')
            expect(card).to_have_attribute('data-solved', '0', timeout=10_000)
            expect(card.locator('[data-attempt-mark="wrong"]')).to_have_count(1)
            expect(card.locator('[data-attempt-mark="pending"]')).to_have_count(0)
        self.assertEqual(first.evaluate('window.__interovesDocumentId'), first_document_id)
        self.assertEqual(second.evaluate('window.__interovesDocumentId'), second_document_id)

    def test_game_start_boundary_opens_task_without_manual_refresh(self):
        from playwright.sync_api import expect

        self.move_clock_game_boundary_near_now('start')
        page = self.browser_page_for(self.user_one)
        url = f'{self.live_server_url}/games/{self.clock_game.pk}/1/'
        page.goto(url)
        document_id = page.evaluate('window.__interovesDocumentId')
        expect(page.locator(f'#new-task-{self.clock_task.pk}')).to_have_count(0)

        expect(page.locator(f'#new-task-{self.clock_task.pk}')).to_have_count(
            1, timeout=12_000,
        )
        self.assertNotEqual(page.evaluate('window.__interovesDocumentId'), document_id)

    def test_game_end_boundary_exposes_post_game_ui_without_manual_refresh(self):
        from playwright.sync_api import expect

        self.move_clock_game_boundary_near_now('end')
        page = self.browser_page_for(self.user_one)
        url = f'{self.live_server_url}/games/{self.clock_game.pk}/1/'
        page.goto(url)
        document_id = page.evaluate('window.__interovesDocumentId')
        card = page.locator(f'#new-task-{self.clock_task.pk}')
        expect(card).to_have_count(1)
        expect(card.locator('[data-answer-open]')).to_have_count(0)

        expect(card.locator('[data-answer-open]')).to_have_count(1, timeout=12_000)
        self.assertNotEqual(page.evaluate('window.__interovesDocumentId'), document_id)
