"""Deciders: each answers one question about words and pairs.

``readings``   which dictionary readings a spelling has (``READINGS``)
``visibility`` whether a word is open from the start (``VISIBILITY``)
``rootbank``   lemma → root structures (``ROOT_BANK``)
``analyzer``   spelling (+ article context) → ``Word`` (``ANALYZER``)
``layers``     the relation layers, each with its index keys
``matcher``    the order of layers and gates (``MATCHER``)
``index``      an article's words and postings; ``analyze_guess``
"""
