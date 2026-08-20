import json
import logging

from django.contrib.auth.views import LoginView
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from games.models import Game, Team
from games.support.access import support_console_required
from games.support.services.actor import (
    build_anon_context,
    build_game_context,
    build_team_context,
    build_user_context,
)
from games.support.services.chain import build_chain_context, format_chain_state
from games.support.services.games import get_all_games_by_project
from games.support.services.alphabetty import (
    AlphabettySupportError,
    alphabetty_dashboard_context,
    create_alphabetty,
    delete_alphabetty,
    forbid_alphabetty,
    generate_more as alphabetty_generate_more,
    get_alphabetty_detail,
    reorder_alphabetty,
    set_publish_start as alphabetty_set_publish_start_service,
    unban_alphabetty_word,
    update_alphabetty,
)
from games.support.services.week_tasks import (
    WeekTaskSupportError,
    create_week_task,
    delete_week_task,
    forbid_week_task,
    generate_more as week_tasks_generate_more,
    get_pool_catalog as week_tasks_get_pool_catalog,
    get_week_task_detail,
    reorder_week_tasks,
    set_publish_start as week_tasks_set_publish_start_service,
    unban_week_task_unit,
    update_week_task,
    week_task_dashboard_context,
)
from games.support.services.ladders import (
    LadderSupportError,
    create_ladder,
    dashboard_context as ladders_dashboard_context,
    delete_ladder,
    get_ladder_detail,
    reorder_ladders,
    set_publish_start,
    update_ladder,
)
from games.support.services.word_salad import (
    WordSaladSupportError,
    create_word_salad,
    dashboard_context as word_salad_dashboard_context,
    delete_word_salad,
    get_word_salad_detail,
    list_word_salad_rows,
    reorder_word_salads,
    update_word_salad,
)
from games.ladder_offer import (
    LadderOfferError,
    accept_offer,
    dashboard_offers_context,
    list_sent_offers,
    offer_for_link,
    offers_by_link_ids,
    request_revision,
    reset_all_raddle_progress,
    serialize_offer,
    update_offer_content,
)
from games.alphabetty_offer import (
    AlphabettyOfferError,
    accept_offer as accept_alphabetty_offer,
    dashboard_offers_context as alphabetty_dashboard_offers_context,
    list_sent_offers as list_sent_alphabetty_offers,
    offer_for_link as alphabetty_offer_for_link,
    offers_by_link_ids as alphabetty_offers_by_link_ids,
    request_revision as request_alphabetty_revision,
    reset_all_alphabetty_progress,
    serialize_offer as serialize_alphabetty_offer,
    update_offer_content as update_alphabetty_offer_content,
)
from games.models import AlphabettyOffer, LadderOffer, Task
from games.support.services.live import get_live_feed
from games.support.services.preview import (
    ActorSpec,
    build_preview_game_context,
    build_preview_task_group_context,
    parse_actor_spec,
)
from games.support.services.pending import get_pending_queue
from games.support.services.search import search
from games.support.services.sections import get_sections_dashboard
from games.support.services.stats import collect_support_stats
from games.support.services.stuck import get_stuck_teams
from games.support.services.social import (
    SocialSupportError,
    create_post_with_plan as social_create_post_with_plan,
    delete_post as social_delete_post,
    get_post as social_get_post,
    list_posts as social_list_posts,
    publish_network as social_publish_network,
    serialize_post as social_serialize_post,
    sync_from_telegram as social_sync_from_telegram,
    update_post as social_update_post,
)

logger = logging.getLogger(__name__)


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _ordered_ids_from_request(request):
    body = _json_body(request)
    if body is None:
        raise ValueError('Некорректный JSON')
    if not isinstance(body, dict):
        raise ValueError('JSON должен быть объектом')
    order = body.get('order')
    if not isinstance(order, list):
        raise ValueError('Нужен order: [link_id, …]')
    try:
        return [int(value) for value in order]
    except (TypeError, ValueError) as exc:
        raise ValueError('order должен быть списком int') from exc


