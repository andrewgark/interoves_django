"""Lexical matching for Цензурки: does a guess open an article word.

``sources``   external dictionaries (pymorphy, Kuznetsova, Tikhonov)
``reviewed``  our hand-reviewed tables under ``data/``
``context``   what the sentence decided about one token
``deciders``  readings, visibility, root bank, word analysis, layers, matcher, index
``match``     gameplay entry points (``analyze_guess``, ``matching_lemmas``)

See docs/censorly-matching-algorithm.md.
"""
