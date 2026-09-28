import json

from django.test import SimpleTestCase

from interoves_django.runtime_env import RuntimeEnvironmentError, load_runtime_environment


class FakeSecretsManager:
    def __init__(self, payload):
        self.payload = payload

    def get_secret_value(self, *, SecretId):
        self.secret_id = SecretId
        return {'SecretString': json.dumps(self.payload)}


class RuntimeEnvironmentTests(SimpleTestCase):
    def test_secret_values_fill_only_missing_environment_keys(self):
        environ = {
            'INTEROVES_CONFIG_SECRET_ID': 'secret/test',
            'INTEROVES_CONFIG_PROFILE': 'production',
            'EXISTING': 'explicit',
        }
        client = FakeSecretsManager({
            'production': {'EXISTING': 'secret', 'NEW_VALUE': 'loaded'},
        })
        result = load_runtime_environment(environ, client=client)
        self.assertEqual(environ['EXISTING'], 'explicit')
        self.assertEqual(environ['NEW_VALUE'], 'loaded')
        self.assertEqual(result['loaded'], 1)

    def test_flat_secret_is_supported(self):
        environ = {'INTEROVES_CONFIG_SECRET_ID': 'secret/test'}
        load_runtime_environment(environ, client=FakeSecretsManager({'FLAG': True}))
        self.assertEqual(environ['FLAG'], 'True')

    def test_missing_profile_fails_without_revealing_values(self):
        environ = {
            'INTEROVES_CONFIG_SECRET_ID': 'secret/test',
            'INTEROVES_CONFIG_PROFILE': 'game-day',
        }
        with self.assertRaisesRegex(RuntimeEnvironmentError, 'profile game-day is missing'):
            load_runtime_environment(
                environ,
                client=FakeSecretsManager({'production': {'TOKEN': 'do-not-print'}}),
            )

    def test_secret_cannot_replace_process_control_variables(self):
        environ = {'INTEROVES_CONFIG_SECRET_ID': 'secret/test'}
        with self.assertRaises(RuntimeEnvironmentError):
            load_runtime_environment(
                environ,
                client=FakeSecretsManager({'DJANGO_SETTINGS_MODULE': 'unsafe.settings'}),
            )
