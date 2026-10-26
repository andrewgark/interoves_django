"""Wall category checker (words + explanation stages)."""

import copy
import json

from games.puzzles.core.check_runtime import BaseChecker, CheckResult
from games.util import clean_text, status_key


class WallChecker(BaseChecker):
    def __init__(self, data, last_attempt_state):
        from games.check import EqualsWithPossibleSpacesChecker

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
            answer['checker'] = EqualsWithPossibleSpacesChecker(answer['checker'])

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
        new_state = self.update_state(
            attempt,
            check_result.status,
            check_result.points * self.data['points_explanation'],
        )
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
