"""Tests for user salad offers (/create_salad, share hash, accept)."""

import json

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
    WordSaladOffer,
)
from games.support.constants import SUPPORT_CONSOLE_GROUP
from games.support.services.word_salad import ensure_word_salad_game, set_publish_start
from games.word_salad import WORD_SALAD_GAME_ID, parse_task_data, validate_puzzle
from games.word_salad_daily import WORD_SALAD_PUBLISH_START_TAG
from games.word_salad_offer import (
    accept_offer,
    create_offer,
    request_revision,
    send_offer,
    serialize_offer,
    update_offer_content,
)


VALID_GRID = 'B C D E\nI H G F\nJ K L M\nQ P O N'
VALID_WORDS = 'BCDEFGHIJKLMNOPQ'


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    CheckerType.objects.get_or_create(pk='word_salad')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


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


def _make_ready_user(username):
    user = User.objects.create_user(username, password='x')
    Profile.objects.create(
        user=user,
        first_name='Анна',
        last_name='Автор',
        telegram_handle=username.replace('-', '_')[:32],
    )
    return user


class WordSaladOfferFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        _ensure_social_apps()
        cls.game = ensure_word_salad_game()
        tags = dict(cls.game.tags or {})
        tags[WORD_SALAD_PUBLISH_START_TAG] = '2099-01-01T00:00:00+03:00'
        cls.game.tags = tags
        cls.game.save(update_fields=['tags'])
        cls.user = _make_ready_user('salad-author')
        cls.other = _make_ready_user('salad-other')
        cls.staff = User.objects.create_user('salad-staff', password='x', is_staff=True)
        Profile.objects.create(
            user=cls.staff, first_name='S', last_name='T', telegram_handle='salad_staff',
        )
        Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)[0].user_set.add(cls.staff)

    def test_profile_gate_blocks_without_telegram(self):
        self.user.profile.telegram_handle = ''
        self.user.profile.save(update_fields=['telegram_handle'])
        c = Client()
        c.force_login(self.user)
        resp = c.get('/create_salad/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['profile_ready'])

    def test_create_page_has_two_actions(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get('/create_salad/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Предложить идею')
        self.assertContains(resp, 'Собрать салатик')
        self.assertContains(resp, 'Предложить опубликовать')
        self.assertNotContains(resp, 'Отправить Андрею')
        self.assertContains(resp, 'word_salad_grid_editor.js')
        self.assertContains(resp, 'offer_draft_autosave.js')

    def test_idea_create_send_accept_without_task_or_schedule(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_IDEA)
        self.assertIsNone(offer.task_group_id)
        self.assertEqual(offer.play_url(), '')
        update_offer_content(
            offer,
            theme='Города',
            idea_text='Столицы Европы, можно редкие',
            suggested_words='ОСЛО\nРИМ',
            comment='если влезет',
        )
        send_offer(offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, WordSaladOffer.STATUS_SENT)
        accept_offer(offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, WordSaladOffer.STATUS_ACCEPTED)
        self.assertIsNone(offer.accepted_link_id)
        self.assertFalse(GameTaskGroup.objects.filter(game_id=WORD_SALAD_GAME_ID).exists())

    def test_idea_send_requires_theme_and_text(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_IDEA)
        with self.assertRaisesRegex(Exception, 'тему'):
            send_offer(offer)
        update_offer_content(offer, theme='Тема')
        with self.assertRaisesRegex(Exception, 'идею'):
            send_offer(offer)

    def test_full_create_send_accept_keeps_task(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        task = Task.objects.get(task_group=offer.task_group, number='1')
        update_offer_content(
            offer,
            theme='Алфавитная дорожка',
            grid_text=VALID_GRID,
            words_text=VALID_WORDS,
            comment='готово',
        )
        Attempt.manager.create(
            task=task,
            game=self.game,
            user=self.other,
            text=json.dumps({'path': [0, 1, 2, 3]}),
            status='Wrong',
        )
        send_offer(offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, WordSaladOffer.STATUS_SENT)
        accept_offer(offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, WordSaladOffer.STATUS_ACCEPTED)
        self.assertIsNotNone(offer.accepted_link_id)
        self.assertEqual(offer.accepted_link.task_group_id, offer.task_group_id)
        self.assertEqual(Task.objects.filter(task_group=offer.task_group).count(), 1)
        self.assertEqual(Attempt.manager.filter(task=task, game=self.game).count(), 1)
        grid, words = parse_task_data(task.checker_data, '')
        validate_puzzle(grid, words)

    def test_full_send_rejects_placeholder_and_removable_letter(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        with self.assertRaisesRegex(Exception, 'заглушку|Соберите'):
            send_offer(offer)
        update_offer_content(
            offer,
            theme='короткое',
            grid_text='A B C D\nH G F E\nI J K L\nP O N M',
            words_text='ABCD',
        )
        with self.assertRaisesRegex(Exception, 'можно убрать'):
            send_offer(offer)

    def test_incomplete_draft_does_not_send_stale_puzzle(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        update_offer_content(
            offer,
            theme='Города',
            grid_text=VALID_GRID,
            words_text=VALID_WORDS,
        )
        update_offer_content(
            offer,
            theme='Города',
            grid_text='B C D',
            words_text=VALID_WORDS,
        )
        offer.refresh_from_db()
        row = serialize_offer(offer)
        self.assertIn('B', row.grid_text)
        self.assertNotIn('Q P O N', row.grid_text)
        self.assertFalse(row.is_playable)
        with self.assertRaisesRegex(Exception, '16'):
            send_offer(offer)

    def test_hash_page_public_after_grid_saved(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        update_offer_content(
            offer,
            theme='Города',
            grid_text=VALID_GRID,
            words_text=VALID_WORDS,
        )
        c = Client()
        resp = c.get('/salad/{}/'.format(offer.share_hash))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'new-word-salad__cell')

    def test_author_cannot_edit_after_send(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        update_offer_content(
            offer,
            theme='Города',
            grid_text=VALID_GRID,
            words_text=VALID_WORDS,
        )
        send_offer(offer)
        c = Client()
        c.force_login(self.user)
        resp = c.post(
            '/create_salad/{}/'.format(offer.pk),
            data=json.dumps({'theme': 'другое', 'grid_text': VALID_GRID, 'words_text': VALID_WORDS}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_http_create_idea_and_full(self):
        c = Client()
        c.force_login(self.user)
        idea = c.post(
            '/create_salad/create/',
            data=json.dumps({'kind': 'idea'}),
            content_type='application/json',
        )
        self.assertEqual(idea.status_code, 200)
        self.assertEqual(idea.json()['offer']['kind'], 'idea')
        self.assertFalse(idea.json()['offer']['is_playable'])
        full = c.post(
            '/create_salad/create/',
            data=json.dumps({'kind': 'full'}),
            content_type='application/json',
        )
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.json()['offer']['kind'], 'full')

    def test_support_accepts_sent_full_offer(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        update_offer_content(
            offer,
            theme='Города',
            grid_text=VALID_GRID,
            words_text=VALID_WORDS,
        )
        send_offer(offer)
        c = Client()
        c.force_login(self.staff)
        resp = c.post(reverse('support:word_salad_offer_accept', args=[offer.pk]))
        self.assertEqual(resp.status_code, 200)
        offer.refresh_from_db()
        self.assertEqual(offer.status, WordSaladOffer.STATUS_ACCEPTED)
        self.assertTrue(GameTaskGroup.objects.filter(pk=offer.accepted_link_id).exists())

    def test_support_dashboard_has_sent_tab(self):
        c = Client()
        c.force_login(self.staff)
        resp = c.get(reverse('support:word_salad'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-tab="sent"')
        self.assertContains(resp, 'word-salad-offers-bootstrap')
        self.assertContains(resp, 'word_salad_grid_editor.js')

    def test_revision_returns_to_draft(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_IDEA)
        update_offer_content(offer, theme='Тема', idea_text='текст')
        send_offer(offer)
        request_revision(offer, admin_note='добавьте ещё образов')
        offer.refresh_from_db()
        self.assertEqual(offer.status, WordSaladOffer.STATUS_DRAFT)
        self.assertEqual(offer.admin_note, 'добавьте ещё образов')

    def test_resolve_game_for_draft_salad(self):
        offer = create_offer(self.user, kind=WordSaladOffer.KIND_FULL)
        task = Task.objects.get(task_group=offer.task_group, number='1')
        game = GameTaskGroup.resolve_game_for_task(task)
        self.assertIsNotNone(game)
        self.assertEqual(game.id, WORD_SALAD_GAME_ID)
        game_hinted = GameTaskGroup.resolve_game_for_task(task, game_id=WORD_SALAD_GAME_ID)
        self.assertEqual(game_hinted.id, WORD_SALAD_GAME_ID)

    def test_merge_preview_counts_salad_offers(self):
        from games.account_merge import build_account_merge_preview

        create_offer(self.user, kind=WordSaladOffer.KIND_IDEA)
        preview = build_account_merge_preview(self.other, self.user)
        self.assertEqual(preview['offers'], 1)


