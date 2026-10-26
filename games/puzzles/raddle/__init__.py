__all__ = ['RaddlePuzzle']


def __getattr__(name: str):
    if name == 'RaddlePuzzle':
        from games.puzzles.raddle.puzzle import RaddlePuzzle

        return RaddlePuzzle
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
