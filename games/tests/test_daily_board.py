from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from games.daily.board import (
    DAILY_BOARD_ADAPTERS,
    build_raddle_board_data,
    build_word_salad_board_data,
    get_daily_board_adapter,
)


class DailyBoardBuilderTests(TestCase):
    def test_known_task_types_have_one_dispatch_entry(self):
        self.assertIs(
            get_daily_board_adapter('other', game_id='salad'),
            DAILY_BOARD_ADAPTERS['word_salad'],
        )
        self.assertIs(
            get_daily_board_adapter('other', game_id='ladder'),
            DAILY_BOARD_ADAPTERS['raddle'],
        )
        self.assertIs(get_daily_board_adapter('word_salad'), DAILY_BOARD_ADAPTERS['word_salad'])
        self.assertIsNone(get_daily_board_adapter('future_daily_game'))

    @patch('games.daily.board.get_daily_share_adapter')
    @patch('games.daily.board.build_word_salad_ui_context', return_value={'board': 'ui'})
    def test_salad_builder_keeps_ui_shape_and_share_boundary(
        self, build_ui, get_share,
    ):
        attach_share = get_share.return_value.attach
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
        get_share.assert_called_once_with('salad')
        attach_share.assert_called_once()

    @patch('games.daily.board.get_daily_share_adapter')
    @patch('games.daily.board.build_word_salad_ui_context', return_value={'board': 'ui'})
    def test_salad_builder_does_not_attach_share_card_for_other_games(
        self, build_ui, get_share,
    ):
        get_share.return_value = None
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
        get_share.assert_called_once_with('other')

    @patch('games.daily.board.get_daily_share_adapter')
    @patch('games.daily.board.build_raddle_ui_context', return_value={'board': 'ui'})
    def test_raddle_builder_preserves_ui_state_and_result_shape(
        self, build_ui, get_share,
    ):
        attach_share = get_share.return_value.attach
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

    @patch('games.daily.board.build_word_salad_board_data', return_value={'prepared': 'salad'})
    @patch('games.daily.board.latest_daily_state', return_value={'state': 'salad'})
    @patch(
        'games.daily.board.parse_word_salad_task_payload',
        return_value=(['A'], ['WORD'], []),
    )
    def test_salad_adapter_pipeline_is_explicit(
        self, parse_task, resolve_state, build_board,
    ):
        task = SimpleNamespace(checker_data='payload', answer='answer')
        attempts = SimpleNamespace(attempts=['attempt'])

        result = get_daily_board_adapter('word_salad').prepare(
            game=SimpleNamespace(id='salad'),
            task=task,
            placement='placement',
            attempts_info=attempts,
            team=None,
            user=None,
            anon_key='anon',
            mode='general',
            replay_slot=None,
            resolve_chain_state=lambda *args, **kwargs: None,
        )

        self.assertEqual(result, {'prepared': 'salad'})
        parse_task.assert_called_once_with('payload', 'answer')
        resolve_state.assert_called_once()
        build_board.assert_called_once()

    @patch('games.daily.board.build_raddle_board_data', return_value={'prepared': 'raddle'})
    @patch('games.daily.board.latest_daily_state', return_value={'state': 'raddle'})
    @patch('games.daily.board.parse_raddle_data', return_value={'n_words': 2})
    def test_raddle_adapter_pipeline_passes_ui_state_and_share_title(
        self, parse_task, resolve_state, build_board,
    ):
        task = SimpleNamespace()
        attempts = SimpleNamespace(attempts=['attempt'], hint_attempts=['hint'])
        ui_state = lambda *args, **kwargs: SimpleNamespace(drafts={}, clue_marks={})

        result = get_daily_board_adapter('raddle').prepare(
            game=SimpleNamespace(id='ladder'),
            task=task,
            placement='placement',
            attempts_info=attempts,
            team=None,
            user=None,
            anon_key='anon',
            mode='general',
            replay_slot=None,
            resolve_chain_state=lambda *args, **kwargs: None,
            raddle_ui_state_for_actor=ui_state,
            share_title=None,
        )

        self.assertEqual(result, {'prepared': 'raddle'})
        parse_task.assert_called_once_with(task)
        resolve_state.assert_called_once()
        build_board.assert_called_once()
