from django.utils import timezone


def game_has_started(game, attempt=None):
    time = timezone.now()
    if attempt:
        time = attempt.time
    if time >= game.start_time:
        return True
    return False


def game_has_ended(game, attempt=None):
    time = timezone.now()
    if attempt:
        time = attempt.time
    if time > game.end_time:
        return True
    return False


def game_is_going_now(game, attempt=None):
    return game_has_started(game, attempt) and not game_has_ended(game, attempt)


def get_game_access(game, action, team=None, attempt=None, mode='general'):
    if action == 'play_with_team':
        return team  and get_game_access(game, 'play_without_team', team=team, attempt=attempt, mode=mode)
    if action == 'see_tournament_results':
        return get_game_access(game, 'play_without_team', team=team, mode='tournament')
    if action == 'see_results':
        return get_game_access(game, 'play_without_team', team=team, mode=mode)
    if action == 'see_answer':
        return game_has_ended(game, attempt) and get_game_access(game, 'read_googledoc', team=team, attempt=attempt, mode=mode)
    if action == 'attempt_is_tournament':
        return not game_has_ended(game, attempt) and get_game_access(game, 'play_without_team', team=team, attempt=attempt, mode=mode)
    if action == 'see_game_preview':
        if mode == 'tournament' and not game.is_tournament:
            return False
        if game.is_ready:
            return True
        if game.is_testing and team and team.is_tester:
            return True
        return False
    if action == 'read_googledoc':
        if not get_game_access(game, 'see_game_preview', team=team, mode=mode):
            return False
        return game_has_started(game, attempt)
    if action == 'play_without_team':
        if not get_game_access(game, 'read_googledoc', team=team, mode=mode):
            return False
        return game.is_playable
    raise Exception('Unknown access action: {}'.format(action))
