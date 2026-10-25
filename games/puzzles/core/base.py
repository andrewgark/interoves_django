"""Common surface of a task type.

Subclasses own how a puzzle is parsed and drawn. Checking a raw tournament
string still lives in ``games.check`` until that checker is only used by one
puzzle.
"""


class Puzzle:
    task_type = ''

    @staticmethod
    def build_board(**kwargs):
        raise NotImplementedError

    @staticmethod
    def prepare_board(**kwargs):
        raise NotImplementedError