def _ladder_error_response(exc: LadderSupportError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


class SupportLoginView(LoginView):
    template_name = 'support/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse('support:hub')


def _feed_kwargs_from_request(request):
    kind = request.GET.get('kind', 'all')
    if kind not in ('all', 'attempts', 'hints'):
        kind = 'all'
    status = request.GET.get('status') or None
    if status and status not in ('Ok', 'Pending', 'Partial', 'Wrong'):
        status = None
    hours_raw = request.GET.get('hours')
    hours = None
    if hours_raw:
        try:
            hours = int(hours_raw)
        except (TypeError, ValueError):
            hours = None
    per_page = 50
    per_page_raw = request.GET.get('per_page')
    if per_page_raw:
        try:
            per_page = min(200, max(10, int(per_page_raw)))
        except (TypeError, ValueError):
            pass
    page = 1
    page_raw = request.GET.get('page')
    if page_raw:
        try:
            page = max(1, int(page_raw))
        except (TypeError, ValueError):
            pass
    return {
        'kind': kind,
        'status': status,
        'hours': hours,
        'page': page,
        'per_page': per_page,
    }


def _feed_filters(request):
    return {
        'kind': request.GET.get('kind', 'all'),
        'status': request.GET.get('status', ''),
        'hours': request.GET.get('hours', ''),
        'page': request.GET.get('page', ''),
        'per_page': request.GET.get('per_page', ''),
    }


@support_console_required
def hub(request):
    query = (request.GET.get('q') or '').strip()
    results = search(query) if query else []
    return render(request, 'support/hub.html', {
        'page_title': 'Support',
        'query': query,
        'results': results,
    })


@support_console_required
def games_browse(request):
    project_id = (request.GET.get('project') or '').strip() or None
    return render(request, 'support/games.html', {
        'page_title': 'Игры',
        'game_groups': get_all_games_by_project(project_id=project_id),
        'project_id': project_id or '',
    })


@support_console_required
def sections_dashboard(request):
    return render(request, 'support/sections.html', {
        'page_title': 'Разделы',
        'sections': get_sections_dashboard(),
    })


@support_console_required
def word_salad_dashboard(request):
    edit_raw = (request.GET.get('edit') or '').strip()
    edit_link_id = int(edit_raw) if edit_raw.isdigit() else None
    try:
        ctx = word_salad_dashboard_context(edit_link_id=edit_link_id)
    except WordSaladSupportError as exc:
        raise Http404(str(exc)) from exc
    return render(request, 'support/word_salad.html', ctx)


def _word_salad_error_response(exc: WordSaladSupportError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


def _word_salad_body(request):
    if request.content_type == 'application/json':
        return _json_body(request)
    return request.POST


@support_console_required
@require_POST
def word_salad_create(request):
    body = _word_salad_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    at_number = body.get('at_number')
    try:
        detail = create_word_salad(at_number=at_number if at_number not in (None, '') else None)
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc)
    except Exception as exc:
        logger.exception('Word Salad create failed')
        return _word_salad_error_response(exc, status=500)
    return JsonResponse({
        'ok': True,
        'item': detail,
        'detail': detail,
        'rows': [row.to_dict() for row in list_word_salad_rows()],
    })


@support_console_required
@require_GET
def word_salad_detail_json(request, link_id):
    try:
        detail = get_word_salad_detail(link_id)
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc, status=404)
    return JsonResponse({'ok': True, 'item': detail, 'detail': detail})


@support_console_required
@require_POST
def word_salad_reorder(request):
    try:
        ordered_ids = _ordered_ids_from_request(request)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        rows = reorder_word_salads(ordered_ids)
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


@support_console_required
@require_POST
def word_salad_save(request, link_id):
    body = _word_salad_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    intro = str(body.get('intro') or '').strip()
    grid_text = body.get('grid_text') or ''
    words_text = body.get('words_text') or ''
    name = body.get('name')
    try:
        detail = update_word_salad(link_id, intro=intro, grid_text=grid_text, words_text=words_text, name=name)
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc)
    except Exception as exc:
        logger.exception('Word Salad save failed link_id=%s', link_id)
        return _word_salad_error_response(exc, status=500)
    return JsonResponse({
        'ok': True,
        'item': detail,
        'detail': detail,
        'rows': [row.to_dict() for row in list_word_salad_rows()],
    })


