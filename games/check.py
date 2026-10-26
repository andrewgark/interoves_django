from games.grid_puzzle import GridShadingChecker, GridWallChecker
from games.puzzles.alphabetty.play import AlphabettyChecker
from games.puzzles.alphabetty.wordle import AntiwordleChecker
from games.puzzles.core.check_runtime import (
    AnyAnswerChecker,
    EqualsChecker,
    EqualsWithPossibleSpacesChecker,
    HangmanLettersChecker,
    LongStringChecker,
    MetagramChecker,
    NormMatcherChecker,
    NumberWithErrorChecker,
    RegexpChecker,
    SeveralAnswersChecker,
    SolutionsTagNumber,
    WhiteGrayBlackListChecker,
)
from games.puzzles.replacements.html_forms import HtmlFormsChecker
from games.puzzles.replacements.lines.checker import ReplacementsLinesChecker
from games.puzzles.raddle.submission import RaddleChecker
from games.puzzles.salad.submission import WordSaladChecker
from games.puzzles.wall.checker import WallChecker


class CheckerFactory:
    def __init__(self):
        self.checker_type_to_checker = {
            'equals': EqualsChecker,
            'equals_with_possible_spaces': EqualsWithPossibleSpacesChecker,
            'white_gray_black_list': WhiteGrayBlackListChecker,
            'metagram_checker': MetagramChecker,
            'norm_matcher': NormMatcherChecker,
            'wall': WallChecker,
            'hangman_letters': HangmanLettersChecker,
            'solutions_tag_number': SolutionsTagNumber,
            'regexp': RegexpChecker,
            'any_answer': AnyAnswerChecker,
            'number_with_error': NumberWithErrorChecker,
            'long_string': LongStringChecker,
            'antiwordle': AntiwordleChecker,
            'several_answers': SeveralAnswersChecker,
            'replacements_lines': ReplacementsLinesChecker,
            'html_forms': HtmlFormsChecker,
            'raddle': RaddleChecker,
            'alphabetty': AlphabettyChecker,
            'word_salad': WordSaladChecker,
            'grid-wall-checker': GridWallChecker,
            'grid-shading-checker': GridShadingChecker,
        }

    def create_checker(self, checker_type, data, last_attempt_state=None):
        return self.checker_type_to_checker[checker_type.id](data, last_attempt_state)
