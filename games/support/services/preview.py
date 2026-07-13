from dataclasses import dataclass
from typing import Optional, Tuple

from django.contrib.auth.models import User
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from games.models import Attempt, Game, GameTaskGroup, HTMLPage, Team
from games.views.new_ui import (
    LADDER_GAME_ID,
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    PALINDROMES_GAME_ID,
    SECTION_RULES_GAME_IDS,
    build_task_group_task_context_dicts,
    is_ladder_number_published,
    visible_ladder_links,
    _game_task_group_links,
    _neighbors_by_pk,
)


@dataclass(frozen=True)
class ActorSpec:
    kind: str
    team_name: Optional[str] = None
    user_id: Optional[int] = None
    anon_key: Optional[str] = None
    play_mode: str = 'team'

    def query_params(self):
        params = {}
        if self.kind == 'team' and self.team_name:
            params['team'] = self.team_name
            params['mode'] = 'team'
        elif self.kind == 'user' and self.user_id is not None:
            params['user'] = str(self.user_id)
            params['mode'] = 'personal'
        elif self.kind == 'anon' and self.anon_key:
            params['anon'] = self.anon_key
            params['mode'] = 'personal'
        return params

    def label(self) -> str:
        if self.kind == 'team' and self.team_name:
            team = Team.objects.filter(pk=self.team_name).first()
            if team:
                return team.visible_name or team.name
            return self.team_name
        if self.kind == 'user' and self.user_id is not None:
            user = User.objects.select_related('profile').filter(pk=self.user_id).first()
            if user and hasattr(user, 'profile') and user.profile:
                return '{} {}'.format(user.profile.first_name, user.profile.last_name).strip() or user.username
            return user.username if user else 'user#{}'.format(self.user_id)
        if self.kind == 'anon' and self.anon_key:
            tail = self.anon_key[-8:] if len(self.anon_key) >= 8 else self.anon_key
            return 'Аноним ··{}'.format(tail)
        return '—'

    def dashboard_url(self) -> str:
        if self.kind == 'team' and self.team_name:
            return reverse('support:actor_team', kwargs={'team_name': self.team_name})
        if self.kind == 'user' and self.user_id is not None:
            return reverse('support:actor_user', kwargs={'user_id': self.user_id})
        if self.kind == 'anon' and self.anon_key:
            return reverse('support:actor_anon', kwargs={'anon_key': self.anon_key})
        return reverse('support:hub')


def parse_actor_spec(request) -> ActorSpec:
    actor_raw = (request.GET.get('actor') or '').strip()
    if actor_raw:
        return _parse_actor_token(actor_raw, request.GET.get('mode'))

    team_name = (request.GET.get('team') or '').strip()
    if team_name:
        return ActorSpec(kind='team', team_name=team_name, play_mode='team')

    user_raw = (request.GET.get('user') or '').strip()
    if user_raw.isdigit():
        return ActorSpec(kind='user', user_id=int(user_raw), play_mode='personal')

    anon_key = (request.GET.get('anon') or request.GET.get('anon_key') or '').strip()
    if anon_key:
        return ActorSpec(kind='anon', anon_key=anon_key, play_mode='personal')

    raise Http404('Укажите актора: ?team=…, ?user=…, ?anon=… или ?actor=team:…')


def _parse_actor_token(token: str, mode: Optional[str]) -> ActorSpec:
    lower = token.lower()
    prefixes = (
        ('team:', 'team'),
        ('user:', 'user'),
        ('anon:', 'anon'),
    )
    for prefix, kind in prefixes:
        if lower.startswith(prefix):
            value = token[len(prefix):].strip()
            if kind == 'team':
                return ActorSpec(kind='team', team_name=value, play_mode='team')
            if kind == 'user' and value.isdigit():
                return ActorSpec(kind='user', user_id=int(value), play_mode='personal')
            if kind == 'anon':
                return ActorSpec(kind='anon', anon_key=value, play_mode='personal')
    raise Http404('Неверный формат actor=…')


def resolve_actor(spec: ActorSpec) -> Tuple[Optional[Team], Optional[User], Optional[str]]:
    if spec.kind == 'team':
        team = Team.objects.filter(pk=spec.team_name).first()
        if team is None:
            raise Http404('Команда не найдена')
        return team, None, None
    if spec.kind == 'user':
        user = User.objects.select_related('profile').filter(pk=spec.user_id).first()
        if user is None:
            raise Http404('Пользователь не найден')
        return None, user, None
    if spec.kind == 'anon':
        if not spec.anon_key:
            raise Http404('Пустой anon key')
        return None, None, spec.anon_key
    raise Http404('Неизвестный актор')


