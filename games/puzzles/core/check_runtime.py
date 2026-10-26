"""Shared checker runtime: results, decorators, and boolean checker base."""

import re

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
