"""/offline-sw.js: the offline worker, or a worker that removes itself when offline play is off."""
from django.test import SimpleTestCase, override_settings

from games.site.views.pages import offline_sw


class OfflineServiceWorkerViewTests(SimpleTestCase):
    def setUp(self):
        offline_sw._worker_source.cache_clear()

    @override_settings(OFFLINE_PLAY_ENABLED=True, SITE_DEPLOY_VERSION=' 42 ')
    def test_worker_carries_the_deploy_version(self):
        response = self.client.get('/offline-sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript; charset=utf-8')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        body = response.content.decode()
        self.assertIn("var VERSION = '42';", body)
        self.assertIn("self.addEventListener('fetch'", body)
        self.assertNotIn('__DEPLOY_VERSION__', body)

    @override_settings(OFFLINE_PLAY_ENABLED=True, SITE_DEPLOY_VERSION='')
    def test_missing_version_matches_the_template_default(self):
        self.assertIn("var VERSION = '0';", self.client.get('/offline-sw.js').content.decode())

    @override_settings(OFFLINE_PLAY_ENABLED=False)
    def test_kill_switch_serves_a_worker_that_unregisters(self):
        body = self.client.get('/offline-sw.js').content.decode()
        self.assertIn('self.registration.unregister()', body)
        self.assertNotIn("addEventListener('fetch'", body)

    def test_post_is_refused(self):
        self.assertEqual(self.client.post('/offline-sw.js').status_code, 405)