def preview_game_url(game_id: str, spec: ActorSpec) -> str:
    base = reverse('support:preview_game', kwargs={'game_id': game_id})
    params = spec.query_params()
    if not params:
        return base
    from urllib.parse import urlencode
    return '{}?{}'.format(base, urlencode(params))


def preview_task_group_url(game_id: str, task_group_number: str, spec: ActorSpec) -> str:
    base = reverse(
        'support:preview_task_group',
        kwargs={'game_id': game_id, 'task_group_number': task_group_number},
    )
    params = spec.query_params()
    if not params:
        return base
    from urllib.parse import urlencode
    return '{}?{}'.format(base, urlencode(params))


def build_preview_game_context(game: Game, spec: ActorSpec):
    from games.views.new_ui import _game_task_group_links

    placements = list(_game_task_group_links(game))
    if game.id == LADDER_GAME_ID:
        placements = list(visible_ladder_links(placements, game))
    rows = []
    for placement in placements:
        rows.append({
            'number': placement.number,
            'name': placement.name,
            'label': placement.task_group.label or str(placement.task_group_id),
            'preview_url': preview_task_group_url(game.id, placement.number, spec),
        })
    return {
        'game': game,
        'actor_spec': spec,
        'actor_label': spec.label(),
        'actor_dashboard_url': spec.dashboard_url(),
        'rows': rows,
        'page_title': 'Preview · {}'.format(game.outside_name or game.name or game.id),
    }


def build_preview_task_group_context(game_id: str, task_group_number: str, spec: ActorSpec):
    from games.models import AudioManager, ImageManager
    from games.views.new_ui import _task_group_page_nav_context

    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        raise Http404('Игра не найдена')

    team, user, anon_key = resolve_actor(spec)
    play_mode = 'team' if team is not None else 'personal'

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    placement = (
        GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
        .filter(game=game, number=str(task_group_number))
        .first()
    )
    if not placement:
        raise Http404('Task group не найден')

    task_group = placement.task_group
    if game.id == LADDER_GAME_ID and not is_ladder_number_published(game, task_group_number):
        raise Http404('Лесенка ещё не опубликована')

    if game.id == LADDER_GAME_ID:
        visible_links = list(visible_ladder_links(_game_task_group_links(game), game))
        prev_tg, next_tg = _neighbors_by_pk(visible_links, placement)
    else:
        prev_tg, next_tg = GameTaskGroup.prev_next_for(game, placement)

    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    section_rules_type = game.id if game.id in SECTION_RULES_GAME_IDS else None
    section_tutorial_html = None
    if section_rules_type:
        try:
            page = HTMLPage.objects.get(name='section_tutorial_' + section_rules_type)
            section_tutorial_html = page.html or ''
        except HTMLPage.DoesNotExist:
            pass
    show_palindrome_rules = game.id == PALINDROMES_GAME_ID
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        tg_rules = placement.task_group.rules
        if tg_rules and (tg_rules.html or '').strip():
            section_rules_type = None
            section_tutorial_html = None
            show_palindrome_rules = False

    ctx_dicts = build_task_group_task_context_dicts(game, task_group, tasks, team, user, anon_key, mode)
    prev_url = (
        preview_task_group_url(game.id, prev_tg.number, spec) if prev_tg else None
    )
    next_url = (
        preview_task_group_url(game.id, next_tg.number, spec) if next_tg else None
    )
    return {
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'raddle_data': ctx_dicts['raddle_data'],
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'can_like': False,
        'has_profile_user': user is not None,
        'mode': mode,
        'play_mode': play_mode,
        'actor_anon_key': anon_key,
        'actor_team': team,
        'actor_user': user,
        'show_palindrome_rules': show_palindrome_rules,
        'section_rules_type': section_rules_type,
        'section_tutorial_html': section_tutorial_html,
        'tg_number': placement.number,
        'tg_name': placement.name,
        'support_preview': True,
        'actor_spec': spec,
        'actor_label': spec.label(),
        'actor_dashboard_url': spec.dashboard_url(),
        'preview_game_url': preview_game_url(game.id, spec),
        'prev_task_group_url': prev_url,
        'next_task_group_url': next_url,
        'back_url': preview_game_url(game.id, spec),
        'back_label': 'К списку TG',
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        **_task_group_page_nav_context(game, prev_tg=prev_tg, next_tg=next_tg),
        'page_title': 'Preview · {} · {}'.format(
            game.outside_name or game.name,
            placement.name,
        ),
    }
