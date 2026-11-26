"""«Создать свой Криптик»: черновик, проверка по ссылке, отправка, приём редактором."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from games.club.offers.cryptic import CrypticOfferError, accept_offer, request_revision, send_offer
from games.console.games.simple_daily_sections import console_for
from games.console.http.constants import CRYPTIC_CONSOLE_GROUP
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID
from games.models import CrypticOffer, GameTaskGroup, Profile, Task
from games.tests.test_advent import _cryptic_game

CLUE = 'Разбитый полк — кровопийца в постели'
FIELDS = {
    'clue': CLUE,
    'answer': 'клоп',
    'enumeration': '',
    'markup': '*Разбитый* (полк) — | {кровопийца в постели}',
    'formula': 'анаг[Разбитый](ПОЛК)',
    'comment': 'первый',
}


def _author(username='cx_author'):
    user = User.objects.create_user(username, password='x')
    Profile.objects.create(
        user=user, first_name='Ира', last_name='Криптикова',
        telegram_handle=username, telegram_verified=True,
    )
    return user


def _json(client, name, body=None, *args):
    return client.post(reverse(name, args=args), data=json.dumps(body or {}), content_type='application/json')


class CrypticOfferFlowTests(TestCase):
    def setUp(self):
        _cryptic_game(start='2020-12-04T00:00:00+03:00')
        self.author = _author()
        self.client.force_login(self.author)

    def _draft(self, **fields):
        offer_id = _json(self.client, 'ui_create_cryptic_create').json()['offer']['id']
        saved = _json(self.client, 'ui_create_cryptic_detail', {**FIELDS, **fields}, offer_id).json()
        self.assertTrue(saved['ok'], saved)
        return CrypticOffer.objects.get(pk=offer_id)

    def test_page_and_hub_link(self):
        page = self.client.get('/create_cryptic/')
        self.assertContains(page, 'Создать свой Криптик')
        self.assertContains(page, 'Как записывать разбор')
        self.assertContains(self.client.get('/cryptic/'), '/create_cryptic/')

    def test_draft_link_checks_answers_without_attempts(self):
        offer = self._draft()
        anon = Client()
        anon.force_login(_author('cx_friend'))
        page = anon.get(offer.play_url())
        self.assertContains(page, CLUE)
        self.assertContains(page, '(4)')
        self.assertNotContains(page, 'КЛОП')
        self.assertNotContains(page, 'клоп')
        check = reverse('ui_cryptic_offer_check', args=[offer.share_hash])
        self.assertEqual(anon.post(check, {'text': 'блоха'}).json(), {'ok': False})
        solved = anon.post(check, {'text': 'К Л О П'}).json()
        self.assertTrue(solved['ok'])
        self.assertIn('cx-steps', solved['explanation_html'])
        self.assertFalse(Task.objects.filter(answer__iexact='клоп').exists())

    def test_preview_endpoint(self):
        data = _json(self.client, 'ui_create_cryptic_preview', FIELDS).json()
        self.assertEqual((data['errors'], data['answer']), ([], 'КЛОП'))
        bad = _json(self.client, 'ui_create_cryptic_preview', {**FIELDS, 'formula': 'анаг[Разбитый](ПОЛ)'}).json()
        self.assertTrue(bad['errors'])

    def test_send_needs_a_checked_explanation_and_notifies_editors(self):
        offer = self._draft(formula='анаг(ПОЛК)')
        refused = _json(self.client, 'ui_create_cryptic_send', {}, offer.pk).json()
        self.assertIn('Разбор не сходится', refused['error'])
        self._fix_formula(offer)
        editor = _author('cx_editor')
        editor.groups.add(Group.objects.get_or_create(name=CRYPTIC_CONSOLE_GROUP)[0])
        Profile.objects.filter(user=editor).update(telegram_user_id=555)
        with patch('games.ops.telegram.bot.notify.send_admin_message', return_value=True) as admin, \
                patch('games.ops.telegram.transport.api.send_message', return_value=True) as dm, \
                self.captureOnCommitCallbacks(execute=True):
            sent = _json(self.client, 'ui_create_cryptic_send', {}, offer.pk).json()
        self.assertEqual(sent['offer']['status'], CrypticOffer.STATUS_SENT)
        self.assertIn('Новый криптик', admin.call_args.args[0])
        self.assertEqual(admin.call_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data'],
                         'offer:cryptic.accept:{}'.format(offer.pk))
        self.assertEqual(dm.call_args.args[0], 555)
        # On review the author can no longer edit.
        self.assertEqual(_json(self.client, 'ui_create_cryptic_detail', FIELDS, offer.pk).status_code, 400)

    def _fix_formula(self, offer):
        _json(self.client, 'ui_create_cryptic_detail', FIELDS, offer.pk)

    def test_editor_accepts_into_the_schedule_and_the_link_becomes_the_day(self):
        # Future days: the accepted one can still be deleted below.
        _cryptic_game(start='2099-12-04T00:00:00+03:00')
        offer = self._draft()
        send_offer(offer)
        editor = _author('cx_editor2')
        editor.groups.add(Group.objects.get_or_create(name=CRYPTIC_CONSOLE_GROUP)[0])
        console_for(CRYPTIC_GAME_ID).create(at_number=1, fields={
            'clue': 'Мир средь бела дня', 'answer': 'лад',
        })
        staff = Client()
        staff.force_login(editor)
        dashboard = staff.get('/support/cryptic/')
        self.assertContains(dashboard, 'Предложения игроков')
        self.assertEqual([o['clue'] for o in dashboard.context['offers_json']], [CLUE])
        accepted = _json(staff, 'support:cryptic_offer_accept', {}, offer.pk).json()
        self.assertTrue(accepted['ok'], accepted)
        self.assertEqual(accepted['offers'], [])
        offer.refresh_from_db()
        self.assertEqual((offer.status, offer.accepted_link.number, offer.reviewed_by), ('accepted', '2', editor))
        link = offer.accepted_link
        self.assertEqual(link.share_hash, offer.share_hash)
        task = Task.objects.get(task_group_id=link.task_group_id)
        self.assertEqual(task.tags['author'], 'Ира Криптикова')
        self.assertEqual(task.tags['cryptic_formula'], FIELDS['formula'])
        self.assertEqual(task.answer, 'КЛОП')
        self.assertEqual(list(link.task_group.authors.all()), [self.author.profile])
        # The share link is now the scheduled day, not the draft page.
        day = self.client.get(offer.play_url())
        self.assertNotContains(day, 'Ответы здесь не сохраняются')
        with self.assertRaisesMessage(CrypticOfferError, 'уже обработан'):
            accept_offer(offer)
        # Deleting the day puts the offer back in the editors' queue.
        console_for(CRYPTIC_GAME_ID).delete(link.pk)
        offer.refresh_from_db()
        self.assertEqual((offer.status, offer.accepted_link_id), ('sent', None))

    def test_revision_returns_the_draft_with_a_note(self):
        offer = self._draft()
        send_offer(offer)
        request_revision(offer, admin_note='Определение слабое')
        offer.refresh_from_db()
        self.assertEqual((offer.status, offer.status_label, offer.admin_note),
                         ('draft', 'На доработке', 'Определение слабое'))
        self.assertTrue(_json(self.client, 'ui_create_cryptic_detail', FIELDS, offer.pk).json()['ok'])

    def test_strangers_cannot_touch_offers(self):
        offer = self._draft()
        stranger = Client()
        stranger.force_login(_author('cx_stranger'))
        self.assertEqual(_json(stranger, 'ui_create_cryptic_detail', FIELDS, offer.pk).status_code, 404)
        self.assertEqual(_json(stranger, 'ui_create_cryptic_send', {}, offer.pk).status_code, 404)
        send_offer(offer)
        self.assertEqual(_json(stranger, 'support:cryptic_offer_accept', {}, offer.pk).status_code, 403)
        self.assertFalse(GameTaskGroup.objects.filter(share_hash=offer.share_hash).exists())

    def test_player_markup_never_reaches_the_page_as_html(self):
        _cryptic_game(start='2099-12-04T00:00:00+03:00')
        offer = self._draft(
            clue='<img src=x onerror=alert`1`> ловушка', answer='орган',
            markup='{<img src=x onerror=alert`1`>} | {ловушка}', formula='ОРГАН',
        )
        send_offer(offer)
        accept_offer(offer)
        offer.refresh_from_db()
        task = Task.objects.get(task_group_id=offer.accepted_link.task_group_id)
        self.assertNotIn('<img', task.text)
        self.assertIn('&lt;img', task.text)

    def test_answer_is_words_only(self):
        offer = self._draft(
            answer='<b>КЛОП</b>', markup='*Разбитый* (полк) — | {кровопийца в постели}',
        )
        with self.assertRaisesMessage(CrypticOfferError, 'только буквы, цифры, пробелы и дефисы'):
            send_offer(offer)

    def test_a_player_waits_after_ten_pending_offers(self):
        for _ in range(10):
            send_offer(self._draft())
        with self.assertRaisesMessage(CrypticOfferError, 'Дождитесь ответа'):
            send_offer(self._draft())

    def test_author_hears_about_the_decision(self):
        from games.notifications.models import Notification

        _cryptic_game(start='2099-12-04T00:00:00+03:00')
        offer = self._draft()
        send_offer(offer)
        request_revision(offer, admin_note='Поправьте определение')
        self._fix_formula(offer)
        send_offer(offer)
        accept_offer(offer)
        kinds = list(Notification.objects.filter(user=self.author).values_list('kind', flat=True))
        self.assertIn('offer.returned', kinds)
        self.assertIn('offer.accepted', kinds)
        page = self.client.get('/notifications/')
        # The «returned» one is stale once the offer is accepted, so the page drops it.
        self.assertContains(page, 'Ваш криптик приняли')

