from django.test import TestCase


class HealthEndpointTests(TestCase):
    def test_liveness_is_dependency_free_and_not_cached(self):
        response = self.client.get('/health/live/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
        self.assertIn('no-cache', response.headers['Cache-Control'])

    def test_liveness_rejects_post(self):
        self.assertEqual(self.client.post('/health/live/').status_code, 405)
