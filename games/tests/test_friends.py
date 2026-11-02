from allauth.socialaccount.models import SocialApp
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from games.community import friends
from games.models import FriendInvite, Friendship, Profile, Project


class FriendsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        cls.anna = User.objects.create_user('fr_anna', password='secret')
        cls.vera = User.objects.create_user('fr_vera', password='secret')
        cls.boris = User.objects.create_user('fr_boris', password='secret')
        cls.anna_p = Profile.objects.create(user=cls.anna, first_name='Анна', last_name='Петрова')
        cls.vera_p = Profile.objects.create(user=cls.vera, first_name='Вера', last_name='Зорина')
        cls.boris_p = Profile.objects.create(user=cls.boris, first_name='Борис', last_name='Лапин')

    def setUp(self):
        cache.clear()
        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider, defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)

    def client_for(self, user):
        client = Client()
        self.assertTrue(client.login(username=user.username, password='secret'))
        return client


class FriendshipServiceTests(FriendsTestCase):
    def test_request_accept_and_remove(self):
        self.assertEqual(friends.request_friendship(self.anna, self.vera.pk), friends.RELATION_OUTGOING)
        self.assertEqual(friends.relation(self.vera, self.anna.pk), friends.RELATION_INCOMING)
        self.assertEqual(friends.incoming_count(self.vera.pk), 1)
        self.assertEqual(friends.accept_request(self.vera, self.anna.pk), friends.RELATION_FRIENDS)
        self.assertEqual(friends.incoming_count(self.vera.pk), 0)
        self.assertEqual(friends.friend_user_ids(self.anna.pk), {self.vera.pk})
        self.assertTrue(friends.are_friends(self.vera.pk, self.anna.pk))
        friends.remove(self.vera, self.anna.pk)
        self.assertEqual(friends.relation(self.anna, self.vera.pk), friends.RELATION_NONE)

    def test_counter_requests_become_friendship(self):
        friends.request_friendship(self.anna, self.vera.pk)
        self.assertEqual(friends.request_friendship(self.vera, self.anna.pk), friends.RELATION_FRIENDS)
        self.assertEqual(Friendship.objects.count(), 1)

    def test_requester_cannot_accept_own_request_or_befriend_self(self):
        friends.request_friendship(self.anna, self.vera.pk)
        with self.assertRaises(friends.FriendshipError):
            friends.accept_request(self.anna, self.vera.pk)
        with self.assertRaises(friends.FriendshipError):
            friends.request_friendship(self.anna, self.anna.pk)

    def test_pending_outgoing_requests_are_capped(self):
        others = [User.objects.create_user('fr_cap_{}'.format(i)) for i in range(friends.MAX_PENDING_OUTGOING)]
        for other in others:
            friends.request_friendship(self.anna, other.pk)
        with self.assertRaises(friends.FriendshipError):
            friends.request_friendship(self.anna, self.vera.pk)

    def test_invite_link_makes_friends_immediately_and_new_link_revokes_old(self):
        old = friends.new_invite(self.anna)
        new = friends.new_invite(self.anna)
        old.refresh_from_db()
        self.assertIsNotNone(old.revoked_at)
        self.assertIsNone(friends.invite_by_token(old.token))
        friends.request_friendship(self.anna, self.vera.pk)  # pending one way
        self.assertEqual(friends.accept_invite(new, self.vera), friends.RELATION_FRIENDS)
        self.assertTrue(friends.are_friends(self.anna.pk, self.vera.pk))
        self.assertEqual(FriendInvite.objects.get(pk=new.pk).uses_count, 1)
        with self.assertRaises(friends.FriendshipError):
            friends.accept_invite(new, self.anna)


