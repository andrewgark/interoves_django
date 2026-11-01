from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from games.identity import ui_notices
from games.models import UiNoticeSeen
from games.site.http.context import ui_notices as ui_notices_context


class UiNoticeSeenEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('notice-user', password='x')
        self.url = reverse('ui_notice_seen')

    def test_signed_in_player_key_is_saved_once(self):
        self.client.force_login(self.user)
        for _ in range(2):
            response = self.client.post(self.url, {'key': 'stuck_nudge.ladder'})
            self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(
            list(UiNoticeSeen.objects.filter(user=self.user).values_list('key', flat=True)),
            ['stuck_nudge.ladder'],
        )

    def test_unknown_key_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {'key': 'whatever'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UiNoticeSeen.objects.exists())

    def test_anonymous_player_keeps_keys_in_the_browser(self):
        response = self.client.post(self.url, {'key': ui_notices.STUCK_NUDGE_OFF})
        self.assertEqual(response.json(), {'status': 'local_only'})
        self.assertFalse(UiNoticeSeen.objects.exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class StuckNudgePartialTests(TestCase):
    def _render(self, user):
        request = RequestFactory().get('/ladder/')
        request.user = user
        context = {'request': request, 'stuck_nudge_game': 'ladder'}
        context.update(ui_notices_context(request))
        return render_to_string('site/partials/stuck_nudge.html', context)

    def test_partial_carries_seen_keys_for_the_player(self):
        user = User.objects.create_user('notice-partial', password='x')
        ui_notices.mark_seen(user, 'stuck_nudge.salad')
        ui_notices.mark_seen(user, ui_notices.STUCK_NUDGE_OFF)
        html = self._render(user)
        self.assertIn('data-seen="stuck_nudge.off stuck_nudge.salad"', html)
        self.assertIn('data-user-id="{}"'.format(user.pk), html)
        self.assertIn("var game = 'ladder';", html)

    def test_anonymous_partial_has_no_server_keys(self):
        from django.contrib.auth.models import AnonymousUser
        html = self._render(AnonymousUser())
        self.assertIn('data-seen=""', html)
        self.assertIn('data-user-id=""', html)
