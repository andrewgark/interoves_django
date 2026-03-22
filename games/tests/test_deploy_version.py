from django.test import TestCase, override_settings


class DeployVersionViewTests(TestCase):
    @override_settings(SITE_DEPLOY_VERSION='')
    def test_empty_version(self):
        r = self.client.get('/meta/deploy-version/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['version'], '')
        self.assertIn('no-store', r['Cache-Control'])

    @override_settings(SITE_DEPLOY_VERSION='abc123')
    def test_returns_version(self):
        r = self.client.get('/meta/deploy-version/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['version'], 'abc123')
