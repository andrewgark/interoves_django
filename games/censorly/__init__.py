"""Цензурки: Redactle-style daily Wikipedia guessing game."""

CENSORLY_GAME_ID = 'censorly'
CENSORLY_TASK_TYPE = 'censorly'
CENSORLY_CHECKER_ID = 'censorly'
CENSORLY_TAGS_KEY = 'censorly'

# Show known morphological endings/postfixes in gray inside masked words (***ого).
# Flip to False to hide endings again without other code changes.
CENSORLY_SHOW_MASK_ENDINGS = True

# Bump when tokenization, lemmas, stop words, or endings change.
# Stored puzzles keep their article text and rebuild the split when this differs.
CENSORLY_SPLITTER_VERSION = 1
