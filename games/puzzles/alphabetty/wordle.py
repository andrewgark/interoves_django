"""Five-letter word dict and Wordle-style tile coloring (antiwordle checker)."""

from collections import Counter
from pathlib import Path

from games.puzzles.core.check_runtime import (
    BaseChecker,
    CheckResult,
    clean,
    delete_punctuation,
)

WORDLE_DICT_PATH = Path(__file__).resolve().parent / 'dictionaries' / 'wordle_dict.txt'


def read_wordle_dict():
    with WORDLE_DICT_PATH.open('r', encoding='utf-8') as f:
        lines = f.readlines()
    return {line.strip() for line in lines if line.strip()}


def convert_words_wordle(words, dict, word_number):
    if len(words) != word_number:
        return ('error', 'Должно быть {} слов'.format(word_number))
    words = [word.lower().strip() for word in words]
    for word in words:
        if len(word) != 5:
            return ('error', 'Найдено слово не из 5 букв: {}'.format(word))
        if word not in dict:
            return ('error', 'Найдено слово не из словаря: {}'.format(word))
    return ('correct', words)


def color_tiles(word, answer):
    res = [None for i in range(5)]
    cnt_answer = Counter()
    for i in range(5):
        if word[i] == answer[i]:
            res[i] = 'g'
        elif word[i] not in answer:
            res[i] = 'n'
            cnt_answer[answer[i]] += 1
        else:
            cnt_answer[answer[i]] += 1
    for i in range(5):
        if res[i] is None:
            letter = word[i]
            if cnt_answer[letter] > 0:
                res[i] = 'y'
                cnt_answer[letter] -= 1
            else:
                res[i] = 'n'
    return ''.join(res)


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
