__all__ = ['AlphabettyMove', 'AlphabettyMoveRefusal', 'AlphabettyPuzzle']


def __getattr__(name: str):
    if name in __all__:
        from games.puzzles.alphabetty.play import (
            AlphabettyMove,
            AlphabettyMoveRefusal,
            AlphabettyPuzzle,
        )

        return {
            'AlphabettyMove': AlphabettyMove,
            'AlphabettyMoveRefusal': AlphabettyMoveRefusal,
            'AlphabettyPuzzle': AlphabettyPuzzle,
        }[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
