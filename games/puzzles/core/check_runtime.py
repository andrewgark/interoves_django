"""Shared checker runtime: results, decorators, and generic checker implementations."""

import json
import re
from decimal import Decimal

from games.puzzles.core.norm_matcher import NormMatcher
from games.models import Attempt, Task
from games.util import clean_text


class CheckResult:
    def __init__(self, status, tournament_status, points, state=None, comment=None):
        self.status = status
        self.tournament_status = tournament_status
        self.points = points
        self.state = state
        self.comment = comment


def clean(func):
    def func_wrapper(self, text, *args, **kw):
        return func(self, clean_text(text), *args, **kw)

    return func_wrapper


def clean_but_ё_stays(func):
    def func_wrapper(self, text, *args, **kw):
        return func(self, clean_text(text, replace_ё=False), *args, **kw)

    return func_wrapper


def delete_spaces(func):
    def func_wrapper(self, text, *args, **kw):
        return func(self, re.sub(r"[^\S\r\n]+", "", text), *args, **kw)

    return func_wrapper


def delete_punctuation(func):
    def func_wrapper(self, text, *args, **kw):
        result = []
        for line in text.split('\n'):
            line = line.strip()
            if len(line) != 1:
                new_line = re.sub(r"[.,\/#!?$%\^&\*;:{}=\"\-_`~()—–]+", "", line)
                new_line = re.sub(r" +", " ", new_line)
                result.append(new_line)
            else:
                result.append(line)
        new_text = '\n'.join(result)
        return func(self, new_text, *args, **kw)

    return func_wrapper


def delete_punctuation_metagram(func):
    def func_wrapper(self, text, *args, **kw):
        new_text = re.sub(r"[.,\/#!?$%\^&\*;:{}=\"\-_`~()—]+", " ", text)
        new_text = re.sub(r" +", " ", new_text)
        return func(self, new_text, *args, **kw)

    return func_wrapper


class BaseChecker:
    def __init__(self, data, last_attempt_state=None):
        pass

    def check(self, text, attempt):
        pass


class SimpleBoolChecker(BaseChecker):
    def bool_check(self, text):
        pass

    def check(self, text, attempt):
        is_ok = self.bool_check(text)
        if is_ok:
            return CheckResult('Ok', 'Ok', 1)
        return CheckResult('Wrong', 'Pending', 0)


class EqualsChecker(SimpleBoolChecker):
    @clean
    def __init__(self, data, last_attempt_state=None):
        self.data = data

    @clean
    def bool_check(self, text):
        return text == self.data


class NormMatcherChecker(SimpleBoolChecker):
    @clean
    @delete_punctuation
    def __init__(self, data, last_attempt_state=None):
        self.matcher = NormMatcher(data)

    @clean
    @delete_punctuation
    def bool_check(self, text):
        return self.matcher.match(text)


class EqualsWithPossibleSpacesChecker(SimpleBoolChecker):
    @clean
    @delete_spaces
    @delete_punctuation
    def __init__(self, data, last_attempt_state=None):
        self.data = []
        for line in data.split('\n'):
            self.data.append(line.strip())

    @clean
    @delete_spaces
    @delete_punctuation
    def bool_check(self, text):
        for line in self.data:
            if text == line:
                return True
        return False


class WhiteGrayBlackListChecker(SimpleBoolChecker):
    def __init__(self, data, last_attempt_state=None):
        self.whitelist = set()
        self.graylist = set()
        self.blacklist = set()
        for line in data.split('\n'):
            line_split = line.strip().split()
            sign = line_split[0]
            words = line_split[1:]
            for word in words:
                word = word.lower().strip()
                if sign == '+':
                    self.whitelist.add(word)
                elif sign == '=':
                    self.graylist.add(word)
                elif sign == '-':
                    self.blacklist.add(word)
                else:
                    raise Exception('incorrect checker data: "{}" can be only "+", "=" or "-"'.format(sign))

    def bool_check(self, text):
        words = text.split()
        has_whitelist_word = False
        for word in words:
            word = word.lower().strip()
            if word in self.blacklist:
                return False
            if word in self.whitelist:
                has_whitelist_word = True
            elif word not in self.graylist:
                return False
        return has_whitelist_word


