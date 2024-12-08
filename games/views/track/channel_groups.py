CHANNEL_GROUPS = {
    'game': (lambda game_id: f'track.game.{game_id}'),
    'game_team': (lambda game_id, team_name_hash: f'track.game.{game_id}.team.{team_name_hash}'),

    # 'game_results': (lambda game_id: f'track.game.{game_id}.results'),
    # 'total_results': (lambda project_id: f'track.project.{project_id}.total_results'),
    'project': (lambda project_id: f'track.project.{project_id}'),
}
