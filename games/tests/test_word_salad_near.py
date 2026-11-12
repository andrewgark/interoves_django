"""«Почти ответ» candidates: forms of the answers that the grid lets a player trace."""

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from games.console.http.constants import SUPPORT_CONSOLE_GROUP
from games.puzzles.salad.near import near_answer_candidates, suggest_near_answers, word_forms
from games.puzzles.salad.rules import parse_grid

# КРАСКИ, КРАСКА and КРАСКЕ all trace from the К in the corner; КРАСОК does not.
GRID_TEXT = 'К Р А С\nБ В Е К\nГ Д А И\nЖ З Л Ю'


class NearAnswerFormsTests(SimpleTestCase):
    def test_forms_of_a_noun_cover_its_paradigm(self):
        forms = word_forms('КРАСКИ')
        self.assertIn('КРАСКА', forms)
        self.assertIn('КРАСОК', forms)
        self.assertIn('КРАСКАМИ', forms)

    def test_a_noun_reading_keeps_a_homograph_verb_out(self):
        forms = word_forms('СТАЛИ')
        self.assertIn('СТАЛЬ', forms)
        self.assertNotIn('СТАТЬ', forms)
        self.assertNotIn('СТАЛ', forms)

    def test_yo_and_multiword_answers(self):
        self.assertIn('ЕЖИКИ', word_forms('ЁЖИК'))
        self.assertEqual(word_forms('ЧЁРНЫЙ ЯЩИК'), ())

    def test_candidates_are_traceable_forms_not_already_listed(self):
        grid = parse_grid(GRID_TEXT)
        self.assertEqual(
            near_answer_candidates(grid, ['КРАСКИ']),
            [{'word': 'КРАСКА', 'answer': 'КРАСКИ'}, {'word': 'КРАСКЕ', 'answer': 'КРАСКИ'}],
        )
        self.assertEqual(
            near_answer_candidates(grid, ['КРАСКИ'], rare_words=['КРАСКЕ'], near_words=['КРАСКА']),
            [],
        )

    def test_unparsed_fields_give_no_candidates(self):
        self.assertEqual(suggest_near_answers('К Р А С', 'КРАСКИ'), [])
        self.assertEqual(suggest_near_answers(GRID_TEXT, ''), [])
        self.assertEqual(
            suggest_near_answers(GRID_TEXT, 'КРАСКИ', '', 'КРАСКА'),
            [{'word': 'КРАСКЕ', 'answer': 'КРАСКИ'}],
        )


class NearAnswerEndpointsTests(TestCase):
    payload = {'grid_text': GRID_TEXT, 'words_text': 'КРАСКИ', 'rare_words_text': '', 'near_words_text': ''}

    @classmethod
    def setUpTestData(cls):
        cls.support_user = User.objects.create_user(username='near-support', password='x')
        cls.support_user.groups.add(Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)[0])
        cls.player = User.objects.create_user(username='near-player', password='x')

    def _post(self, url):
        return self.client.post(url, self.payload, content_type='application/json')

    def test_support_console_lists_candidates(self):
        url = reverse('support:word_salad_near_candidates')
        self.client.force_login(self.player)
        self.assertNotEqual(self._post(url).status_code, 200)
        self.client.force_login(self.support_user)
        response = self._post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['word'] for item in response.json()['candidates']],
            ['КРАСКА', 'КРАСКЕ'],
        )

    def test_salad_authors_page_lists_candidates_for_a_signed_in_user(self):
        url = reverse('new_create_salad_near_candidates')
        self.assertNotEqual(self._post(url).status_code, 200)
        self.client.force_login(self.player)
        response = self._post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['candidates'][0], {'word': 'КРАСКА', 'answer': 'КРАСКИ'})
        self.assertEqual(self.client.get(url).status_code, 405)
