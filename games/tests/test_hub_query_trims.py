from allauth.socialaccount.models import SocialApp
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase, override_settings

from games.identity.allauth import SocialAccountAdapter, clear_social_apps_cache
from games.identity.profile import has_team
from games.models import Profile


class HasTeamOncePerProfileTests(TestCase):
    def test_team_membership_is_repaired_once_per_loaded_profile(self):
        user = User.objects.create_user('team-once')
        Profile.objects.create(user=user)
        user = User.objects.get(pk=user.pk)
        has_team(user)
        with self.assertNumQueries(0):
            self.assertFalse(has_team(user))
            self.assertFalse(has_team(user))


@override_settings(SOCIAL_APPS_CACHE_SECONDS=60)
class SocialAppsCacheTests(TestCase):
    def setUp(self):
        clear_social_apps_cache()
        self.addCleanup(clear_social_apps_cache)
        site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 't'})
        self.app = SocialApp.objects.create(provider='google', name='G', client_id='a', secret='s')
        self.app.sites.add(site)
        self.request = RequestFactory().get('/')

    def test_apps_are_read_once_and_refreshed_after_an_edit(self):
        adapter = SocialAccountAdapter()
        self.assertEqual(len(adapter.list_apps(self.request, provider='google')), 1)
        with self.assertNumQueries(0):
            self.assertEqual(len(adapter.list_apps(self.request, provider='google')), 1)
        self.app.delete()
        self.assertEqual(adapter.list_apps(self.request, provider='google'), [])
