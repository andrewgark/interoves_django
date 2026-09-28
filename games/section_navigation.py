"""Navigation context shared by section game pages."""


def section_ui_context(
    game,
    *,
    sections_project_id='sections',
    ladder_game_id='ladder',
    alphabetty_game_id='alphabetty',
    salad_game_id='salad',
):
    """Return section-specific flags and the canonical results URL."""
    if getattr(game, 'project_id', None) != sections_project_id:
        return {}
    from games.section_paths import section_results_path

    return {
        'is_ladder_section': game.id == ladder_game_id,
        'is_alphabetty_section': game.id == alphabetty_game_id,
        'is_salad_section': game.id == salad_game_id,
        'section_results_url': section_results_path(game.id),
    }
