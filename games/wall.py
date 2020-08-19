import json
from games.util import clean_text


class WallTile:
    def __init__(self, id, text):
        self.id = id
        self.text = text


class ExpTile:
    def __init__(self, type=None, words_n_attempts=None, words_max_attempts=None,
                 explanation_n_attempts=None, explanation_max_attempts=None, id=None):
        self.type = type
        self.words_n_attempts = words_n_attempts
        self.words_max_attempts = words_max_attempts
        self.explanation_n_attempts = explanation_n_attempts
        self.explanation_max_attempts = explanation_max_attempts
        self.id = id


def get_wall_default_max_attempts(n_cat):
    attempts = []
    for i in range(0, n_cat - 1):
        attempts.append(3 * 2 ** i)
    return attempts[::-1]


class Wall:
    def __init__(self, task):
        try:
            data = json.loads(task.text)
        except:
            data = {}
        try:
            checker_data = json.loads(task.checker_data)
        except:
            checker_data = {}
        self.task = task
        self.n_cat = data.get('n_cat', 4)
        self.n_words = data.get('n_words', 4)
        self.words = data['words']
        self.max_attempts = data.get('attempts', get_wall_default_max_attempts(self.n_cat))
        self.points_words = checker_data['points_words']
        self.points_explanation = checker_data['points_explanation']
        self.points_bonus = checker_data['points_bonus']
        self.answer_words = [x['words'] for x in checker_data['answers']]
        self.max_points = self.n_cat * (self.points_words + self.points_explanation) + self.points_bonus
        self.text = data.get('text', '')

    def get_tiles(self):
        wall_tiles = []
        last_id = 0
        for word in self.words:
            last_id += 1
            wall_tiles.append(WallTile(last_id, word))
        return wall_tiles

    def get_attempt_text(self, data):
        if 'explanation' in data:
            return "{} ({})".format(data['explanation'], ", ".join(sorted(data['words'])))
        return ", ".join(sorted(data['words']))

    def get_n_max_attempts_dict(self, attempts=None):
        n_max_attempts_dict = {
            'cat_words': {
            },
            'cat_explanation': {
            }
        }
        for i, max_attempts in enumerate(self.max_attempts + [1]):
            n_max_attempts_dict['cat_words'][i] = {}
            n_max_attempts_dict['cat_words'][i]['n_attempts'] = 0
            n_max_attempts_dict['cat_words'][i]['max_attempts'] = max_attempts
            n_max_attempts_dict['cat_words'][i]['guessed'] = False

            n_max_attempts_dict['cat_explanation'][i] = {}
            n_max_attempts_dict['cat_explanation'][i]['n_attempts'] = 0
            n_max_attempts_dict['cat_explanation'][i]['max_attempts'] = self.task.get_max_attempts()

        for attempt in attempts:
            state = json.loads(attempt.state)
            
            stage = state['last_attempt']['stage']
            if stage == 'cat_words':
                n_guessed = len(state['guessed_words'])
                status = state['last_attempt']['status']
                if status != 'Ok':
                    n_max_attempts_dict[stage][n_guessed]['n_attempts'] += 1
                else:
                    n_max_attempts_dict[stage][n_guessed - 1]['n_attempts'] += 1
                    n_max_attempts_dict[stage][n_guessed - 1]['guessed'] = True
            else:
                words = state['last_attempt']['words']
                words_index = None
                for i, other_words in enumerate(state['guessed_words']):
                    if words == other_words:
                        words_index = i
                if words_index is not None:
                    n_max_attempts_dict[stage][words_index]['n_attempts'] += 1
        return n_max_attempts_dict
    
    def validate_max_attempts(self, attempts, attempt):
        if not attempts:
            return None

        n_max_attempts_dict = self.get_n_max_attempts_dict(attempts)

        attempt_data = json.loads(attempt.text)
        stage = attempt_data['stage']
        state = json.loads(attempts[-1].state)
        if stage == 'cat_words':
            index = len(state['guessed_words'])
        else:
            words = attempt_data['words']
            index = None
            for i, other_words in enumerate(state['guessed_words']):
                if words == other_words:
                    index = i
        n_attempts = n_max_attempts_dict[stage][index]['n_attempts']
        max_attempts = n_max_attempts_dict[stage][index]['max_attempts']
        
        if n_attempts >= max_attempts:
            return stage, n_attempts, max_attempts
        return None

    def get_not_guessed_tiles(self, attempts_info):
        tiles = self.get_tiles()
        if not attempts_info or not attempts_info.last_attempt:
            return tiles
        guessed_cats = json.loads(attempts_info.last_attempt.state).get('guessed_words', [])
        not_guessed_tiles = []
        for tile in tiles:
            is_guessed = False
            for category in guessed_cats:    
                if clean_text(tile.text) in category:
                    is_guessed = True
            if not is_guessed:
                not_guessed_tiles.append(tile)
        return not_guessed_tiles

    def get_guessed_tiles(self, attempts_info):
        tiles = self.get_tiles()
        if not attempts_info or not attempts_info.last_attempt:
            return []
        guessed_cats = json.loads(attempts_info.last_attempt.state).get('guessed_words', [])
        guessed_tiles = []
        for i, category in enumerate(guessed_cats):
            for tile in tiles:
                if clean_text(tile.text) in category:
                    tile.category_id = i
                    guessed_tiles.append(tile)
        return guessed_tiles

    def guessing_tiles_is_over(self, attempts_info):
        if not attempts_info or not attempts_info.last_attempt:
            return False

        attempts = []
        if attempts_info and attempts_info.attempts:
            attempts = attempts_info.attempts

        state = json.loads(attempts_info.last_attempt.state)
        if len(state['guessed_words']) == self.n_cat:
            return True

        n_max_attempts_dict = self.get_n_max_attempts_dict(attempts)
        for x in n_max_attempts_dict['cat_words'].values():
            if x['n_attempts'] >= x['max_attempts'] and not x['guessed']:
                return True
        return False

    def get_exptiles(self, attempts_info, mode):
        attempts = []
        if attempts_info and attempts_info.attempts:
            attempts = attempts_info.attempts
        n_max_attempts_dict = self.get_n_max_attempts_dict(attempts)

        exptiles = []
        for i in range(self.n_cat - 1):
            exptile = ExpTile(
                type='nothing_is_guessed',
                words_n_attempts=n_max_attempts_dict['cat_words'][i]['n_attempts'],
                words_max_attempts=n_max_attempts_dict['cat_words'][i]['max_attempts'],
                explanation_n_attempts=n_max_attempts_dict['cat_explanation'][i]['n_attempts'],
                explanation_max_attempts=n_max_attempts_dict['cat_explanation'][i]['max_attempts'],
                id=i,
            )
            if mode == 'tournament':
                if n_max_attempts_dict['cat_words'][i]['n_attempts'] >= n_max_attempts_dict['cat_words'][i]['max_attempts'] and not n_max_attempts_dict['cat_words'][i]['guessed']:
                    exptile.type = 'no_more_guesses'
                if n_max_attempts_dict['cat_explanation'][i]['n_attempts'] >= n_max_attempts_dict['cat_explanation'][i]['max_attempts']:
                    exptile.type = 'no_more_guesses'
            exptiles.append(exptile)

        if not attempts_info or not attempts_info.last_attempt:
            exptiles[0].type = 'first_nothing_is_guessed'
            return exptiles

        state = json.loads(attempts_info.last_attempt.state)

        if len(state['guessed_explanations']) + len(state['guessed_words']) >= self.n_cat:
            exptiles.append(ExpTile(
                explanation_n_attempts=n_max_attempts_dict['cat_explanation'][self.n_cat - 1]['n_attempts'],
                explanation_max_attempts=n_max_attempts_dict['cat_explanation'][self.n_cat - 1]['max_attempts'],
                id=self.n_cat - 1
            ))

        current_row = 0

        for words in state['guessed_words']:
            if words in state['guessed_explanations']:
                exptiles[current_row].type = 'explanation_is_guessed'
            elif exptiles[current_row].type != 'no_more_guesses':
                exptiles[current_row].words = words
                exptiles[current_row].type = 'words_are_guessed'
            current_row += 1
        if current_row >= self.n_cat - 1:
            return exptiles
        if exptiles[current_row].type == 'no_more_guesses':
            exptiles = exptiles[:current_row + 1]
            return exptiles
        exptiles[current_row].type = 'first_nothing_is_guessed'
        return exptiles