class RegexpChecker(SimpleBoolChecker):
    def __init__(self, data, last_attempt_state=None):
        self.regexp = re.compile(data.strip())

    @clean
    @delete_punctuation
    def bool_check(self, text):
        if self.regexp.findall(text):
            return True
        return False


class AnyAnswerChecker(SimpleBoolChecker):
    def bool_check(self, text):
        return True


class NumberWithErrorChecker(SimpleBoolChecker):
    def __init__(self, data, last_attempt_state=None):
        self.number, self.error = [int(s.strip()) for s in data.split('\n')]

    @clean
    @delete_punctuation
    def bool_check(self, text):
        try:
            answer = int(text)
            return (self.number - self.error <= answer <= self.number + self.error)
        except Exception:
            return False


class SeveralAnswersChecker(BaseChecker):
    @clean
    def __init__(self, data, last_attempt_state=None):
        self.answers = {}
        for line in data.split('\n'):
            line_split = line.strip().split()
            points = int(line_split[0])
            answer = ' '.join(line_split[1:])
            self.answers[answer] = points

    @clean
    def check(self, text, attempt):
        if text not in self.answers:
            return CheckResult('Wrong', 'Pending', 0)
        points = self.answers[text]
        if points == 100:
            return CheckResult('Ok', 'Ok', 1)
        return CheckResult('Partial', 'Partial', Decimal(points) / Decimal(100))


class MetagramChecker(BaseChecker):
    @clean
    @delete_punctuation_metagram
    def __init__(self, data, last_attempt_state=None):
        self.answer_variants = []
        self.n = None
        for line in data.split('\n'):
            answers = []
            line_split = line.strip().split()
            for word in line_split:
                if word:
                    answers.append(word)
            if answers:
                self.answer_variants.append(answers)
                if self.n is not None:
                    assert self.n == len(answers)
                else:
                    self.n = len(answers)

    @clean
    @delete_punctuation_metagram
    def check(self, text, attempt):
        words = []
        for word in text.strip().split():
            if word:
                words.append(word)
        if len(words) != self.n:
            return CheckResult('Wrong', 'Pending', 0)
        best_matching_segment = 0
        for answers in self.answer_variants:
            last_matching_segment = 0
            for word, answer in zip(words, answers):
                if word == answer:
                    last_matching_segment += 1
                else:
                    last_matching_segment = 0
                best_matching_segment = max(best_matching_segment, last_matching_segment)
        if best_matching_segment == self.n:
            return CheckResult('Ok', 'Ok', 1)
        if best_matching_segment * 2 >= self.n:
            return CheckResult('Partial', 'Pending', Decimal(0.5))
        return CheckResult('Wrong', 'Pending', 0)


class LongStringChecker(BaseChecker):
    @clean
    @delete_punctuation_metagram
    def __init__(self, data, last_attempt_state=None):
        self.answer_variants = []
        self.n = None
        for line in data.split('\n'):
            answers = []
            line_split = line.strip().split()
            for word in line_split:
                if word:
                    answers.append(word)
            if answers:
                self.answer_variants.append(answers)
                if self.n is not None:
                    assert self.n == len(answers)
                else:
                    self.n = len(answers)

    @clean
    @delete_punctuation_metagram
    def check(self, text, attempt):
        words = []
        for word in text.strip().split():
            if word:
                words.append(word)
        if len(words) > self.n:
            return CheckResult('Wrong', 'Pending', 0)
        best_matching_segment = 0
        for answers in self.answer_variants:

            def NVP(words, answers):
                n = len(answers)
                m = len(words)
                dp = [[0] * (n + 1) for _ in range(m + 1)]
                for i in range(1, m + 1):
                    for j in range(1, n + 1):
                        if words[i - 1] == answers[j - 1]:
                            dp[i][j] = 1 + dp[i - 1][j - 1]
                        else:
                            dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
                return dp[m][n]

            last_matching_segment = NVP(words, answers)
            best_matching_segment = max(best_matching_segment, last_matching_segment)
        if best_matching_segment == self.n:
            return CheckResult('Ok', 'Ok', 1)
        if best_matching_segment > 0:
            return CheckResult('Partial', 'Pending', Decimal(best_matching_segment / self.n))
        return CheckResult('Wrong', 'Pending', 0)


