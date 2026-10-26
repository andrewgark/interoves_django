import copy
import json
import re
from decimal import Decimal
from games.matcher.norm_matcher import NormMatcher
from games.util import status_key, clean_text
from games.models import Task, Attempt
from games.replacements_lines import split_slot_answer_alternatives
from games.wordle import convert_words_wordle, read_wordle_dict, color_tiles
from games.alphabetty.core import guess_status, is_valid_guess, normalize_word
from games.alphabetty.play import dump_state, load_state
from games.grid_puzzle import GridShadingChecker, GridWallChecker
from games.puzzles.core.check_runtime import (
    BaseChecker,
    CheckResult,
    SimpleBoolChecker,
    clean,
    clean_but_ё_stays,
    delete_punctuation,
    delete_punctuation_metagram,
    delete_spaces,
)
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
                # есть слово из блэклиста
                return False
            if word in self.whitelist:
                has_whitelist_word = True
            elif word not in self.graylist:
                # есть непредсказуемое слово
                return False
        # все слова из грейлиста и есть хотя бы одно из вайтлиста
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
        except:
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
                except:
                    pass
            self.vocab.add(line)

        self.ALPHABET = set(list('абвгдеёжзийклмнопрстуфхцчшщъыьэюя'))

    @clean_but_ё_stays
    @delete_punctuation
    def check(self, text, attempt):
        words = text.split()
        if len(words) not in self.n_words_to_points:
            if len(words) > self.n_words:
                return CheckResult('Wrong', 'Wrong', 0,
                    comment='Число слов ({}) больше, чем {}'.format(len(words), self.n_words))
            return CheckResult('Wrong', 'Wrong', 0, comment='Слишком мало слов')
        has_word_not_from_vocab = False
        prev_letters = set()
        for word in words:
            word_letters = set()
            if len(word) != self.len_words:
                return CheckResult('Wrong', 'Wrong', 0,
                    comment='В слове {} число букв ({}) не равно {}'.format(word, len(word), self.len_words))
            for letter in word:
                if letter not in self.ALPHABET:
                    return CheckResult('Wrong', 'Wrong', 0,
                        comment='В слове {} встречен странный символ: {}'.format(word, letter))
                if letter in prev_letters:
                    return CheckResult('Wrong', 'Wrong', 0,
                        comment='Буква {} встречается в нескольких словах'.format(letter))
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


class AntiwordleChecker(BaseChecker):
    @clean
    @delete_punctuation
    def __init__(self, data, last_attempt_state=None):
        self.dictionary = read_wordle_dict()
        self.colors = data.split('\n')
        self.word_number = len(self.colors)

    @clean
    @delete_punctuation
    def check(self, text, attempt):
        words = [x.strip() for x in text.split()]
        answer = text.split()[-1].strip()

        status, data = convert_words_wordle(words, self.dictionary, self.word_number)
        if status == 'error':
            return CheckResult('Wrong', 'Wrong', 0, comment=data)
        words = data
        for i, word in enumerate(words):
            colors = color_tiles(word, answer)
            if colors != self.colors[i]: 
                return CheckResult('Wrong', 'Wrong', 0, comment='Цвета не совпадают в строке {}'.format(i + 1))
        return CheckResult('Ok', 'Ok', 1)


