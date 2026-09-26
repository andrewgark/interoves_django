import json
import os
import tempfile

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


class EbVersionLabelTests(TestCase):
    def test_reads_version_label(self):
        from interoves_django.settings import _eb_version_label

        handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False)
        json.dump({'VersionLabel': 'app-25e4-260925_232016346380'}, handle)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        self.assertEqual(_eb_version_label(handle.name), 'app-25e4-260925_232016346380')

    def test_missing_manifest_is_empty(self):
        from interoves_django.settings import _eb_version_label

        self.assertEqual(_eb_version_label('/tmp/interoves-no-such-manifest.json'), '')
