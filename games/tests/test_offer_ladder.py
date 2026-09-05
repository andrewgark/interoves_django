"""Tests for user ladder offers (/create_ladder, share hash, accept)."""

import json

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from games.ladder_daily import LADDER_GAME_ID, LADDER_PUBLISH_START_TAG
from games.ladder_offer import (
    accept_offer,
    create_offer,
    request_revision,
    send_offer,
    update_offer_content,
)
from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    LadderOffer,
    Like,
    Profile,
    Project,
    Task,
)
from games.support.constants import SUPPORT_CONSOLE_GROUP


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    CheckerType.objects.get_or_create(pk='raddle')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


def _make_ladder_game():
    project = Project.objects.get(pk='sections')
    game, _ = Game.objects.get_or_create(
        pk=LADDER_GAME_ID,
        defaults={
            'name': 'Лесенка',
            'author': 'test',
            'project': project,
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
        },
    )
    tags = dict(game.tags or {})
    tags[LADDER_PUBLISH_START_TAG] = '2099-01-01T00:00:00+03:00'
    game.tags = tags
    game.project = project
    game.is_ready = True
    game.is_playable = True
    game.is_tournament = False
    game.requires_ticket = False
    game.save()
    return game


def _ensure_social_apps():
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    site = Site.objects.get_current()
    for provider in ('google', 'vk', 'yandex'):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class LadderOfferFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        _ensure_social_apps()
        cls.game = _make_ladder_game()
        cls.user = User.objects.create_user('author', password='x')
        Profile.objects.create(
            user=cls.user,
            first_name='Анна',
            last_name='Автор',
            telegram_handle='anna_author',
        )
        cls.other = User.objects.create_user('other', password='x')
        Profile.objects.create(
            user=cls.other,
            first_name='Оля',
            last_name='Другая',
            telegram_handle='olya',
        )
        cls.staff = User.objects.create_user('staff', password='x', is_staff=True)
        Profile.objects.create(user=cls.staff, first_name='S', last_name='T', telegram_handle='staff')
        Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)[0].user_set.add(cls.staff)

    def test_profile_gate_blocks_without_telegram(self):
        self.user.profile.telegram_handle = ''
        self.user.profile.save(update_fields=['telegram_handle'])
        c = Client()
        c.force_login(self.user)
        resp = c.get('/create_ladder/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['profile_ready'])

    def test_create_page_explains_daily_offer(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get('/create_ladder/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['profile_ready'])
        self.assertContains(
            resp,
            'предлагать сделать одной из ежедневных',
        )
        self.assertContains(resp, 'Предложить опубликовать')
        self.assertNotContains(resp, 'предлагать Андрею')
        self.assertNotContains(resp, 'Отправить Андрею')
        self.assertContains(resp, 'offer_draft_autosave.js')
        self.assertContains(resp, 'Автосохранение включено')

    def test_offer_ladder_url_redirects(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get('/offer_ladder/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/create_ladder/')

    def test_create_send_accept_keeps_task_and_attempts(self):
        offer = create_offer(self.user)
        task = Task.objects.get(task_group=offer.task_group, number='1')
        update_offer_content(
            offer,
            words=['КОТ', 'РОТ', 'РОД'],
            hints=['к→р', 'т→д'],
            intro='',
            author='Анна Автор',
            comment='пожалуйста',
            mixed_script=False,
        )
        # Attempt + like before accept
        Attempt.manager.create(
            task=task,
            game=self.game,
            user=self.other,
            text=json.dumps({'word_index': 1, 'word': 'РОТ'}),
            status='Partial',
        )
        Like.manager.create(task=task, user=self.other, value=1)

        send_offer(offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, LadderOffer.STATUS_SENT)

        accept_offer(offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, LadderOffer.STATUS_ACCEPTED)
        self.assertIsNotNone(offer.accepted_link_id)
        self.assertEqual(offer.accepted_link.task_group_id, offer.task_group_id)
        self.assertEqual(Task.objects.filter(task_group=offer.task_group).count(), 1)
        self.assertEqual(Attempt.manager.filter(task=task, game=self.game).count(), 1)
        self.assertEqual(Like.manager.filter(task=task).count(), 1)

    def test_hash_page_public(self):
        offer = create_offer(self.user)
        update_offer_content(
            offer,
            words=['ДОМ', 'СОМ'],
            hints=['д→с'],
            author='Анна',
        )
        c = Client()
        resp = c.get('/ladder/{}/'.format(offer.share_hash))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-task-label="Лесенка · Анна"')
        resp_r = c.get('/ladder/{}/results/'.format(offer.share_hash))
        self.assertEqual(resp_r.status_code, 200)

    def test_create_offer_uses_new_placeholders(self):
        offer = create_offer(self.user)
        task = Task.objects.get(task_group=offer.task_group, number='1')
        payload = json.loads(task.checker_data)
        self.assertEqual(payload['words'], ['ОДИН', 'ДВА'])

    def test_author_cannot_edit_after_send(self):
        offer = create_offer(self.user)
        update_offer_content(offer, words=['ААА', 'БББ'], hints=['x'], author='A')
        send_offer(offer)
        c = Client()
        c.force_login(self.user)
        resp = c.post(
            '/create_ladder/{}/'.format(offer.pk),
            data=json.dumps({
                'words': ['XXX', 'YYY'],
                'hints': ['z'],
                'intro': '',
                'author': 'A',
                'comment': '',
                'mixed_script': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_send_rejects_default_placeholders(self):
        offer = create_offer(self.user)
        with self.assertRaisesMessage(Exception, 'Замените слова-заглушки на настоящую лесенку'):
            send_offer(offer)

    def test_request_revision_unlocks_draft(self):
        offer = create_offer(self.user)
        update_offer_content(offer, words=['ААА', 'БББ'], hints=['x'], author='A')
        send_offer(offer)
        request_revision(offer, admin_note='поправьте подсказку')
        offer.refresh_from_db()
        self.assertEqual(offer.status, LadderOffer.STATUS_DRAFT)
        self.assertEqual(offer.admin_note, 'поправьте подсказку')
        self.assertTrue(offer.can_author_edit())

    def test_support_accept_endpoint(self):
        offer = create_offer(self.user)
        update_offer_content(offer, words=['ЛЕС', 'БЕС'], hints=['л→б'], author='A')
        send_offer(offer)
        c = Client()
        c.force_login(self.staff)
        resp = c.post('/support/ladders/offers/{}/accept/'.format(offer.pk))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        offer.refresh_from_db()
        self.assertEqual(offer.status, LadderOffer.STATUS_ACCEPTED)
        self.assertTrue(
            GameTaskGroup.objects.filter(game=self.game, task_group=offer.task_group).exists()
        )

    def test_other_user_cannot_access_offer_api(self):
        offer = create_offer(self.user)
        c = Client()
        c.force_login(self.other)
        resp = c.get('/create_ladder/{}/'.format(offer.pk))
        self.assertEqual(resp.status_code, 404)

    def test_hash_page_allows_send_attempt_before_accept(self):
        offer = create_offer(self.user)
        update_offer_content(
            offer,
            words=['КОТ', 'РОТ'],
            hints=['к→р'],
            author='Анна',
        )
        task = Task.objects.get(task_group=offer.task_group, number='1')
        c = Client()
        c.force_login(self.other)
        resp = c.post(
            '/send_attempt/{}/'.format(task.pk),
            {
                'game_id': LADDER_GAME_ID,
                'word_index': '1',
                'word': 'РОТ',
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotEqual(data.get('status'), 'ambiguous_game')
        self.assertNotEqual(data.get('status'), 'no_access')

    def test_delete_accepted_slot_keeps_offer_task(self):
        from games.support.services.ladders import delete_ladder

        offer = create_offer(self.user)
        update_offer_content(offer, words=['ДОМ', 'СОМ'], hints=['д→с'], author='A')
        send_offer(offer)
        accept_offer(offer)
        offer.refresh_from_db()
        link_id = offer.accepted_link_id
        task_id = Task.objects.get(task_group=offer.task_group).pk
        delete_ladder(link_id)
        offer.refresh_from_db()
        self.assertIsNone(offer.accepted_link_id)
        self.assertEqual(offer.status, LadderOffer.STATUS_SENT)
        self.assertTrue(Task.objects.filter(pk=task_id).exists())
        self.assertTrue(LadderOffer.objects.filter(pk=offer.pk).exists())

    def test_hash_stays_public_after_accept_while_numeric_url_is_embargoed(self):
        offer = create_offer(self.user)
        update_offer_content(offer, words=['ЛЕС', 'БЕС'], hints=['л→б'], author='A')
        send_offer(offer)
        accept_offer(offer, at_number=100)
        offer.refresh_from_db()
        c = Client()

        # The opaque share URL remains public after the offer enters the schedule.
        resp = c.get('/ladder/{}/'.format(offer.share_hash))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            'data-task-label="Лесенка №{}"'.format(offer.accepted_link.number),
        )
        resp_r = c.get('/ladder/{}/results/'.format(offer.share_hash))
        self.assertEqual(resp_r.status_code, 200)

        # The future numeric URL still does not reveal the scheduled ladder.
        numeric_resp = c.get('/ladder/100/')
        self.assertEqual(numeric_resp.status_code, 404)
        numeric_results_resp = c.get('/ladder/100/results/')
        self.assertEqual(numeric_results_resp.status_code, 404)

    def test_reset_all_progress_clears_every_actor(self):
        from games.ladder_offer import reset_all_raddle_progress

        offer = create_offer(self.user)
        update_offer_content(offer, words=['КОТ', 'РОТ'], hints=['к→р'], author='A')
        task = Task.objects.get(task_group=offer.task_group, number='1')
        Attempt.manager.create(
            task=task, game=self.game, user=self.user, text='РОТ', status='Ok', points=1,
        )
        Attempt.manager.create(
            task=task, game=self.game, user=self.other, text='РОТ', status='Ok', points=1,
        )
        stats = reset_all_raddle_progress(task=task)
        self.assertEqual(stats['attempts'], 2)
        self.assertEqual(Attempt.manager.filter(task=task, game=self.game).count(), 0)

    def test_resize_unpublished_offer_resets_progress_for_every_actor(self):
        offer = create_offer(self.user)
        update_offer_content(
            offer,
            words=['КОТ', 'РОТ', 'РОД'],
            hints=['к→р', 'т→д'],
            author='А',
        )
        task = Task.objects.get(task_group=offer.task_group, number='1')
        attempt = Attempt.manager.create(
            task=task,
            game=self.game,
            user=self.other,
            text=json.dumps({'word_index': 1, 'word': 'РОТ'}),
            status='Ok',
            state=json.dumps({'solved_indices': [0, 1, 2]}),
        )
        ChainTaskState.objects.create(
            task=task,
            game=self.game,
            user=self.other,
            game_mode='general',
            state=attempt.state,
            last_attempt=attempt,
        )

        update_offer_content(
            offer,
            words=['ДОМ', 'СОМ', 'СОК', 'МАК'],
            hints=['1', '2', '3'],
            author='А',
            reset_actor_user=self.user,
        )

        self.assertFalse(Attempt.manager.filter(task=task, game=self.game).exists())
        self.assertFalse(ChainTaskState.objects.filter(task=task, game=self.game).exists())

    def test_same_size_offer_edit_keeps_other_actor_progress(self):
        offer = create_offer(self.user)
        update_offer_content(
            offer,
            words=['КОТ', 'РОТ', 'РОД'],
            hints=['1', '2'],
            author='А',
        )
        task = Task.objects.get(task_group=offer.task_group, number='1')
        Attempt.manager.create(
            task=task,
            game=self.game,
            user=self.other,
            text=json.dumps({'word_index': 1, 'word': 'РОТ'}),
            status='Ok',
        )

        update_offer_content(
            offer,
            words=['ДОМ', 'СОМ', 'СОК'],
            hints=['3', '4'],
            author='А',
            reset_actor_user=self.user,
        )

        self.assertEqual(
            Attempt.manager.filter(task=task, game=self.game, user=self.other).count(),
            1,
        )

    def test_resize_published_offer_does_not_reset_progress(self):
        self.game.tags = {
            **(self.game.tags or {}),
            LADDER_PUBLISH_START_TAG: '2020-01-01T00:00:00+03:00',
        }
        self.game.save(update_fields=['tags'])
        offer = create_offer(self.user)
        update_offer_content(
            offer,
            words=['КОТ', 'РОТ', 'РОД'],
            hints=['1', '2'],
            author='А',
        )
        send_offer(offer)
        offer = accept_offer(offer)
        task = Task.objects.get(task_group=offer.task_group, number='1')
        Attempt.manager.create(
            task=task,
            game=self.game,
            user=self.other,
            text=json.dumps({'word_index': 1, 'word': 'РОТ'}),
            status='Ok',
        )

        update_offer_content(
            offer,
            words=['ДОМ', 'СОМ', 'СОК', 'МАК'],
            hints=['3', '4', '5'],
            author='А',
            allow_non_draft=True,
        )

        self.assertEqual(Attempt.manager.filter(task=task, game=self.game).count(), 1)

    def test_support_reset_progress_endpoints(self):
        offer = create_offer(self.user)
        update_offer_content(offer, words=['КОТ', 'РОТ'], hints=['к→р'], author='A')
        send_offer(offer)
        task = Task.objects.get(task_group=offer.task_group, number='1')
        Attempt.manager.create(
            task=task, game=self.game, user=self.other, text='РОТ', status='Ok', points=1,
        )
        c = Client()
        c.force_login(self.staff)
        resp = c.post(
            reverse('support:ladder_offer_reset_progress', kwargs={'offer_id': offer.pk}),
            content_type='application/json',
            data='{}',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(resp.json()['reset']['attempts'], 1)

        accept_offer(offer)
        offer.refresh_from_db()
        Attempt.manager.create(
            task=task, game=self.game, user=self.user, text='РОТ', status='Ok', points=1,
        )
        resp2 = c.post(
            reverse(
                'support:ladders_reset_progress',
                kwargs={'link_id': offer.accepted_link_id},
            ),
            content_type='application/json',
            data='{}',
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()['reset']['attempts'], 1)
        self.assertEqual(Attempt.manager.filter(task=task).count(), 0)

    def test_send_offer_notifies_admin(self):
        from unittest.mock import patch

        offer = create_offer(self.user)
        update_offer_content(
            offer,
            words=['КОТ', 'РОТ'],
            hints=['к→р'],
            author='Анна',
            comment='Проверьте пожалуйста',
        )
        with patch('games.telegram.notify.notify_new_ladder_offer') as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                send_offer(offer)
            mock_notify.assert_called_once_with(offer.pk)
