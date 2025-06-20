from collections import defaultdict
import json
from decimal import Decimal
from django.shortcuts import render, get_object_or_404
from games.access import game_has_ended
from games.models import Game, Project, Team


def total_results_page(request, project_id):
    team_to_results = defaultdict(lambda : defaultdict(lambda: 0))
    project = get_object_or_404(Project, id=project_id)
    games = [
        game for game in Game.objects.filter(project=project)
        if game.is_ready and not game.is_testing and game_has_ended(game)
    ]
    games = sorted(games, key=lambda game: (game.start_time, game.name))

    for game in games:
        try:
            team_to_game_score = json.loads(game.results)
        except:
            team_to_game_score = {}
        for team_name, res in team_to_game_score.items():
            team = get_object_or_404(Team, name=team_name)
            points = Decimal(res['score'])
            place = int(res['place'])
            if place == 1:
                team_to_results[team]['place1'] += 1
            if place == 2:
                team_to_results[team]['place2'] += 1
            if place == 3:
                team_to_results[team]['place3'] += 1
            team_to_results[team]['n_games'] += 1
            team_to_results[team]['n_points'] += points
            team_to_results[team]['avg_points'] = team_to_results[team]['n_points'] / team_to_results[team]['n_games']
    teams_sorted = sorted(
        team_to_results.keys(),
        key=lambda x: (
            -team_to_results[x]['place1'],
            -team_to_results[x]['place2'],
            -team_to_results[x]['place3'],
            -team_to_results[x]['n_points'],
            team_to_results[x]['n_games']
        )
    )
    return render(request, 'total_results.html', {
        'project': project,
        'teams_sorted': teams_sorted,
        'team_to_results': team_to_results,
    }) 