@support_console_required
@require_POST
def word_salad_delete(request, link_id):
    try:
        rows = delete_word_salad(link_id)
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc)
    except Exception as exc:
        logger.exception('Word Salad delete failed link_id=%s', link_id)
        return _word_salad_error_response(exc, status=500)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


@support_console_required
def stats_dashboard(request):
    hours_raw = request.GET.get('hours', '24')
    try:
        hours = max(1, min(168, int(hours_raw)))
    except (TypeError, ValueError):
        hours = 24
    return render(request, 'support/stats.html', {
        'page_title': 'Статистика',
        'stats': collect_support_stats(hours=hours),
        'hours': hours,
    })


@support_console_required
def actor_team(request, team_name):
    team = Team.objects.filter(name=team_name).first()
    if team is None:
        raise Http404('Team not found')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_team_context(team, feed_kwargs=feed_kwargs)
    ctx.update({
        'page_title': ctx['actor_title'],
        'feed_filters': _feed_filters(request),
        'actor_spec': ActorSpec(kind='team', team_name=team.name, play_mode='team'),
    })
    return render(request, 'support/actor.html', ctx)


@support_console_required
def actor_user(request, user_id):
    from django.contrib.auth.models import User

    user = User.objects.select_related('profile').filter(pk=user_id).first()
    if user is None:
        raise Http404('User not found')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_user_context(user, feed_kwargs=feed_kwargs)
    ctx.update({
        'page_title': ctx['actor_title'],
        'feed_filters': _feed_filters(request),
        'actor_spec': ActorSpec(kind='user', user_id=user.pk, play_mode='personal'),
    })
    return render(request, 'support/actor.html', ctx)


@support_console_required
def actor_anon(request, anon_key):
    if not anon_key:
        raise Http404('Anon key required')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_anon_context(anon_key, feed_kwargs=feed_kwargs)
    ctx.update({
        'page_title': ctx['actor_title'],
        'feed_filters': _feed_filters(request),
        'actor_spec': ActorSpec(kind='anon', anon_key=anon_key, play_mode='personal'),
    })
    return render(request, 'support/actor.html', ctx)


@support_console_required
def game_dashboard(request, game_id):
    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        raise Http404('Game not found')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_game_context(game, feed_kwargs=feed_kwargs)
    minutes_raw = request.GET.get('stuck_minutes', '30')
    try:
        stuck_minutes = int(minutes_raw)
    except (TypeError, ValueError):
        stuck_minutes = 30
    ctx.update({
        'page_title': game.outside_name or game.name or game.id,
        'feed_filters': _feed_filters(request),
        'stuck_teams': get_stuck_teams(game, minutes=stuck_minutes),
        'stuck_minutes': stuck_minutes,
    })
    return render(request, 'support/game.html', ctx)


@support_console_required
def preview_game(request, game_id):
    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        raise Http404('Game not found')
    spec = parse_actor_spec(request)
    ctx = build_preview_game_context(game, spec)
    return render(request, 'support/preview_game.html', ctx)


@support_console_required
def preview_task_group(request, game_id, task_group_number):
    spec = parse_actor_spec(request)
    ctx = build_preview_task_group_context(game_id, task_group_number, spec)
    return render(request, 'support/preview_task_group.html', ctx)


@support_console_required
def pending_queue(request):
    return render(request, 'support/pending.html', {
        'page_title': 'Pending',
        'items': get_pending_queue(),
    })


@support_console_required
def live_dashboard(request):
    hours_raw = request.GET.get('hours', '2')
    try:
        hours = max(1, min(24, int(hours_raw)))
    except (TypeError, ValueError):
        hours = 2
    poll_raw = request.GET.get('poll', '30')
    try:
        poll_seconds = max(0, min(300, int(poll_raw)))
    except (TypeError, ValueError):
        poll_seconds = 30
    game_id = (request.GET.get('game') or '').strip() or None
    games, feed = get_live_feed(hours=hours, game_id=game_id)
    return render(request, 'support/live.html', {
        'page_title': 'Live',
        'games': games,
        'feed': feed,
        'hours': hours,
        'poll_seconds': poll_seconds,
        'game_id': game_id or '',
    })


