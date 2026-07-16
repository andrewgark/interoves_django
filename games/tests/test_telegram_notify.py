from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings

from games.models import (
    BugReport,
    CheckerType,
    CorporateGameOrder,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
    Team,
    TicketRequest,
)


def _ensure_test_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


@override_settings(
    TELEGRAM_BOT_TOKEN='test-token',
    TELEGRAM_ADMIN_CHAT_ID='123456789',
    TELEGRAM_NOTIFY_CHAT_ID='123456789',
    SITE_BASE_URL='https://interoves.com',
)
class TelegramNotifyTests(TransactionTestCase):
    def setUp(self):
        from games.telegram.config import clear_admin_mute

        clear_admin_mute()
        _ensure_test_fixtures()
        self.game = Game.objects.create(
            id='tg_test_game',
            name='Test game',
            author='a',
            author_extra='',
        )
        self.task_group = TaskGroup.objects.create(label='TG')
        GameTaskGroup.objects.create(
            game=self.game,
            task_group=self.task_group,
            number='1',
            name='Section',
        )
        with patch('games.views.track.track_task_change'):
            self.task = Task.objects.create(
                task_group=self.task_group,
                number='1',
                text='Question?',
                answer='answer',
            )
        self.team = Team.objects.create(name='Team A', visible_name='Team A')

    @patch('games.telegram.api.requests.post')
    def test_bug_report_triggers_telegram(self, mock_post):
        mock_post.return_value.json.return_value = {'ok': True}
        mock_post.return_value.raise_for_status = lambda: None

        report = BugReport.objects.create(
            task=self.task,
            game=self.game,
            team=self.team,
            text='Опечатка в условии',
            status='Pending',
        )

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['chat_id'], '123456789')
        self.assertIn('Новый репорт о баге', payload['text'])
        self.assertIn('Опечатка в условии', payload['text'])
        self.assertIn('/admin/games/bugreport/{}/change/'.format(report.pk), payload['text'])
        self.assertIn('/games/{}/1/#new-task-{}'.format(self.game.id, self.task.pk), payload['text'])
        self.assertIn('/admin/games/task/{}/change/'.format(self.task.pk), payload['text'])
        self.assertIn('/admin/games/taskgroup/{}/change/'.format(self.task_group.pk), payload['text'])
        self.assertIn('/admin/games/game/{}/change/'.format(self.game.id), payload['text'])

    @patch('games.telegram.api.requests.post')
    def test_ticket_request_triggers_telegram(self, mock_post):
        mock_post.return_value.json.return_value = {'ok': True}
        mock_post.return_value.raise_for_status = lambda: None

        ticket_request = TicketRequest.objects.create(
            team=self.team,
            money=4000,
            tickets=2,
            status='Pending',
        )

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs['json']
        self.assertIn('Новая заявка на билеты', payload['text'])
        self.assertIn('Team A', payload['text'])
        self.assertIn(str(ticket_request.pk), payload['text'])

    @patch('games.telegram.api.requests.post')
    def test_corporate_order_triggers_telegram(self, mock_post):
        mock_post.return_value.json.return_value = {'ok': True}
        mock_post.return_value.raise_for_status = lambda: None

        order = CorporateGameOrder.objects.create(
            company_name='Acme',
            contact_name='Bob',
            contact_method='telegram',
            contact_value='@bob',
            message='Need a game',
        )

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs['json']
        self.assertIn('корпоративную игру', payload['text'])
        self.assertIn('Acme', payload['text'])
        self.assertIn('@bob', payload['text'])
        self.assertIn(str(order.pk), payload['text'])

    @override_settings(TELEGRAM_BOT_TOKEN='')
    @patch('games.telegram.api.requests.post')
    def test_skips_when_not_configured(self, mock_post):
        BugReport.objects.create(
            task=self.task,
            game=self.game,
            text='Silent bug',
            status='Pending',
        )
        mock_post.assert_not_called()

    @patch('games.telegram.api.requests.post')
    def test_reviewed_bug_report_does_not_notify(self, mock_post):
        user = User.objects.create_user(username='reporter', password='x')
        BugReport.objects.create(
            task=self.task,
            game=self.game,
            user=user,
            text='Already handled',
            status='Reviewed',
        )
        mock_post.assert_not_called()
