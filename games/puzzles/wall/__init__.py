__all__ = ['WallPuzzle']


def __getattr__(name: str):
    if name == 'WallPuzzle':
        from games.puzzles.wall.puzzle import WallPuzzle

        return WallPuzzle
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
