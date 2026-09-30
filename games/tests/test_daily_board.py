from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from games.daily.board import (
    build_raddle_board_data,
    build_word_salad_board_data,
)


class DailyBoardBuilderTests(TestCase):
    @patch('games.daily.board.attach_salad_share_card')
    @patch('games.daily.board.build_word_salad_ui_context', return_value={'board': 'ui'})
    def test_salad_builder_keeps_ui_shape_and_share_boundary(
        self, build_ui, attach_share,
    ):
        game = SimpleNamespace(id='salad')
        ui = build_word_salad_board_data(
            game=game,
            task='task',
            placement='placement',
            grid=['A'],
            words=['WORD'],
            rare_words=[],
            state={'found': []},
            attempts=['attempt'],
            user='user',
            anon_key='anon',
        )

        self.assertEqual(ui, {'board': 'ui'})
        build_ui.assert_called_once()
        attach_share.assert_called_once()

    @patch('games.daily.board.attach_salad_share_card')
    @patch('games.daily.board.build_word_salad_ui_context', return_value={'board': 'ui'})
    def test_salad_builder_does_not_attach_share_card_for_other_games(
        self, build_ui, attach_share,
    ):
        build_word_salad_board_data(
            game=SimpleNamespace(id='other'),
            task='task',
            placement='placement',
            grid=['A'],
            words=['WORD'],
            rare_words=[],
            state={},
            attempts=[],
            user=None,
            anon_key='',
        )

        build_ui.assert_called_once()
        attach_share.assert_not_called()

    @patch('games.daily.board.attach_ladder_share_card')
    @patch('games.daily.board.build_raddle_ui_context', return_value={'board': 'ui'})
    def test_raddle_builder_preserves_ui_state_and_result_shape(
        self, build_ui, attach_share,
    ):
        state = {}
        task = SimpleNamespace(
            get_max_attempts=lambda: 5,
            get_results_max_points=lambda: 10,
        )
        ui_state = SimpleNamespace(drafts={'0': 'draft'}, clue_marks={'1': True})

        result = build_raddle_board_data(
            game=SimpleNamespace(id='ladder'),
            task=task,
            placement='placement',
            parsed={'n_words': 2},
            state=state,
            attempts=['attempt'],
            hint_attempts=['hint'],
            mode='general',
            ui_state=ui_state,
            share_title=None,
            user='user',
            anon_key='anon',
        )

        self.assertEqual(state['drafts'], {'0': 'draft'})
        self.assertEqual(state['clue_marks'], {'1': True})
        self.assertEqual(result['parsed'], {'n_words': 2})
        self.assertEqual(result['ui'], {'board': 'ui'})
        self.assertEqual(result['max_attempts'], 5)
        self.assertEqual(result['max_points_total'], 10)
        build_ui.assert_called_once()
        attach_share.assert_called_once()
