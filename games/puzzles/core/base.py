"""Common surface of a task type.

Subclasses own how a puzzle is parsed and drawn. Checking a raw tournament string is wired through
``games.puzzles.core.check_runtime.CheckerFactory`` (``games.check`` is a
compat shim for tests and scripts).
"""


class Puzzle:
    task_type = ''

    @staticmethod
    def build_board(**kwargs):
        raise NotImplementedError

    @staticmethod
    def prepare_board(**kwargs):
        raise NotImplementedError