class WordSaladChecker(BaseChecker):
    """Проверяет JSON-путь по сетке и хранит найденные слова в chain state."""

    def __init__(self, data, last_attempt_state=None):
        from games.word_salad import load_state, parse_task_payload
        self.grid, self.words, self.rare_words = parse_task_payload(data, '')
        self.last_state = load_state(last_attempt_state)

    def check(self, text, attempt):
        from games.word_salad import (
            EXTRA_FOUND_COMMENT,
            EXTRA_NOT_FOUND_COMMENT,
            RARE_FOUND_COMMENT,
            append_unique_word,
            dump_state,
            extra_found_word,
            load_state,
            neighbours,
            normalize_word,
            removable_cells,
            score_for_state,
        )
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат пути')
        if not isinstance(payload, dict):
            return CheckResult('Wrong', 'Pending', 0, comment='Неверный формат пути')
        state = load_state(self.last_state)
        solved = set(state.get('solved_indices') or [])
        hint_counts = dict(state.get('hint_counts') or {})
        active = set(state.get('active', []))
        found_rare = set(state.get('found_rare') or [])
        found_rare_words = list(state.get('found_rare_words') or [])
        found_extra = list(state.get('found_extra') or [])

        def play_state():
            return {
                'solved_indices': sorted(solved),
                'hints': sorted(hint_counts),
                'hint_counts': hint_counts,
                'active': sorted(active),
                'found_rare': sorted(found_rare),
                'found_rare_words': found_rare_words,
                'found_extra': found_extra,
            }

        action = (payload.get('action') or 'solve').strip().lower()
        if action == 'hint':
            try:
                word_index = int(payload.get('word_index', -1))
            except (TypeError, ValueError):
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверная подсказка')
            if not (0 <= word_index < len(self.words)):
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверная подсказка')
            if word_index in solved:
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Слово уже открыто')
            current_count = int(hint_counts.get(word_index, 0) or 0)
            word_length = len(normalize_word(self.words[word_index]))
            if current_count >= word_length:
                return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Все буквы уже раскрыты')
            requested_number = payload.get('hint_number')
            if requested_number is not None:
                try:
                    requested_number = int(requested_number)
                except (TypeError, ValueError):
                    return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверная подсказка')
                if requested_number != current_count + 1:
                    return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Устаревшая подсказка')
            hint_counts[word_index] = current_count + 1
            next_state = play_state()
            tournament_status = 'Partial' if solved else 'Wrong'
            return CheckResult('Partial', tournament_status, score_for_state(next_state), dump_state(next_state))
        try:
            path = [int(value) for value in payload.get('path', [])]
        except (TypeError, ValueError):
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Неверный формат пути')
        if not path or len(path) != len(set(path)) or any(i not in active for i in path):
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Недопустимый путь')
        if any(b not in set(neighbours(a)) for a, b in zip(path, path[1:])):
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Буквы не соседние')
        written = ''.join(self.grid[i] for i in path)
        already = next((i for i, word in enumerate(self.words) if normalize_word(word) == written), None)
        if already is not None and already in solved:
            return CheckResult('Wrong', 'Pending', score_for_state(state), dump_state(state), comment='Слово уже открыто')
        match = next((i for i, word in enumerate(self.words)
                      if i not in solved and normalize_word(word) == written), None)
        if match is not None:
            solved.add(match)
            while True:
                removable = removable_cells(self.grid, self.words, active, solved)
                if not removable:
                    break
                active.discard(removable[0])
            next_state = play_state()
            status = 'Ok' if len(solved) == len(self.words) else 'Partial'
            return CheckResult(status, status, score_for_state(next_state), dump_state(next_state))
        already_rare = next(
            (i for i, word in enumerate(self.rare_words) if normalize_word(word) == written),
            None,
        )
        if already_rare is not None and already_rare in found_rare:
            return CheckResult(
                'Wrong',
                'Pending',
                score_for_state(state),
                dump_state(play_state()),
                comment='Слово уже открыто',
            )
        rare_match = next(
            (
                i for i, word in enumerate(self.rare_words)
                if i not in found_rare and normalize_word(word) == written
            ),
            None,
        )
        if rare_match is not None:
            found_rare.add(rare_match)
            append_unique_word(found_rare_words, self.rare_words[rare_match])
            next_state = play_state()
            tournament_status = 'Partial' if solved else 'Wrong'
            return CheckResult(
                'Partial',
                tournament_status,
                score_for_state(next_state),
                dump_state(next_state),
                comment=RARE_FOUND_COMMENT,
            )
        extra = extra_found_word(written, self.words, rare_words=self.rare_words)
        if extra:
            if extra in found_extra:
                return CheckResult(
                    'Wrong',
                    'Pending',
                    score_for_state(state),
                    dump_state(play_state()),
                    comment='Слово уже открыто',
                )
            append_unique_word(found_extra, extra)
            next_state = play_state()
            tournament_status = 'Partial' if solved else 'Wrong'
            return CheckResult(
                'Partial',
                tournament_status,
                score_for_state(next_state),
                dump_state(next_state),
                comment=EXTRA_FOUND_COMMENT,
            )
        return CheckResult(
            'Wrong',
            'Pending',
            score_for_state(state),
            dump_state(play_state()),
            comment=EXTRA_NOT_FOUND_COMMENT,
        )


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
                    tags__task=self.task_tag
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
            total_points * self.points_multiplier
        )


class AlphabettyChecker(BaseChecker):
    """Чекер Алфавитки (основной путь — dedicated guess API; это запасной send_attempt)."""

    def __init__(self, data, last_attempt_state=None):
        self.secret = normalize_word((data or '').strip().splitlines()[0] if data else '')
        self.last_state = load_state(last_attempt_state)

    def check(self, text, attempt):
        word = normalize_word(text)
        state = dict(self.last_state)
        state.setdefault('guesses', [])
        if state.get('won'):
            # Не начислять повторные Ok/очки после победы (как dedicated guess API).
            return CheckResult('Wrong', 'Wrong', 0, dump_state(state), comment='Уже угадано')
        if not is_valid_guess(
            word,
            user=getattr(attempt, 'user', None),
            anon_key=getattr(attempt, 'anon_key', None),
            task=getattr(attempt, 'task', None),
        ):
            return CheckResult('Wrong', 'Wrong', 0, dump_state(state), comment='Слова нет в словаре')
        if word in state['guesses']:
            return CheckResult('Wrong', 'Wrong', 0, dump_state(state), comment='Уже вводили')
        status = guess_status(word, self.secret)
        state['guesses'] = list(state['guesses']) + [word]
        if status == 'correct':
            state['won'] = True
            return CheckResult('Ok', 'Ok', 1, dump_state(state))
        return CheckResult('Partial', 'Partial', 0, dump_state(state), comment=status)


from games.puzzles.replacements.html_forms import HtmlFormsChecker
from games.puzzles.replacements.lines.checker import ReplacementsLinesChecker
from games.puzzles.raddle.submission import RaddleChecker
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
