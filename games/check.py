import re
from decimal import Decimal


def clean(func):
   def func_wrapper(self, text):
       return func(self, text.lower().strip().replace("ё", "е"))
   return func_wrapper


def delete_spaces(func):
   def func_wrapper(self, text):
       return func(self, re.sub(r"\s+", "", text))
   return func_wrapper


def delete_punctuation(func):
   def func_wrapper(self, text):
       return func(self, re.sub(r"[.,\/#!$%\^&\*;:{}=\-_`~()—]+", "", text))
   return func_wrapper


class BaseChecker:
    def __init__(self, data):
        pass

    def check(self, text):
        pass


class SimpleBoolChecker(BaseChecker):
    def bool_check(self, text):
        pass

    def check(self, text):
        is_ok = self.bool_check(text)
        if is_ok:
            return 'Ok', 1
        else:
            return 'Wrong', 0


class EqualsChecker(SimpleBoolChecker):
    @clean
    def __init__(self, data):
        self.data = data

    @clean
    def bool_check(self, text):
        return text == self.data


class EqualsWithPossibleSpacesChecker(SimpleBoolChecker):
    @clean
    @delete_spaces
    @delete_punctuation
    def __init__(self, data):
        self.data = data

    @clean
    @delete_spaces
    @delete_punctuation
    def bool_check(self, text):
        return text == self.data


class WhiteGrayBlackListChecker(SimpleBoolChecker):
    def __init__(self, data):
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


class MetagramChecker(BaseChecker):
    @clean
    @delete_punctuation
    def __init__(self, data):
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
    @delete_punctuation
    def check(self, text):
        words = []
        for word in text.strip().split():
            if word:
                words.append(word)
        if len(words) != self.n:
            return 'Wrong', 0
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
            return 'Ok', 1
        if best_matching_segment * 2 >= self.n:
            return 'Partial', Decimal(0.5)
        return 'Wrong', 0


class CheckerFactory:
    def __init__(self):
        self.checker_type_to_checker = {
            'equals': EqualsChecker,
            'equals_with_possible_spaces': EqualsWithPossibleSpacesChecker,
            'white_gray_black_list': WhiteGrayBlackListChecker,
            'metagram_checker': MetagramChecker,
        }
    
    def create_checker(self, checker_type, data):
        return self.checker_type_to_checker[checker_type.id](data)
