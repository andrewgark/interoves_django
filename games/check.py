import copy
import json
import re
from decimal import Decimal
from games.matcher.norm_matcher import NormMatcher
from games.util import status_key, clean_text
from games.models import Task, Attempt
from games.wordle import convert_words_wordle, read_wordle_dict, color_tiles


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
                result.append(line) # односимвольную пунктуацию не нужно удалять
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
        else:
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


class WallChecker(BaseChecker):
    def __init__(self, data, last_attempt_state):
        self.data = json.loads(data)
        if last_attempt_state is None:
            self.last_attempt_state = {
                'best_status': 'Wrong',
                'best_points': 0,
                'guessed_words': [],
                'guessed_explanations': [],
            }
        else:
            self.last_attempt_state = json.loads(last_attempt_state)
        for answer in self.data['answers']:
            answer['words'] = sorted([clean_text(x) for x in answer['words']])
            answer['checker'] = NormMatcherChecker(answer['checker'])

    def get_result(self, state):
        max_points = (self.data['points_words'] + self.data['points_explanation']) * len(self.data['answers'])

        if state['best_points'] == max_points:
            state['last_attempt']['points'] += self.data['points_bonus']
            state['best_points'] += self.data['points_bonus']
            return CheckResult('Ok', 'Ok', max_points + self.data['points_bonus'], json.dumps(state))
        if state['best_points'] == 0:
            return CheckResult('Wrong', 'Wrong', 0, json.dumps(state))
        if state['last_attempt']['stage'] == 'cat_words':
            return CheckResult('Partial', 'Partial', state['best_points'], json.dumps(state))
        if state['last_attempt']['status'] == 'Ok':
            return CheckResult('Partial', 'Partial', state['best_points'], json.dumps(state))        

        return CheckResult('Partial', 'Pending', state['best_points'], json.dumps(state))

    def update_state(self, attempt, status, points):        
        new_state = copy.deepcopy(self.last_attempt_state)
        new_state['last_attempt'] = {}
        new_state['last_attempt']['status'] = status
        new_state['last_attempt']['points'] = points
        new_state['last_attempt']['stage'] = attempt['stage']
        new_state['last_attempt']['words'] = attempt['words']

        new_state['best_status'] = max(new_state['best_status'], status, key=status_key)
        new_state['best_points'] = new_state['best_points'] + points
        return new_state

    def fail(self, attempt):
        state = self.update_state(attempt, 'Wrong', 0)
        return self.get_result(state)

    def check_category_words(self, attempt, category):
        if category['words'] in self.last_attempt_state['guessed_words']:
            return self.fail(attempt)

        new_state = self.update_state(attempt, 'Ok', self.data['points_words'])
        new_state['guessed_words'].append(category['words'])
        if len(new_state['guessed_words']) == len(self.data['answers']) - 1:
            for answer in self.data['answers']:
                if answer['words'] not in new_state['guessed_words']:
                    new_state['guessed_words'].append(answer['words'])
                    new_state['last_attempt']['points'] += 1
                    new_state['best_points'] += 1
                    break
        return self.get_result(new_state)

    def check_category_explanation(self, attempt, category):
        if category['words'] not in self.last_attempt_state['guessed_words']:
            return self.fail(attempt)
        if category['words'] in self.last_attempt_state['guessed_explanations']:
            return self.fail(attempt)

        check_result = category['checker'].check(attempt['explanation'], None)
        new_state = self.update_state(attempt, check_result.status, check_result.points * self.data['points_explanation'])
        if check_result.status == 'Ok':
            new_state['guessed_explanations'].append(category['words'])
        return self.get_result(new_state)

    def check(self, text, attempt):
        attempt = json.loads(text)
        words = sorted([clean_text(x) for x in attempt['words']])
        category = None
        for answer in self.data['answers']:
            if answer['words'] == words:
                category = answer
        if category is None:
            return self.fail(attempt)

        if attempt['stage'] == 'cat_words':
            return self.check_category_words(attempt, category)

        if attempt['stage'] == 'cat_explanation':
            return self.check_category_explanation(attempt, category)

        raise Exception('Unknown wall stage: {}'.format(attempt['stage']))


class SolutionsTagNumber:
    def __init__(self, data, last_attempt_state=None):
        self.data = json.loads(data)
        self.task_tag = self.data.get('tag', '')
        self.points_multiplier = self.data.get('points_multiplier', 1)

    def check(self, text, attempt):        
        try:
            task = Task.objects.get(
                    task_group=attempt.task.task_group,
                    tags__team=attempt.team.name,
                    tags__task=self.task_tag
                )
        except Task.DoesNotExist:
            return CheckResult('Wrong', 'Pending', 0)
        attempts_infos = Attempt.manager.get_task_attempts_infos(task, mode="tournament")
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
        }
    
    def create_checker(self, checker_type, data, last_attempt_state=None):
        return self.checker_type_to_checker[checker_type.id](data, last_attempt_state)