@support_console_required
def live_feed_json(request):
    hours_raw = request.GET.get('hours', '2')
    try:
        hours = max(1, min(24, int(hours_raw)))
    except (TypeError, ValueError):
        hours = 2
    game_id = (request.GET.get('game') or '').strip() or None
    games, feed = get_live_feed(hours=hours, game_id=game_id)
    rows = []
    for item in feed:
        rows.append({
            'time': item.time.isoformat() if item.time else None,
            'kind': item.kind,
            'actor_label': item.actor_label,
            'actor_url': item.actor_url,
            'game_id': item.game_id,
            'status': item.status,
            'detail': item.detail,
            'submission_text': item.submission_text,
            'correct_answer': item.correct_answer,
            'object_id': item.object_id,
            'chain_url': item.chain_url,
        })
    return JsonResponse({
        'games': [g.id for g in games],
        'rows': rows,
    })


@support_console_required
def chain_attempt(request, attempt_id):
    ctx = build_chain_context(attempt_id)
    if not ctx:
        raise Http404('Chain context not found')
    ctx['state_formatter'] = format_chain_state
    ctx['formatted_chain_states'] = {
        row.game_mode: format_chain_state(row.state)
        for row in ctx['chain_rows']
    }
    return render(request, 'support/chain.html', ctx)


@support_console_required
def ladders_dashboard(request):
    ctx = ladders_dashboard_context()
    ctx.update(dashboard_offers_context())
    offer_map = offers_by_link_ids([r.link_id for r in ctx.get('ladders') or []])
    ctx['offer_by_link_json'] = {
        str(k): v.to_dict() for k, v in offer_map.items()
    }
    ctx['page_title'] = 'Лесенки'
    return render(request, 'support/ladders.html', ctx)


def _offer_error_response(exc: LadderOfferError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


@support_console_required
@require_GET
def ladder_offers_list_json(request):
    rows = list_sent_offers()
    return JsonResponse({'ok': True, 'offers': [r.to_dict() for r in rows]})


@support_console_required
@require_GET
def ladder_offer_detail_json(request, offer_id):
    offer = (
        LadderOffer.objects.select_related('task_group', 'accepted_link', 'user', 'user__profile')
        .filter(pk=offer_id)
        .first()
    )
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    return JsonResponse({'ok': True, 'offer': serialize_offer(offer).to_dict()})


@support_console_required
@require_POST
def ladder_offer_save(request, offer_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    offer = LadderOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    words = body.get('words')
    hints = body.get('hints')
    if not isinstance(words, list) or not isinstance(hints, list):
        return JsonResponse({'ok': False, 'error': 'Нужны words и hints'}, status=400)
    try:
        offer = update_offer_content(
            offer,
            words=words,
            hints=hints,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or ''),
            comment=str(body.get('comment') or ''),
            mixed_script=bool(body.get('mixed_script')),
            allow_non_draft=True,
        )
    except LadderOfferError as exc:
        return _offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_offers()],
    })


@support_console_required
@require_POST
def ladder_offer_accept(request, offer_id):
    offer = LadderOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = accept_offer(offer)
        rows = ladders_dashboard_context()['ladders']
    except LadderOfferError as exc:
        return _offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_offers()],
        'ladders': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def ladder_offer_request_revision(request, offer_id):
    body = _json_body(request) or {}
    offer = LadderOffer.objects.filter(pk=offer_id).first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = request_revision(offer, admin_note=str(body.get('admin_note') or ''))
    except LadderOfferError as exc:
        return _offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_offers()],
    })


@support_console_required
@require_POST
def ladder_link_request_revision(request, link_id):
    body = _json_body(request) or {}
    offer = offer_for_link(int(link_id))
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Нет автора в системе для этой лесенки'}, status=404)
    if offer.status == LadderOffer.STATUS_DRAFT:
        return JsonResponse({'ok': False, 'error': 'Уже на доработке'}, status=400)
    try:
        offer = request_revision(offer, admin_note=str(body.get('admin_note') or ''))
    except LadderOfferError as exc:
        return _offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_offers()],
    })


@support_console_required
@require_POST
def ladder_offer_reset_progress(request, offer_id):
    offer = LadderOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    task = Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'Нет задания'}, status=404)
    try:
        stats = reset_all_raddle_progress(task=task)
    except LadderOfferError as exc:
        return _offer_error_response(exc)
    return JsonResponse({'ok': True, 'reset': stats})