class FriendsViewTests(FriendsTestCase):
    def player_url(self, profile):
        return reverse('new_player_profile', kwargs={'public_id': profile.public_id})

    def post_action(self, client, profile, action, next_url=None):
        return client.post(reverse('new_friends_action'), {
            'player': profile.public_id, 'action': action, 'next': next_url or self.player_url(profile),
        })

    def test_full_request_flow_through_player_pages(self):
        anna, vera = self.client_for(self.anna), self.client_for(self.vera)
        self.assertIn('Добавить в друзья', anna.get(self.player_url(self.vera_p)).content.decode())
        response = self.post_action(anna, self.vera_p, 'request')
        self.assertRedirects(response, self.player_url(self.vera_p), fetch_redirect_response=False)
        self.assertIn('Заявка отправлена', anna.get(self.player_url(self.vera_p)).content.decode())

        friends_page = vera.get(reverse('new_friends')).content.decode()
        self.assertIn('Хотят дружить', friends_page)
        self.assertIn('Анна Петрова', friends_page)
        self.assertIn('new-nav__inline-badge', friends_page)

        self.post_action(vera, self.anna_p, 'accept', '/friends/')
        self.assertIn('В друзьях', anna.get(self.player_url(self.vera_p)).content.decode())
        body = vera.get(reverse('new_friends')).content.decode()
        self.assertIn('1 друг', body)
        self.assertNotIn('new-nav__inline-badge', body)

    def test_friends_see_hidden_page_and_photo_strangers_do_not(self):
        Profile.objects.filter(pk=self.vera_p.pk).update(page_hidden=True, avatar_url='https://example.com/v.jpg')
        friends.request_friendship(self.anna, self.vera.pk)
        friends.accept_request(self.vera, self.anna.pk)
        body = self.client_for(self.anna).get(self.player_url(self.vera_p)).content.decode()
        self.assertNotIn('скрыл свою страницу', body)
        self.assertIn('https://example.com/v.jpg', body)
        body = self.client_for(self.boris).get(self.player_url(self.vera_p)).content.decode()
        self.assertIn('скрыл свою страницу', body)
        self.assertNotIn('https://example.com/v.jpg', body)

    def test_mutual_friends_count(self):
        for a, b in ((self.anna, self.vera), (self.boris, self.vera)):
            friends.request_friendship(a, b.pk)
            friends.accept_request(b, a.pk)
        body = self.client_for(self.boris).get(self.player_url(self.anna_p)).content.decode()
        self.assertIn('1 друг · 1 общих', body)

    def test_unsafe_next_falls_back_to_friends_page(self):
        response = self.post_action(self.client_for(self.anna), self.vera_p, 'request', 'https://evil.example/')
        self.assertEqual(response['Location'], reverse('new_friends'))

    def test_action_requires_login_and_known_player(self):
        response = Client().post(reverse('new_friends_action'), {'player': self.vera_p.public_id, 'action': 'request'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Friendship.objects.exists())
        response = self.client_for(self.anna).post(reverse('new_friends_action'), {'player': 'nope', 'action': 'request'})
        self.assertEqual(response.status_code, 404)

    def test_invite_page_and_accept(self):
        anna = self.client_for(self.anna)
        anna.post(reverse('new_friend_invite_create'))
        invite = FriendInvite.objects.get(user=self.anna, revoked_at__isnull=True)
        self.assertIn(invite.token, anna.get(reverse('new_friends')).content.decode())
        url = reverse('new_friend_invite', kwargs={'token': invite.token})

        anonymous = Client().get(url).content.decode()
        self.assertIn('Войти и подружиться', anonymous)
        self.assertIn('Анна Петрова', anonymous)
        self.assertIn('ваша ссылка', anna.get(url).content.decode())

        vera = self.client_for(self.vera)
        self.assertIn('Подружиться', vera.get(url).content.decode())
        self.assertFalse(Friendship.objects.exists())  # GET never befriends
        response = vera.post(reverse('new_friend_invite_accept', kwargs={'token': invite.token}))
        self.assertRedirects(response, self.player_url(self.anna_p), fetch_redirect_response=False)
        self.assertTrue(friends.are_friends(self.anna.pk, self.vera.pk))

    def test_revoked_invite_is_404(self):
        invite = friends.new_invite(self.anna)
        friends.new_invite(self.anna)
        response = Client().get(reverse('new_friend_invite', kwargs={'token': invite.token}))
        self.assertEqual(response.status_code, 404)
