__all__ = ['ReplacementsPuzzle']


def __getattr__(name: str):
    if name == 'ReplacementsPuzzle':
        from games.puzzles.replacements.puzzle import ReplacementsPuzzle

        return ReplacementsPuzzle
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
