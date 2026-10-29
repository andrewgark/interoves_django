"""games/puzzles/raddle/offline_pack.py: slot keys are complete and pieces stay linear."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from games.puzzles.raddle.engine import build_raddle_ui_context, default_raddle_state, parse_raddle_data
from games.puzzles.raddle.offline_pack import build_pack, reachable_states, slot_keys
from games.puzzles.raddle.transition import advance


def ladder(n, *, mixed=False):
    words = ['СЛ{}В'.format(chr(1040 + i % 32)) for i in range(n)]
    lengths = [4] * n
    if mixed and n >= 5:
        words[2], lengths[2] = 'ПОЛ-ЛИТ|ПОЛЛИТ', '3-3'
        words[3], lengths[3] = 'Dog', 3
    hints = ['Подсказка {} ____ ...'.format(i) if i % 2 else '____ и ещё {}'.format(i) for i in range(n - 1)]
    return parse_raddle_data(SimpleNamespace(task_type='raddle', answer='', checker_data=json.dumps(
        {'lengths': lengths, 'hints': hints, 'words': words}, ensure_ascii=False)))


def renderer():
    request = RequestFactory().get('/')
    request.user = AnonymousUser()
    base = {
        'request': request,
        'task': SimpleNamespace(id=7, tags={}, text='', task_type='raddle', get_player_hints=[], number='2'),
        'game': SimpleNamespace(id='ladder', name='Лесенка', outside_name=''),
        'gameplay_context_tokens': {7: 'tok'},
        'tg_name': 'Лесенка 3',
        'tg_number': '3',
    }
    return lambda template, extra: render_to_string(template, {**base, **extra}, request)


def pack_for(parsed, base_state, *, verify):
    calls = []

    def make_ui(state):
        calls.append(state)
        return build_raddle_ui_context(parsed, state, mode='general')

    tiers = {int(k): v for k, v in (base_state.get('assist_tier') or {}).items()}
    pack = build_pack(parsed, base_state, tiers=tiers, make_ui=make_ui, render=renderer(), verify=verify)
    return pack, calls


class OfflinePackTests(SimpleTestCase):
    def test_every_slot_of_every_state_matches_its_piece(self):
        # verify=True renders every slot of every reachable state and raises
        # PackConflict when one key would need two different pieces.
        for n, mixed in ((2, False), (3, False), (4, True), (7, True), (10, False)):
            parsed = ladder(n, mixed=mixed)
            for base in self._bases(parsed):
                with self.subTest(n=n, base=base):
                    pack_for(parsed, base, verify=True)

    def _bases(self, parsed):
        n = parsed['n_words']
        base = default_raddle_state(n)
        yield base
        if n >= 5:
            later = advance(advance(base, parsed, 1), parsed, n - 2)
            later = {**later, 'assist_tier': {'2': 1, str(n - 3): 2}, 'drafts': {'3': 'ПО'},
                     'clue_marks': {'1': True}}
            yield later

    def test_pieces_cover_every_key_the_page_can_meet(self):
        parsed = ladder(8, mixed=True)
        base = default_raddle_state(8)
        pack, _ = pack_for(parsed, base, verify=False)
        for state in reachable_states(parsed, base):
            keys = slot_keys(state, 8, {})
            for i, key in enumerate(keys['rows']):
                self.assertIn(key, pack['rows'][i])
            if keys['last_word'] is None:
                for h, key in enumerate(keys['clues']):
                    self.assertIn(key, pack['clues'][h])
            else:
                self.assertIn(str(keys['last_word']), pack['last_word'])
        self.assertIsNotNone(pack['result'])

    def test_pieces_grow_linearly(self):
        sizes = {}
        for n in (12, 24):
            pack, calls = pack_for(ladder(n), default_raddle_state(n), verify=False)
            pieces = sum(map(len, pack['rows'])) + sum(map(len, pack['clues'])) + len(pack['last_word'])
            sizes[n] = (pieces, len(calls))
        # Twice the words, about twice the pieces and contexts (not four times).
        self.assertLess(sizes[24][0], sizes[12][0] * 2.5, sizes)
        self.assertLess(sizes[24][1], sizes[12][1] * 2.5, sizes)


KEYS_CONTRACT = Path(settings.BASE_DIR) / 'contracts' / 'raddle_slot_keys.json'


def build_keys_contract():
    """Slot keys of every reachable state of a few ladders, from the Python keys."""
    cases = []
    for n in (2, 3, 4, 7):
        parsed = ladder(n, mixed=True)
        base = default_raddle_state(n)
        tiers = {}
        if n >= 5:
            base = advance(base, parsed, 1)
            tiers = {2: 1, n - 2: 2}
        for state in reachable_states(parsed, base):
            subset = {key: state[key] for key in ('solved_indices', 'used_hints')}
            cases.append({
                'n': n,
                'tiers': {str(k): v for k, v in tiers.items()},
                'state': subset,
                'keys': slot_keys(state, n, tiers),
            })
    return {
        '_comment': 'Generated by games/tests/test_raddle_offline_pack.py from offline_pack.slot_keys.',
        'cases': cases,
    }


class SlotKeysContractTests(SimpleTestCase):
    def test_contract_file_is_up_to_date(self):
        # Regenerate after an intended change:
        #   INTEROVES_UPDATE_CONTRACTS=1 ./run_tests.sh games.tests.test_raddle_offline_pack
        contract = build_keys_contract()
        if os.environ.get('INTEROVES_UPDATE_CONTRACTS') == '1':
            with KEYS_CONTRACT.open('w', encoding='utf-8') as f:
                json.dump(contract, f, ensure_ascii=False, separators=(',', ':'))
                f.write('\n')
        with KEYS_CONTRACT.open(encoding='utf-8') as f:
            self.assertEqual(json.load(f), contract)
