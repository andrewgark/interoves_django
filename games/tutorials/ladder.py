"""The immutable demo configuration for the first Ladder tutorial.

This deliberately does not use a Game, Task, Attempt, or any other gameplay
model.  The words and clue order are copied from Ladder #7 in the local
release data; the tutorial owns its own small client-side state.
"""

from dataclasses import dataclass


LADDER_TUTORIAL_VERSION = 'v1'


@dataclass(frozen=True)
class LadderTutorialAdapter:
    words: tuple[str, ...]
    lengths: tuple[int, ...]
    unused_clues: tuple[tuple[int, str, str], ...]
    used_clues: tuple[str, ...]
    first_answer: str
    second_answer: str

    @property
    def initial_solved_indices(self):
        return (0, 1, 2, 7, 8, 9, 10)

    def payload(self):
        return {
            'version': LADDER_TUTORIAL_VERSION,
            'game': 'ladder',
            'words': self.words,
            'lengths': self.lengths,
            'rows': [
                {'index': index, 'word': word, 'length': self.lengths[index],
                 'solved': index in self.initial_solved_indices}
                for index, word in enumerate(self.words)
            ],
            'initial_solved_indices': self.initial_solved_indices,
            'unused_clues': [
                {'index': index, 'text': text, 'answer': answer}
                for index, text, answer in self.unused_clues
            ],
            'used_clues': self.used_clues,
            'first_answer': self.first_answer,
            'second_answer': self.second_answer,
        }

    def ui_context(self):
        """Shape the immutable payload like the production raddle adapter."""
        solved = set(self.initial_solved_indices)
        rows = []
        for index, word in enumerate(self.words):
            length = self.lengths[index]
            rows.append({
                'index': index, 'word': word if index in solved else '',
                'mask_html': '_' * length, 'mask_tokens': ['◼'] * length,
                'mask_placeholder': '_' * length, 'length_label': length,
                'input_format_len': length, 'input_size': length,
                'input_format': '_' * length, 'mask_slots': length,
                'max_length': length, 'is_solved': index in solved,
                'is_playable': False,
                # Production raddle renders the unresolved middle as draft
                # inputs. Tutorial keeps those controls visible too; only the
                # tutorial controller may promote the active edge to a guess.
                'is_draftable': index not in solved,
                'attempts_exhausted': False,
                'is_given': index in (0, len(self.words) - 1),
                'is_default_focus': False, 'is_default_ref': False,
                'assist_tier': 0, 'draft_letters': '',
                'show_clue_btn': False, 'can_clue_assist': False,
                'can_answer_assist': False,
            })
        unused = [
            {
                'index': index, 'text': text, 'display_html': text,
                'blank_template': text, 'prev_word': '', 'next_word': '',
                'prev_solved': False, 'next_solved': False,
                'has_next_slot': True, 'is_revealed': False,
                'is_struck': False, 'clue_variant': '',
                'tutorial_answer': answer,
            }
            for index, text, answer in self.unused_clues
        ]
        used = [
            {'index': index, 'text': text, 'display_html': text}
            for index, text in enumerate(self.used_clues)
        ]
        return {
            'rows': rows, 'unused_hints': unused, 'used_hints': used,
            'title_from': self.words[0], 'title_to': self.words[-1],
            'n_words': len(self.words), 'is_tournament': False,
            'mixed_script': False, 'mixed_script_notice': '',
            'last_word_clue_options': [], 'last_word_dual_clues': False,
        }


LADDER_TUTORIAL = LadderTutorialAdapter(
    words=(
        'ГРЯЗЬ', 'УДАРИТЬ', 'ПАЛЕЦ', 'ВВЕРХ', 'РУКИ', 'ЗОЛОТЫЕ',
        'ВОРОТА', 'ВРАТАРЬ', 'АКИНФЕЕВ', 'ИГОРЬ', 'КНЯЗЬ',
    ),
    lengths=(5, 7, 5, 5, 4, 7, 6, 7, 8, 5, 5),
    # The visible wording follows the requested partially-solved demo. The
    # answer is kept in adapter data, never in a server-side game state.
    unused_clues=(
        (2, '... ПАЛЕЦ — одобрение', 'ВВЕРХ'),
        (3, '... ПАЛЕЦ — поп-группа', 'РУКИ'),
        (4, 'ПАЛЕЦ ... — в Сан-Франциско', 'ЗОЛОТЫЕ'),
        (5, 'У мастера ... ПАЛЕЦ', 'ВОРОТА'),
        (6, 'Он защищает ПАЛЕЦ', 'ВРАТАРЬ'),
    ),
    used_clues=(
        'УДАРИТЬ в ГРЯЗЬ лицом',
        'ПАЛЕЦ о ПАЛЕЦ не УДАРИТЬ',
        'Одиннадцатикратный «ВРАТАРЬ года» → АКИНФЕЕВ',
        'АКИНФЕЕВ — а как его зовут? → ИГОРЬ',
        'Опера «КНЯЗЬ ИГОРЬ»',
    ),
    first_answer='ВВЕРХ',
    second_answer='РУКИ',
)