@support_console_required
@require_POST
def ladders_reset_progress(request, link_id):
    try:
        detail = get_ladder_detail(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except LadderSupportError as exc:
        return _ladder_error_response(exc, status=404)
    task_id = detail.get('task_id')
    if not task_id:
        return JsonResponse({'ok': False, 'error': 'Нет задания'}, status=404)
    task = Task.objects.filter(pk=task_id).first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'Нет задания'}, status=404)
    try:
        stats = reset_all_raddle_progress(task=task)
    except LadderOfferError as exc:
        return _offer_error_response(exc)
    return JsonResponse({'ok': True, 'reset': stats})


@support_console_required
@require_GET
def ladders_detail_json(request, link_id):
    try:
        detail = get_ladder_detail(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except LadderSupportError as exc:
        return _ladder_error_response(exc, status=404)
    return JsonResponse({'ok': True, 'ladder': detail})


@support_console_required
@require_POST
def ladders_reorder(request):
    try:
        ordered_ids = _ordered_ids_from_request(request)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        rows = reorder_ladders(ordered_ids)
    except LadderSupportError as exc:
        return _ladder_error_response(exc)
    return JsonResponse({
        'ok': True,
        'ladders': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def ladders_create(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        at_number = int(body.get('at_number'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен at_number'}, status=400)
    words = body.get('words')
    hints = body.get('hints')
    if words is not None and not isinstance(words, list):
        return JsonResponse({'ok': False, 'error': 'words должен быть списком'}, status=400)
    if hints is not None and not isinstance(hints, list):
        return JsonResponse({'ok': False, 'error': 'hints должен быть списком'}, status=400)
    try:
        detail = create_ladder(
            at_number=at_number,
            words=words,
            hints=hints,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or ''),
            mixed_script=bool(body.get('mixed_script')),
        )
        rows = ladders_dashboard_context()['ladders']
    except LadderSupportError as exc:
        return _ladder_error_response(exc)
    return JsonResponse({
        'ok': True,
        'ladder': detail,
        'ladders': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def ladders_update(request, link_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    words = body.get('words')
    hints = body.get('hints')
    if not isinstance(words, list) or not isinstance(hints, list):
        return JsonResponse({'ok': False, 'error': 'Нужны words и hints (списки)'}, status=400)
    try:
        detail = update_ladder(
            int(link_id),
            words=words,
            hints=hints,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or ''),
            mixed_script=bool(body.get('mixed_script')),
        )
        rows = ladders_dashboard_context()['ladders']
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except LadderSupportError as exc:
        return _ladder_error_response(exc)
    return JsonResponse({
        'ok': True,
        'ladder': detail,
        'ladders': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def ladders_set_publish_start(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    date_iso = body.get('publish_start') or body.get('date')
    if not date_iso:
        return JsonResponse({'ok': False, 'error': 'Нужна publish_start (YYYY-MM-DD)'}, status=400)
    try:
        new_date = set_publish_start(str(date_iso))
        rows = ladders_dashboard_context()['ladders']
    except LadderSupportError as exc:
        return _ladder_error_response(exc)
    return JsonResponse({
        'ok': True,
        'publish_start': new_date,
        'ladders': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def ladders_delete(request, link_id):
    try:
        rows = delete_ladder(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except LadderSupportError as exc:
        return _ladder_error_response(exc)
    return JsonResponse({
        'ok': True,
        'ladders': [r.to_dict() for r in rows],
    })


def _alphabetty_error_response(exc: AlphabettySupportError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


def _alphabetty_offer_error_response(exc: AlphabettyOfferError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


@support_console_required
def alphabetty_dashboard(request):
    ctx = alphabetty_dashboard_context()
    ctx.update(alphabetty_dashboard_offers_context())
    offer_map = alphabetty_offers_by_link_ids([r.link_id for r in ctx.get('rows') or []])
    ctx['offer_by_link_json'] = {
        str(k): v.to_dict() for k, v in offer_map.items()
    }
    ctx['page_title'] = 'Алфавитки'
    return render(request, 'support/alphabetty.html', ctx)


@support_console_required
@require_GET
def alphabetty_offers_list_json(request):
    rows = list_sent_alphabetty_offers()
    return JsonResponse({'ok': True, 'offers': [r.to_dict() for r in rows]})


@support_console_required
@require_GET
def alphabetty_offer_detail_json(request, offer_id):
    offer = (
        AlphabettyOffer.objects.select_related('task_group', 'accepted_link', 'user', 'user__profile')
        .filter(pk=offer_id)
        .first()
    )
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    return JsonResponse({'ok': True, 'offer': serialize_alphabetty_offer(offer).to_dict()})


@support_console_required
@require_POST
def alphabetty_offer_save(request, offer_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    offer = AlphabettyOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = update_alphabetty_offer_content(
            offer,
            word=str(body.get('word') or ''),
            comment=str(body.get('comment') or ''),
            allow_non_draft=True,
        )
    except AlphabettyOfferError as exc:
        return _alphabetty_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_alphabetty_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_alphabetty_offers()],
    })


@support_console_required
@require_POST
def alphabetty_offer_accept(request, offer_id):
    offer = AlphabettyOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = accept_alphabetty_offer(offer)
        rows = alphabetty_dashboard_context()['rows']
    except AlphabettyOfferError as exc:
        return _alphabetty_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_alphabetty_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_alphabetty_offers()],
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def alphabetty_offer_request_revision(request, offer_id):
    body = _json_body(request) or {}
    offer = AlphabettyOffer.objects.filter(pk=offer_id).first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = request_alphabetty_revision(offer, admin_note=str(body.get('admin_note') or ''))
    except AlphabettyOfferError as exc:
        return _alphabetty_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_alphabetty_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_alphabetty_offers()],
    })


@support_console_required
@require_POST
def alphabetty_offer_reset_progress(request, offer_id):
    offer = AlphabettyOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    task = Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'Нет задания'}, status=404)
    try:
        stats = reset_all_alphabetty_progress(task=task)
    except AlphabettyOfferError as exc:
        return _alphabetty_offer_error_response(exc)
    return JsonResponse({'ok': True, 'reset': stats})


@support_console_required
@require_POST
def alphabetty_link_request_revision(request, link_id):
    body = _json_body(request) or {}
    offer = alphabetty_offer_for_link(int(link_id))
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Нет автора в системе для этой алфавитки'}, status=404)
    if offer.status == AlphabettyOffer.STATUS_DRAFT:
        return JsonResponse({'ok': False, 'error': 'Уже на доработке'}, status=400)
    try:
        offer = request_alphabetty_revision(offer, admin_note=str(body.get('admin_note') or ''))
    except AlphabettyOfferError as exc:
        return _alphabetty_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_alphabetty_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_alphabetty_offers()],
    })


@support_console_required
@require_GET
def alphabetty_detail_json(request, link_id):
    try:
        detail = get_alphabetty_detail(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc, status=404)
    return JsonResponse({'ok': True, 'item': detail})


@support_console_required
@require_POST
def alphabetty_reorder(request):
    try:
        ordered_ids = _ordered_ids_from_request(request)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        rows = reorder_alphabetty(ordered_ids)
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({'ok': True, 'rows': [r.to_dict() for r in rows]})


@support_console_required
@require_POST
def alphabetty_create(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        at_number = int(body.get('at_number'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен at_number'}, status=400)
    word = body.get('word')
    try:
        detail = create_alphabetty(
            at_number=at_number,
            word=str(word) if word else None,
        )
        rows = alphabetty_dashboard_context()['rows']
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({
        'ok': True,
        'item': detail,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def alphabetty_update(request, link_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    word = body.get('word')
    if word is None:
        return JsonResponse({'ok': False, 'error': 'Нужно word'}, status=400)
    try:
        detail = update_alphabetty(int(link_id), word=str(word))
        rows = alphabetty_dashboard_context()['rows']
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({
        'ok': True,
        'item': detail,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def alphabetty_set_publish_start(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    date_iso = body.get('publish_start') or body.get('date')
    if not date_iso:
        return JsonResponse({'ok': False, 'error': 'Нужна publish_start (YYYY-MM-DD)'}, status=400)
    try:
        new_date = alphabetty_set_publish_start_service(str(date_iso))
        rows = alphabetty_dashboard_context()['rows']
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({
        'ok': True,
        'publish_start': new_date,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def alphabetty_generate(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        n = int(body.get('n'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен n'}, status=400)
    try:
        result = alphabetty_generate_more(n)
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({
        'ok': True,
        'created_count': result['created_count'],
        'rows': result['rows'],
    })


@support_console_required
@require_POST
def alphabetty_delete(request, link_id):
    try:
        rows = delete_alphabetty(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({
        'ok': True,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def alphabetty_forbid(request, link_id):
    try:
        result = forbid_alphabetty(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except AlphabettySupportError as exc:
        return _alphabetty_error_response(exc)
    return JsonResponse({'ok': True, **result})


@support_console_required
@require_POST
def alphabetty_unban(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    word = body.get('word')
    if not word:
        return JsonResponse({'ok': False, 'error': 'Нужно word'}, status=400)
    banned = unban_alphabetty_word(str(word))
    return JsonResponse({'ok': True, 'banned': banned})


def _week_tasks_error_response(exc: WeekTaskSupportError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


@support_console_required
def week_tasks_dashboard(request):
    ctx = week_task_dashboard_context()
    ctx['page_title'] = 'Задания недели'
    return render(request, 'support/week_tasks.html', ctx)


@support_console_required
@require_GET
def week_tasks_detail_json(request, link_id):
    try:
        detail = get_week_task_detail(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc, status=404)
    return JsonResponse({'ok': True, 'item': detail})


@support_console_required
@require_POST
def week_tasks_reorder(request):
    try:
        ordered_ids = _ordered_ids_from_request(request)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        rows = reorder_week_tasks(ordered_ids)
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({'ok': True, 'rows': [r.to_dict() for r in rows]})


def _week_tasks_source_kwargs(body):
    """Общие поля выбора источника из JSON body."""
    kwargs = {}
    if 'source_task_group_id' in body and body.get('source_task_group_id') not in (None, ''):
        try:
            kwargs['source_task_group_id'] = int(body.get('source_task_group_id'))
        except (TypeError, ValueError):
            raise ValueError('Некорректный source_task_group_id')
    if 'major' in body:
        major = body.get('major')
        kwargs['major'] = None if major in (None, '') else str(major)
    if 'task_numbers' in body and body.get('task_numbers') is not None:
        nums = body.get('task_numbers')
        if not isinstance(nums, list):
            raise ValueError('task_numbers должен быть списком')
        kwargs['task_numbers'] = [str(x) for x in nums]
    return kwargs


@support_console_required
@require_GET
def week_tasks_pool_catalog(request):
    try:
        catalog = week_tasks_get_pool_catalog()
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc, status=404)
    return JsonResponse({'ok': True, 'games': catalog})


@support_console_required
@require_POST
def week_tasks_create(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        at_number = int(body.get('at_number'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен at_number'}, status=400)
    try:
        source_kwargs = _week_tasks_source_kwargs(body)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    name = body.get('name')
    try:
        detail = create_week_task(
            at_number=at_number,
            name=None if name is None else str(name),
            **source_kwargs,
        )
        rows = week_task_dashboard_context()['rows']
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({
        'ok': True,
        'item': detail,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def week_tasks_update(request, link_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        source_kwargs = _week_tasks_source_kwargs(body)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    name = body.get('name')
    if name is None and not source_kwargs:
        return JsonResponse({'ok': False, 'error': 'Нужно name или источник'}, status=400)
    try:
        detail = update_week_task(
            int(link_id),
            name=None if name is None else str(name),
            **source_kwargs,
        )
        rows = week_task_dashboard_context()['rows']
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({
        'ok': True,
        'item': detail,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def week_tasks_set_publish_start(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    date_iso = body.get('publish_start') or body.get('date')
    if not date_iso:
        return JsonResponse({'ok': False, 'error': 'Нужна publish_start (YYYY-MM-DD)'}, status=400)
    try:
        new_date = week_tasks_set_publish_start_service(str(date_iso))
        rows = week_task_dashboard_context()['rows']
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({
        'ok': True,
        'publish_start': new_date,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def week_tasks_generate(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        n = int(body.get('n'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен n'}, status=400)
    try:
        result = week_tasks_generate_more(n)
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({
        'ok': True,
        'created_count': result['created_count'],
        'rows': result['rows'],
    })


@support_console_required
@require_POST
def week_tasks_delete(request, link_id):
    try:
        rows = delete_week_task(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({
        'ok': True,
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def week_tasks_forbid(request, link_id):
    try:
        result = forbid_week_task(int(link_id))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный id'}, status=400)
    except WeekTaskSupportError as exc:
        return _week_tasks_error_response(exc)
    return JsonResponse({'ok': True, **result})


@support_console_required
@require_POST
def week_tasks_unban(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        task_group_id = int(body.get('task_group_id'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен task_group_id'}, status=400)
    nums = body.get('task_numbers')
    task_numbers = None
    if nums is not None:
        if not isinstance(nums, list):
            return JsonResponse({'ok': False, 'error': 'task_numbers должен быть списком'}, status=400)
        task_numbers = [str(x) for x in nums]
    banned = unban_week_task_unit(
        task_group_id=task_group_id,
        task_numbers=task_numbers,
    )
    return JsonResponse({'ok': True, 'banned': banned})


def _social_error_response(exc: SocialSupportError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


@support_console_required
def social_dashboard(request):
    posts = social_list_posts()
    return render(request, 'support/social.html', {
        'page_title': 'Посты',
        'posts_json': posts,
    })


@support_console_required
@require_POST
def social_create(request):
    caption = (request.POST.get('caption') or '').strip()
    image = request.FILES.get('image')
    mode = (request.POST.get('mode') or 'draft').strip().lower()
    schedule_at = request.POST.get('schedule_at') or None
    networks = request.POST.getlist('networks')
    if not networks:
        raw = request.POST.get('networks') or ''
        networks = [n.strip() for n in raw.split(',') if n.strip()]
    try:
        post = social_create_post_with_plan(
            caption=caption,
            image_file=image,
            networks=networks,
            mode=mode,
            schedule_at=schedule_at,
        )
    except SocialSupportError as exc:
        return _social_error_response(exc)
    return JsonResponse({'ok': True, 'post': social_serialize_post(post)})


@support_console_required
@require_POST
def social_update(request, post_id):
    try:
        post = social_get_post(post_id)
    except SocialSupportError as exc:
        return _social_error_response(exc, status=404)
    caption = request.POST.get('caption')
    image = request.FILES.get('image')
    # JSON body fallback for caption-only edits
    if caption is None and not request.FILES:
        body = _json_body(request)
        if body is not None:
            caption = body.get('caption')
    try:
        post = social_update_post(
            post,
            caption=caption if caption is not None else None,
            image_file=image,
        )
    except SocialSupportError as exc:
        return _social_error_response(exc)
    return JsonResponse({'ok': True, 'post': social_serialize_post(post)})


@support_console_required
@require_POST
def social_publish(request, post_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        post = social_get_post(post_id)
        post = social_publish_network(
            post,
            str(body.get('network') or ''),
            force=bool(body.get('force')),
            immediate=bool(body.get('immediate', True)),
            schedule_at=body.get('schedule_at') or body.get('queued_for'),
            action=str(body.get('action') or 'publish'),
        )
    except SocialSupportError as exc:
        return _social_error_response(exc, status=404 if 'not found' in str(exc).lower() else 400)
    return JsonResponse({'ok': True, 'post': social_serialize_post(post)})


@support_console_required
@require_POST
def social_sync_telegram(request, post_id):
    try:
        post = social_get_post(post_id)
        post = social_sync_from_telegram(post)
    except SocialSupportError as exc:
        return _social_error_response(exc, status=404 if 'not found' in str(exc).lower() else 400)
    return JsonResponse({'ok': True, 'post': social_serialize_post(post)})


@support_console_required
@require_POST
def social_delete(request, post_id):
    try:
        post = social_get_post(post_id)
        social_delete_post(post)
    except SocialSupportError as exc:
        return _social_error_response(exc, status=404)
    return JsonResponse({'ok': True, 'id': post_id})
