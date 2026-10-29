"""A tournament ladder page must not carry unsolved answers in its markup."""
import json
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from games.puzzles.raddle.engine import build_raddle_ui_context, default_raddle_state, parse_raddle_data

LADDER = SimpleNamespace(
    task_type='raddle',
    answer='',
    checker_data=json.dumps({
        'lengths': [3, 3, 3, 3],
        'hints': ['A ____', '____ C', '____ D'],
        'words': ['КОТ', 'РОТ', 'РОГ', 'РОК'],
    }, ensure_ascii=False),
)


def render(mode):
    parsed = parse_raddle_data(LADDER)
    ui = build_raddle_ui_context(parsed, default_raddle_state(parsed['n_words']), mode=mode)
    request = RequestFactory().get('/')
    request.user = AnonymousUser()
    return render_to_string('task-content/task-raddle.html', {
        'request': request,
        'rd': {'ui': ui},
        'task': SimpleNamespace(id=1, tags={}, text='', task_type='raddle', get_player_hints=[], number='1'),
        'game': SimpleNamespace(id='t', name='T', outside_name=''),
        'gameplay_context_tokens': {},
    })


class RaddleTournamentMarkupTests(SimpleTestCase):
    def test_tournament_page_has_only_solved_words(self):
        html = render('tournament')
        self.assertIn('КОТ', html)
        self.assertIn('РОК', html)
        self.assertNotIn('РОТ', html)
        self.assertNotIn('РОГ', html)

    def test_general_page_keeps_its_markup(self):
        html = render('general')
        self.assertIn('data-next-word="РОТ"', html)
        self.assertIn('data-prev-word="РОГ"', html)
