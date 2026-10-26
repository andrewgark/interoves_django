__all__ = ['SaladSync', 'WordSaladPuzzle']


def __getattr__(name: str):
    if name == 'WordSaladPuzzle':
        from games.puzzles.salad.puzzle import WordSaladPuzzle

        return WordSaladPuzzle
    if name == 'SaladSync':
        from games.puzzles.salad.submission import SaladSync

        return SaladSync
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
