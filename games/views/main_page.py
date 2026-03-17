from django.shortcuts import render, get_object_or_404
from django.views import View
from django.utils import timezone
from django.http import JsonResponse
from django.core.paginator import Paginator
# from django_telegram_login.widgets.constants import SMALL
# from django_telegram_login.widgets.generator import create_redirect_login_widget
from games.forms import CreateTeamForm, JoinTeamForm, TicketRequestForm
from games.models import Game, Project
from games.views.util import has_profile, has_team
# from interoves_django.settings import TELEGRAM_BOT_NAME


class MainPageView(View):
    project_name = 'main'
    games_per_page = 4  # Number of games to load per request

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
        
        # Check if this is an AJAX request for games
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return self.get_games_ajax(request)
        
        # Get all games for initial page load
        all_games = self.get_games_list(request)
        total_games = len(all_games)
        
        return render(request, 'index.html', {
            'create_team_form': CreateTeamForm(project),
            'join_team_form': JoinTeamForm(project),
            'ticket_request_form': TicketRequestForm(),
            'games': all_games[:self.games_per_page],  # Load first batch
            'total_games': total_games,
            'games_per_page': self.games_per_page,
            'today': timezone.now(),
            'project': project,
#            'telegram_login_widget': create_redirect_login_widget(
#                project.get_url(), TELEGRAM_BOT_NAME, size=SMALL, user_photo=True
#            )
        })

    def get_games_ajax(self, request):
        """Handle AJAX requests for loading more games"""
        page = int(request.GET.get('page', 1))
        all_games = self.get_games_list(request)
        
        paginator = Paginator(all_games, self.games_per_page)
        games_page = paginator.get_page(page)
        
        # Render just the games HTML
        games_html = render(request, 'games_grid.html', {
            'games': games_page,
            'page': page,
            'has_next': games_page.has_next(),
            'total_pages': paginator.num_pages
        }).content.decode('utf-8')
        
        return JsonResponse({
            'games_html': games_html,
            'page': page,
            'has_next': games_page.has_next(),
            'total_pages': paginator.num_pages,
            'total_games': len(all_games)
        }) 