class HangmanLettersChecker(BaseChecker):
    @clean_but_ё_stays
    @delete_punctuation
    def __init__(self, data, last_attempt_state=None):
        self.n_words = None
        self.len_words = None
        self.vocab = set()
        self.n_words_to_points = {}
        for i, line in enumerate(data.split('\n')):
            line = line.strip()
            if i == 0:
                x, y = line.split()
                self.n_words, self.len_words = int(x), int(y)
                continue
            if len(line.split()) == 2:
                try:
                    n_words, points = [int(x) for x in line.split()]
                    self.n_words_to_points[n_words] = points
                    continue
                except Exception:
                    pass
            self.vocab.add(line)

        self.ALPHABET = set(list('абвгдеёжзийклмнопрстуфхцчшщъыьэюя'))

    @clean_but_ё_stays
    @delete_punctuation
    def check(self, text, attempt):
        words = text.split()
        if len(words) not in self.n_words_to_points:
            if len(words) > self.n_words:
                return CheckResult(
                    'Wrong', 'Wrong', 0,
                    comment='Число слов ({}) больше, чем {}'.format(len(words), self.n_words),
                )
            return CheckResult('Wrong', 'Wrong', 0, comment='Слишком мало слов')
        has_word_not_from_vocab = False
        prev_letters = set()
        for word in words:
            word_letters = set()
            if len(word) != self.len_words:
                return CheckResult(
                    'Wrong', 'Wrong', 0,
                    comment='В слове {} число букв ({}) не равно {}'.format(word, len(word), self.len_words),
                )
            for letter in word:
                if letter not in self.ALPHABET:
                    return CheckResult(
                        'Wrong', 'Wrong', 0,
                        comment='В слове {} встречен странный символ: {}'.format(word, letter),
                    )
                if letter in prev_letters:
                    return CheckResult(
                        'Wrong', 'Wrong', 0,
                        comment='Буква {} встречается в нескольких словах'.format(letter),
                    )
                word_letters.add(letter)
            for letter in word_letters:
                prev_letters.add(letter)
            if word not in self.vocab:
                has_word_not_from_vocab = True
        result_status = 'Ok'
        points = self.n_words_to_points[len(words)]
        if len(words) != self.n_words:
            result_status = 'Partial'
        if has_word_not_from_vocab:
            result_status = 'Pending'
        return CheckResult(result_status, result_status, points)


class SolutionsTagNumber:
    def __init__(self, data, last_attempt_state=None):
        self.data = json.loads(data)
        self.task_tag = self.data.get('tag', '')
        self.points_multiplier = self.data.get('points_multiplier', 1)

    def check(self, text, attempt):
        try:
            task = Task.objects.visible().get(
                task_group=attempt.task.task_group,
                tags__team=attempt.team.name,
                tags__task=self.task_tag,
            )
        except Task.DoesNotExist:
            return CheckResult('Wrong', 'Pending', 0)
        attempts_infos = Attempt.manager.get_task_attempts_infos(
            task, mode="tournament", game=attempt.game,
        )
        total_points = 0
        for attempts_info in attempts_infos:
            if not attempts_info.attempts:
                continue
            team = attempts_info.attempts[0].team
            if team.is_hidden:
                continue
            if attempts_info.best_attempt is None:
                continue
            task_points = attempts_info.best_attempt.points
            if task_points > 0:
                total_points += task_points
        return CheckResult(
            'Ok', 'Ok',
            total_points * self.points_multiplier,
        )


class CheckerFactory:
    def __init__(self):
        from games.grid_puzzle import GridShadingChecker, GridWallChecker
        from games.puzzles.alphabetty.play import AlphabettyChecker
        from games.puzzles.alphabetty.wordle import AntiwordleChecker
        from games.puzzles.replacements.html_forms import HtmlFormsChecker
        from games.puzzles.replacements.lines.checker import ReplacementsLinesChecker
        from games.puzzles.raddle.submission import RaddleChecker
        from games.puzzles.salad.submission import WordSaladChecker
        from games.puzzles.wall.checker import WallChecker

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


from games.grid_puzzle import GridShadingChecker, GridWallChecker  # noqa: E402
from games.puzzles.replacements.lines.checker import ReplacementsLinesChecker  # noqa: E402
from games.puzzles.raddle.submission import RaddleChecker  # noqa: E402
