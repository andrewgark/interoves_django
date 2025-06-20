from django.shortcuts import render, get_object_or_404
from django.views import View
from django.utils import timezone
from django_telegram_login.widgets.constants import SMALL
from django_telegram_login.widgets.generator import create_redirect_login_widget
from games.forms import CreateTeamForm, JoinTeamForm, TicketRequestForm
from games.models import Game, Project
from games.views.util import has_profile, has_team
from interoves_django.settings import TELEGRAM_BOT_NAME


class MainPageView(View):
    project_name = 'main'

    def get_games_list(self, request):
        team = None
        if has_profile(request.user):
            team = request.user.profile.team_on

        games_list = []

        project = get_object_or_404(Project, id=self.project_name)
        for game in Game.objects.filter(project=project):
            if game.has_access('see_game_preview', team=team):
                games_list.append(game)
        return sorted(games_list, key=lambda game: (game.start_time, game.name), reverse=True)

    def get(self, request, *args, **kwargs):
        project = get_object_or_404(Project, id=self.project_name)
        return render(request, 'index.html', {
            'create_team_form': CreateTeamForm(project),
            'join_team_form': JoinTeamForm(project),
            'ticket_request_form': TicketRequestForm(),
            'games': self.get_games_list(request),
            'today': timezone.now(),
            'project': project,
            'telegram_login_widget': create_redirect_login_widget(
                project.get_url(), TELEGRAM_BOT_NAME, size=SMALL, user_photo=True
            )
        }) 