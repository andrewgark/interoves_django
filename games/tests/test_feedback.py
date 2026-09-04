"""Кабинет обращений: список, права, тред, атрибуция user в команде."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from games.feedback import add_admin_reply, add_user_reply
from games.models import (
    AlphabettyOffer,
    BugReport,
    BugReportMessage,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    LadderOffer,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.support.constants import SUPPORT_CONSOLE_GROUP


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


def _ensure_social_apps():
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    site = Site.objects.get_current()
    for provider in ('google', 'vk', 'yandex'):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


def _make_playable_game(game_id='fb_game'):
    now = timezone.now()
    game = Game.objects.create(
        id=game_id,
        name='Feedback Game',
        author='a',
        author_extra='',
        project_id='sections',
        is_ready=True,
        is_playable=True,
        is_tournament=False,
        requires_ticket=False,
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=1),
    )
    task_group = TaskGroup.objects.create(label='fb tg')
    task = Task.objects.create(task_group=task_group, number='7', text='task text')
    GameTaskGroup.objects.create(game=game, task_group=task_group, number='7', name='Seven')
    return game, task


class FeedbackCabinetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        _ensure_social_apps()
        cls.game, cls.task = _make_playable_game()
        cls.user = User.objects.create_user('fb-user', 'fb@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='Федя', last_name='Багов')
        cls.other = User.objects.create_user('fb-other', 'other@example.com', 'secret')
        Profile.objects.create(user=cls.other, first_name='Оля', last_name='Чужая')
        cls.own = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.user,
            text='Моя опечатка в условии',
            status='Pending',
        )
        cls.foreign = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.other,
            text='Чужой репорт',
            status='Reviewed',
        )
        cls.reviewed = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.user,
            text='Уже посмотрели',
            status='Reviewed',
        )
        cls.dismissed = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.user,
            text='Отклонили',
            status='Dismissed',
        )
        cls.fixed = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.user,
            text='Уже починили',
            status='Fixed',
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_list_shows_own_reports_with_russian_status(self):
        response = self.client.get(reverse('new_profile_reports'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Моя опечатка в условии')
        self.assertContains(response, 'На рассмотрении')
        self.assertContains(response, 'Просмотрен')
        self.assertContains(response, 'Отклонён')
        self.assertContains(response, 'Исправлен')
        self.assertContains(response, 'Список багрепортов, отправленных вами')
        self.assertNotContains(response, 'Чужой репорт')
        self.assertContains(response, reverse('new_profile_report_detail', args=[self.own.pk]))

    def test_list_hides_foreign_reports_even_by_direct_url(self):
        response = self.client.get(reverse('new_profile_report_detail', args=[self.foreign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_profile_has_cabinet_links(self):
        response = self.client.get(reverse('new_profile'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Мои материалы и обращения')
        self.assertNotContains(response, 'багрепорты и ответы')
        self.assertNotContains(response, 'черновики и статусы')
        self.assertContains(response, 'class="new-cabinet"')
        self.assertContains(response, reverse('new_profile_reports'))
        self.assertContains(response, 'Мои обращения')
        self.assertNotContains(response, 'Мои лесенки')
        self.assertNotContains(response, 'Мои алфавитки')
        self.assertNotContains(response, '/create_ladder/')
        self.assertNotContains(response, '/create_alphabetty/')

    def test_profile_hides_cabinet_when_empty(self):
        user = User.objects.create_user('empty-cab', 'empty-cab@example.com', 'secret')
        Profile.objects.create(user=user, first_name='Э', last_name='Пустов')
        client = Client()
        client.force_login(user)
        response = client.get(reverse('new_profile'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="new-cabinet"')
        self.assertNotContains(response, 'Мои лесенки')
        self.assertNotContains(response, 'Мои алфавитки')

    def test_profile_shows_offer_links_when_content_exists(self):
        LadderOffer.objects.create(
            user=self.user,
            share_hash='cabinetladderhash1',
            task_group=TaskGroup.objects.create(label='cabinet-ladder'),
        )
        AlphabettyOffer.objects.create(
            user=self.user,
            share_hash='cabinetabchash1',
            task_group=TaskGroup.objects.create(label='cabinet-alphabetty'),
        )
        response = self.client.get(reverse('new_profile'))
        self.assertContains(response, 'Мои лесенки')
        self.assertContains(response, '/create_ladder/')
        self.assertContains(response, 'Мои алфавитки')
        self.assertContains(response, '/create_alphabetty/')
        self.assertNotContains(response, 'черновики и статусы')

    def test_guest_is_redirected_from_list(self):
        guest = Client()
        response = guest.get(reverse('new_profile_reports'))
        self.assertEqual(response.status_code, 302)

    def test_user_can_reply_while_open(self):
        response = self.client.post(
            reverse('new_profile_report_detail', args=[self.own.pk]),
            {'text': 'Дополнил: скрин во вложении нет, но вот детали'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            BugReportMessage.objects.filter(
                report=self.own,
                author_role=BugReportMessage.ROLE_USER,
                text__contains='Дополнил',
            ).exists()
        )

    def test_user_cannot_reply_when_dismissed(self):
        before = BugReportMessage.objects.filter(report=self.dismissed).count()
        response = self.client.post(
            reverse('new_profile_report_detail', args=[self.dismissed.pk]),
            {'text': 'Попытка после закрытия'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BugReportMessage.objects.filter(report=self.dismissed).count(), before)
        self.assertContains(response, 'закрыто')

    def test_user_cannot_reply_when_fixed(self):
        before = BugReportMessage.objects.filter(report=self.fixed).count()
        response = self.client.post(
            reverse('new_profile_report_detail', args=[self.fixed.pk]),
            {'text': 'Попытка после исправления'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BugReportMessage.objects.filter(report=self.fixed).count(), before)
        self.assertContains(response, 'закрыто')
        self.assertContains(response, 'Исправлен')

    def test_fixed_status_uses_ok_pill(self):
        self.assertEqual(self.fixed.status_label_ru(), 'Исправлен')
        self.assertEqual(self.fixed.status_pill_class(), 'new-pill--ok')
        response = self.client.get(reverse('new_profile_report_detail', args=[self.fixed.pk]))
        self.assertContains(response, 'new-pill--ok')

    def test_admin_notes_are_not_shown(self):
        self.own.admin_notes = 'секретная заметка админа'
        self.own.save(update_fields=['admin_notes'])
        response = self.client.get(reverse('new_profile_report_detail', args=[self.own.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'секретная заметка админа')

    def test_opening_message_created_with_report(self):
        self.assertEqual(
            BugReportMessage.objects.filter(report=self.own, author_role='user').count(),
            1,
        )
        self.assertEqual(
            BugReportMessage.objects.get(report=self.own).text,
            'Моя опечатка в условии',
        )

    def test_project_scoped_reports_url(self):
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        response = self.client.get('/glowbyte/profile/reports/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Моя опечатка в условии')


class FeedbackBugReportSubmitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.game, cls.task = _make_playable_game('fb_submit')
        cls.user = User.objects.create_user('fb-sub', 'sub@example.com', 'secret')
        cls.team = Team.objects.create(name='fb_team', visible_name='FB Team')
        Profile.objects.create(
            user=cls.user, first_name='Саша', last_name='Репорт', team_on=cls.team,
        )

    def test_logged_in_personal_report_sets_user(self):
        client = Client()
        client.force_login(self.user)
        response = client.post(
            reverse('new_bug_report', args=[self.task.pk]),
            {'text': 'Сломалась проверка', 'game_id': self.game.id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        report = BugReport.objects.get(pk=payload['report_id'])
        self.assertEqual(report.user, self.user)
        self.assertIsNone(report.team_id)
        self.assertIn('/profile/reports/{}/'.format(report.pk), payload['report_url'])

    def test_team_mode_still_sets_user(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['play_mode_sections'] = 'team'
        session.save()
        response = client.post(
            reverse('new_bug_report', args=[self.task.pk]),
            {'text': 'Командный баг', 'game_id': self.game.id},
        )
        self.assertEqual(response.status_code, 200)
        report = BugReport.objects.get(pk=response.json()['report_id'])
        self.assertEqual(report.user, self.user)
        self.assertEqual(report.team, self.team)
        listed = client.get(reverse('new_profile_reports'))
        self.assertContains(listed, 'Командный баг')


class FeedbackNotifyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.game, cls.task = _make_playable_game('fb_notify')
        cls.user = User.objects.create_user('fb-tg', 'tg@example.com', 'secret')
        Profile.objects.create(
            user=cls.user,
            first_name='Тима',
            last_name='Грам',
            telegram_user_id=777001,
            telegram_verified=True,
        )
        cls.report = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.user,
            text='Первое сообщение',
            status='Reviewed',
        )

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_ADMIN_CHAT_ID='12345',
        SITE_BASE_URL='https://interoves.com',
    )
    @patch('games.telegram.notify.send_message')
    def test_user_reply_pings_admin_not_as_new_report(self, send_mock):
        send_mock.return_value = True
        send_mock.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            add_user_reply(self.report, self.user, 'Ещё подробности')
        self.assertEqual(send_mock.call_count, 1)
        text = send_mock.call_args.args[1]
        self.assertIn('Ответ пользователя', text)
        self.assertNotIn('Новый репорт о баге', text)
        self.assertIn('/profile/reports/{}/'.format(self.report.pk), text)
        self.assertIsNone(send_mock.call_args.kwargs.get('reply_markup'))

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_ADMIN_CHAT_ID='12345',
        SITE_BASE_URL='https://interoves.com',
    )
    @patch('games.telegram.notify.send_message')
    def test_admin_reply_dms_user_with_telegram_id(self, send_mock):
        send_mock.return_value = True
        send_mock.reset_mock()
        admin = User.objects.create_user('fb-admin', password='x')
        with self.captureOnCommitCallbacks(execute=True):
            add_admin_reply(self.report, admin, 'Исправим в ближайшем выпуске')
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], 777001)
        text = send_mock.call_args.args[1]
        self.assertIn('Ответ по вашему сообщению', text)
        self.assertIn('Исправим в ближайшем выпуске', text)

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_ADMIN_CHAT_ID='12345',
        SITE_BASE_URL='https://interoves.com',
    )
    @patch('games.telegram.notify.send_message')
    def test_admin_reply_skips_dm_without_telegram_id(self, send_mock):
        self.user.profile.telegram_user_id = None
        self.user.profile.save(update_fields=['telegram_user_id'])
        send_mock.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            add_admin_reply(self.report, None, 'Ответ только на сайте')
        send_mock.assert_not_called()
        self.assertTrue(
            BugReportMessage.objects.filter(
                report=self.report, author_role='admin', text='Ответ только на сайте',
            ).exists()
        )


class FeedbackSupportReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        cls.game, cls.task = _make_playable_game('fb_support')
        cls.staff = User.objects.create_user('fb-staff', password='secret', is_staff=True)
        Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)[0].user_set.add(cls.staff)
        cls.player = User.objects.create_user('fb-player', password='x')
        Profile.objects.create(user=cls.player, first_name='Игрок', last_name='Сайт')
        cls.bug = BugReport.objects.create(
            task=cls.task,
            game=cls.game,
            user=cls.player,
            text='что-то сломалось',
            status='Pending',
        )

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='fb-staff', password='secret'))

    def test_pending_has_reply_field(self):
        response = self.client.get(reverse('support:pending'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Баг #{}'.format(self.bug.pk))
        self.assertContains(response, 'name="reply_text"')
        self.assertContains(response, '/profile/reports/{}/'.format(self.bug.pk))

    def test_bug_reply_action_creates_public_message(self):
        response = self.client.post(reverse('support:action'), {
            'kind': 'bug',
            'id': self.bug.pk,
            'action': 'bug_reply',
            'reply_text': 'Спасибо, поправим',
            'next': reverse('support:pending'),
        })
        self.assertEqual(response.status_code, 302)
        self.bug.refresh_from_db()
        self.assertEqual(self.bug.status, 'Pending')
        msg = BugReportMessage.objects.get(report=self.bug, author_role='admin')
        self.assertEqual(msg.text, 'Спасибо, поправим')
        self.assertEqual(msg.author_user, self.staff)
        self.assertNotEqual(msg.text, self.bug.admin_notes)
