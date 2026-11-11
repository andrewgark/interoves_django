from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from games.models import (
    CheckerType,
    Game,
    HTMLPage,
    PlayerAnalyticsState,
    PlayerStartedGame,
    Project,
    Task,
    TaskGroup,
)
from games.ops.analytics import acquisition
from games.ops.analytics.acquisition import (
    FIRST_TOUCH_COOKIE_NAME,
    bind_first_touch,
    first_touch_from_request,
    release_first_touch,
)
from games.ops.analytics.events import register_started_game
from games.ops.analytics.identity import ANON_COOKIE_NAME, stamp_anon_identity
from games.ops.analytics.persistence import merge_analytics_state_rows
from games.site.http.middleware.anonymous_identity import AnonymousIdentityMiddleware

ANON = '0b4a7f3e-1111-4c2d-9e8f-000000000001'


@override_settings(ALLOWED_HOSTS=['interoves.com', '.interoves.com', 'testserver'])
class FirstTouchFromRequestTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_utm_external_referrer_landing_and_host(self):
        request = self.rf.get(
            '/salad/first/?utm_source=telegram&utm_medium=post&utm_campaign=io-tg-nov26',
            HTTP_REFERER='https://web.telegram.org/k/',
            HTTP_HOST='ru.interoves.com',
        )
        touch = first_touch_from_request(request)
        self.assertEqual(touch['us'], 'telegram')
        self.assertEqual(touch['um'], 'post')
        self.assertEqual(touch['uc'], 'io-tg-nov26')
        self.assertEqual(touch['rh'], 'web.telegram.org')
        self.assertEqual(touch['lp'], '/salad/first/')
        self.assertEqual(touch['sh'], 'ru.interoves.com')
        self.assertIn('at', touch)

    def test_own_site_referrer_is_not_a_source(self):
        request = self.rf.get('/ladder/', HTTP_REFERER='https://ru.interoves.com/', HTTP_HOST='interoves.com')
        touch = first_touch_from_request(request)
        self.assertNotIn('rh', touch)
        self.assertNotIn('us', touch)
        self.assertEqual(touch['sh'], 'interoves.com')


class FirstTouchMiddlewareTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.seen = []

        def view(request):
            self.seen.append(acquisition.current_first_touch())
            return HttpResponse('ok')

        self.middleware = AnonymousIdentityMiddleware(view)

    def test_new_browser_gets_signed_first_touch_cookie(self):
        response = self.middleware(self.rf.get('/?utm_source=yandex&utm_medium=cpc'))
        self.assertIn(FIRST_TOUCH_COOKIE_NAME, response.cookies)
        self.assertTrue(response.cookies[FIRST_TOUCH_COOKIE_NAME]['httponly'])
        self.assertEqual(self.seen[0]['us'], 'yandex')

        # The next request carries the cookie and sees the same touch, not its own.
        follow = self.rf.get('/ladder/?utm_source=other')
        follow.COOKIES[FIRST_TOUCH_COOKIE_NAME] = response.cookies[FIRST_TOUCH_COOKIE_NAME].value
        follow.COOKIES[ANON_COOKIE_NAME] = response.cookies[ANON_COOKIE_NAME].value
        follow.COOKIES['interoves_anon_sig'] = response.cookies['interoves_anon_sig'].value
        second = self.middleware(follow)
        self.assertNotIn(FIRST_TOUCH_COOKIE_NAME, second.cookies)
        self.assertEqual(self.seen[1]['us'], 'yandex')
        self.assertIsNone(acquisition.current_first_touch())

    def test_known_browser_without_cookie_is_not_labelled(self):
        request = self.rf.get('/?utm_source=yandex')
        stamp_anon_identity(request, ANON)
        response = self.middleware(request)
        self.assertNotIn(FIRST_TOUCH_COOKIE_NAME, response.cookies)
        self.assertIsNone(self.seen[0])

    def test_tampered_cookie_is_ignored(self):
        request = self.rf.get('/')
        stamp_anon_identity(request, ANON)
        request.COOKIES[FIRST_TOUCH_COOKIE_NAME] = 'garbage:value'
        self.middleware(request)
        self.assertIsNone(self.seen[0])


class RecordFirstTouchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name)
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        cls.project, _ = Project.objects.get_or_create(id='main')
        cls.game = Game.objects.create(
            id='acq-game', name='acq', author='t', project=cls.project,
            requires_ticket=False, is_tournament=False, is_ready=True,
        )
        cls.user = User.objects.create_user(username='acq-user', password='pw')

    def _task(self, n):
        group = TaskGroup.objects.create(label=f'acq-{n}')
        return Task.objects.create(
            task_group=group, number='1', task_type='default',
            checker_id='equals_with_possible_spaces', answer='X',
        )

    def _start(self, task, touch, **actor):
        token = acquisition._current_touch.set(touch)
        try:
            register_started_game(task=task, game=self.game, **actor)
        finally:
            release_first_touch(token)

    def test_first_start_copies_touch_and_later_starts_keep_it(self):
        self._start(self._task(1), {'us': 'telegram', 'lp': '/salad/first/', 'sh': 'interoves.com', 'at': 1762560000},
                    anon_key=ANON)
        state = PlayerAnalyticsState.objects.get(anon_key=ANON)
        self.assertEqual(state.first_utm_source, 'telegram')
        self.assertEqual(state.first_landing_path, '/salad/first/')
        self.assertEqual(state.first_touch_at, datetime.fromtimestamp(1762560000, tz=dt_timezone.utc))

        self._start(self._task(2), {'us': 'yandex', 'at': 1762570000}, anon_key=ANON)
        state.refresh_from_db()
        self.assertEqual(state.first_utm_source, 'telegram')

    def test_player_with_earlier_start_is_not_relabelled(self):
        self._start(self._task(3), None, user=self.user)
        self._start(self._task(4), {'us': 'yandex', 'at': 1762570000}, user=self.user)
        self.assertFalse(
            PlayerAnalyticsState.objects.filter(user=self.user, first_touch_at__isnull=False).exists()
        )
        self.assertEqual(PlayerStartedGame.objects.filter(user=self.user).count(), 2)

    def test_merge_keeps_earliest_touch(self):
        early = datetime(2026, 11, 1, tzinfo=dt_timezone.utc)
        target = PlayerAnalyticsState.objects.create(
            user=self.user, first_touch_at=early + timedelta(days=3), first_utm_source='yandex',
        )
        source = PlayerAnalyticsState.objects.create(
            anon_key=ANON, first_touch_at=early, first_utm_source='telegram', first_landing_path='/ladder/first/',
        )
        merge_analytics_state_rows(target, source)
        target.refresh_from_db()
        self.assertEqual(target.first_touch_at, early)
        self.assertEqual(target.first_utm_source, 'telegram')
        self.assertEqual(target.first_landing_path, '/ladder/first/')

    def test_bind_without_cookie_for_known_browser_is_none(self):
        request = RequestFactory().get('/?utm_source=x')
        token = bind_first_touch(request, new_browser=False)
        try:
            self.assertIsNone(acquisition.current_first_touch())
        finally:
            release_first_touch(token)
