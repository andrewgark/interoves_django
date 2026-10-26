__all__ = ['CensorlyMoveRefusal', 'CensorlyPuzzle']


def __getattr__(name):
    if name in __all__:
        from games.puzzles.censorly.play import CensorlyMoveRefusal, CensorlyPuzzle

        return {'CensorlyMoveRefusal': CensorlyMoveRefusal, 'CensorlyPuzzle': CensorlyPuzzle}[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
