import json
import logging

from django.contrib.auth.views import LoginView
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from games.models import Game, Team
from games.models import Attempt, ChainTaskState, CensorlyCategory, CensorlyObjectType, CensorlyPoolEntry, GameTaskGroup, Task
from games.censorly.pool_admin import DIFFICULTIES as CENSORLY_POOL_DIFFICULTIES, pool_rows, taxonomy as censorly_pool_taxonomy, save_pool_entry
from games.censorly import CENSORLY_TAGS_KEY
from games.censorly.tokenize import build_puzzle_payload
from games.censorly.tokenize import title_content_lemmas
from games.censorly.wiki import WikiFetchError, fetch_article
from django.utils import timezone
from games.support.access import support_console_required
from games.support.queues import dashboard_context as queues_dashboard_context
from games.models import WordSaladRecheckJob
from games.word_salad_recheck import retry_word_salad_recheck, serialize_job
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
    defer_alphabetty,
    restore_alphabetty,
)
from games.support.services.censorly import (
    CensorlySupportError,
    create_at_number as censorly_create_at_number,
    dashboard_context as censorly_dashboard_context,
    accept_censorly_offer,
    defer_censorly,
    generate_from_title as censorly_create_from_title,
    generate_more as censorly_generate_more,
    generate_random as censorly_create_random,
    list_random_rows as censorly_list_random_rows,
    list_censorly_offers,
    refetch_article_text as censorly_refetch_article_text,
    reset_my_progress as censorly_reset_my_progress,
    reject_censorly_offer,
    reorder_censorly,
    restore_censorly,
    set_publish_start as censorly_set_publish_start_service,
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
    defer_week_task,
    restore_week_task,
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
    defer_ladder,
    restore_ladder,
)
from games.support.services.word_salad import (
    WordSaladSupportError,
    create_word_salad,
    dashboard_context as word_salad_dashboard_context,
    delete_word_salad,
    get_word_salad_detail,
    list_word_salad_rows,
    recheck_word_salad,
    reorder_word_salads,
    set_publish_start as word_salad_set_publish_start_service,
    update_word_salad,
    defer_word_salad,
    restore_word_salad,
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
from games.word_salad_offer import (
    WordSaladOfferError,
    accept_offer as accept_word_salad_offer,
    dashboard_offers_context as word_salad_dashboard_offers_context,
    list_sent_offers as list_sent_word_salad_offers,
    offer_for_link as word_salad_offer_for_link,
    offers_by_link_ids as word_salad_offers_by_link_ids,
    request_revision as request_word_salad_revision,
    reset_all_salad_progress,
    serialize_offer as serialize_word_salad_offer,
    update_offer_content as update_word_salad_offer_content,
)
from games.models import AlphabettyOffer, LadderOffer, Task, WordSaladOffer
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
    regenerate_post_image as social_regenerate_post_image,
    serialize_post as social_serialize_post,
    sync_from_telegram as social_sync_from_telegram,
    update_post as social_update_post,
)
from games.views.util import json_boolean_field, parse_json_object_body

logger = logging.getLogger('games.support.views')


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _json_object_body(request):
    """Parse a JSON request whose top-level value must be an object."""
    return parse_json_object_body(request)


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


def _schedule_move_response(operation, link_id):
    try:
        rows = operation(link_id)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


@support_console_required
@require_POST
def ladders_defer(request, link_id):
    return _schedule_move_response(defer_ladder, link_id)


@support_console_required
@require_POST
def ladders_restore(request, link_id):
    return _schedule_move_response(restore_ladder, link_id)


@support_console_required
@require_POST
def alphabetty_defer(request, link_id):
    return _schedule_move_response(defer_alphabetty, link_id)


@support_console_required
@require_POST
def alphabetty_restore(request, link_id):
    return _schedule_move_response(restore_alphabetty, link_id)


@support_console_required
@require_POST
def week_tasks_defer(request, link_id):
    return _schedule_move_response(defer_week_task, link_id)


@support_console_required
@require_POST
def week_tasks_restore(request, link_id):
    return _schedule_move_response(restore_week_task, link_id)


@support_console_required
@require_POST
def word_salad_defer(request, link_id):
    return _schedule_move_response(defer_word_salad, link_id)


@support_console_required
@require_POST
def word_salad_restore(request, link_id):
    return _schedule_move_response(restore_word_salad, link_id)


@support_console_required
@require_POST
def censorly_defer(request, link_id):
    return _schedule_move_response(defer_censorly, link_id)


@support_console_required
@require_POST
def censorly_restore(request, link_id):
    return _schedule_move_response(restore_censorly, link_id)


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
    ctx.update(word_salad_dashboard_offers_context())
    offer_map = word_salad_offers_by_link_ids([r.link_id for r in ctx.get('rows') or []])
    ctx['offer_by_link_json'] = {
        str(k): v.to_dict() for k, v in offer_map.items()
    }
    return render(request, 'support/word_salad.html', ctx)


def _word_salad_error_response(exc: WordSaladSupportError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


def _word_salad_body(request):
    if request.content_type == 'application/json':
        return _json_object_body(request)
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
def word_salad_set_publish_start(request):
    body = _word_salad_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    date_iso = body.get('publish_start') or body.get('date')
    if not date_iso:
        return JsonResponse({'ok': False, 'error': 'Нужна publish_start (YYYY-MM-DD)'}, status=400)
    try:
        new_date = word_salad_set_publish_start_service(str(date_iso))
        rows = list_word_salad_rows()
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc)
    return JsonResponse({
        'ok': True,
        'publish_start': new_date,
        'rows': [row.to_dict() for row in rows],
    })


@support_console_required
@require_POST
def word_salad_save(request, link_id):
    body = _word_salad_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    intro = str(body.get('intro') or '').strip()
    grid_text = body.get('grid_text') or ''
    words_text = body.get('words_text') or ''
    rare_words_text = body.get('rare_words_text') or ''
    author = str(body.get('author') or '')
    try:
        detail = update_word_salad(
            link_id,
            intro=intro,
            grid_text=grid_text,
            words_text=words_text,
            rare_words_text=rare_words_text,
            author=author,
        )
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
def word_salad_recheck(request, link_id):
    try:
        stats = recheck_word_salad(link_id)
    except WordSaladSupportError as exc:
        return _word_salad_error_response(exc)
    except Exception as exc:
        logger.exception('Word Salad recheck failed link_id=%s', link_id)
        return _word_salad_error_response(exc, status=500)
    return JsonResponse({'ok': True, 'recheck': stats})


@support_console_required
@require_GET
def word_salad_recheck_status(request, job_id):
    job = WordSaladRecheckJob.objects.filter(pk=job_id).first()
    if job is None:
        return JsonResponse({'ok': False, 'error': 'Очередь не найдена'}, status=404)
    return JsonResponse({'ok': True, 'recheck': serialize_job(job)})


@support_console_required
@require_POST
def word_salad_recheck_retry(request, job_id):
    job = WordSaladRecheckJob.objects.filter(pk=job_id).first()
    if job is None:
        return JsonResponse({'ok': False, 'error': 'Очередь не найдена'}, status=404)
    job = retry_word_salad_recheck(job.pk)
    return JsonResponse({'ok': True, 'recheck': serialize_job(job)})


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


def _word_salad_offer_error_response(exc: WordSaladOfferError, status=400):
    return JsonResponse({'ok': False, 'error': str(exc)}, status=status)


@support_console_required
@require_GET
def word_salad_offers_list_json(request):
    rows = list_sent_word_salad_offers()
    return JsonResponse({'ok': True, 'offers': [r.to_dict() for r in rows]})


@support_console_required
@require_GET
def word_salad_offer_detail_json(request, offer_id):
    offer = (
        WordSaladOffer.objects.select_related('task_group', 'accepted_link', 'user', 'user__profile')
        .filter(pk=offer_id)
        .first()
    )
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    return JsonResponse({'ok': True, 'offer': serialize_word_salad_offer(offer).to_dict()})


@support_console_required
@require_POST
def word_salad_offer_save(request, offer_id):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    offer = WordSaladOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = update_word_salad_offer_content(
            offer,
            theme=str(body.get('theme') or ''),
            idea_text=str(body.get('idea_text') or ''),
            suggested_words=str(body.get('suggested_words') or ''),
            grid_text=str(body.get('grid_text') or ''),
            words_text=str(body.get('words_text') or ''),
            rare_words_text=str(body.get('rare_words_text') or ''),
            comment=str(body.get('comment') or ''),
            author=str(body.get('author') or ''),
            allow_non_draft=True,
        )
    except WordSaladOfferError as exc:
        return _word_salad_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_word_salad_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_word_salad_offers()],
    })


@support_console_required
@require_POST
def word_salad_offer_accept(request, offer_id):
    offer = WordSaladOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = accept_word_salad_offer(offer)
        rows = list_word_salad_rows()
    except WordSaladOfferError as exc:
        return _word_salad_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_word_salad_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_word_salad_offers()],
        'rows': [r.to_dict() for r in rows],
    })


@support_console_required
@require_POST
def word_salad_offer_request_revision(request, offer_id):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    offer = WordSaladOffer.objects.filter(pk=offer_id).first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    try:
        offer = request_word_salad_revision(offer, admin_note=str(body.get('admin_note') or ''))
    except WordSaladOfferError as exc:
        return _word_salad_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_word_salad_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_word_salad_offers()],
    })


@support_console_required
@require_POST
def word_salad_offer_reset_progress(request, offer_id):
    offer = WordSaladOffer.objects.filter(pk=offer_id).select_related('task_group').first()
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Не найдено'}, status=404)
    if not offer.task_group_id:
        return JsonResponse({'ok': False, 'error': 'У идеи нет задания'}, status=400)
    task = Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()
    if task is None:
        return JsonResponse({'ok': False, 'error': 'Нет задания'}, status=404)
    try:
        stats = reset_all_salad_progress(task=task)
    except WordSaladOfferError as exc:
        return _word_salad_offer_error_response(exc)
    return JsonResponse({'ok': True, 'reset': stats})


@support_console_required
@require_POST
def word_salad_link_request_revision(request, link_id):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    offer = word_salad_offer_for_link(int(link_id))
    if offer is None:
        return JsonResponse({'ok': False, 'error': 'Нет автора в системе для этого салатика'}, status=404)
    if offer.status == WordSaladOffer.STATUS_DRAFT:
        return JsonResponse({'ok': False, 'error': 'Уже на доработке'}, status=400)
    try:
        offer = request_word_salad_revision(offer, admin_note=str(body.get('admin_note') or ''))
    except WordSaladOfferError as exc:
        return _word_salad_offer_error_response(exc)
    return JsonResponse({
        'ok': True,
        'offer': serialize_word_salad_offer(offer).to_dict(),
        'offers': [r.to_dict() for r in list_sent_word_salad_offers()],
    })


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
def queues_dashboard(request):
    return render(request, 'support/queues.html', queues_dashboard_context())


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
    body = _json_object_body(request)
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
        mixed_script = json_boolean_field(body, 'mixed_script')
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        offer = update_offer_content(
            offer,
            words=words,
            hints=hints,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or ''),
            comment=str(body.get('comment') or ''),
            mixed_script=mixed_script,
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
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
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
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
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
    body = _json_object_body(request)
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
    emojis = body.get('emojis')
    if emojis is not None and not isinstance(emojis, list):
        return JsonResponse({'ok': False, 'error': 'emojis должен быть списком'}, status=400)
    try:
        mixed_script = json_boolean_field(body, 'mixed_script')
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        detail = create_ladder(
            at_number=at_number,
            words=words,
            hints=hints,
            emojis=emojis,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or ''),
            mixed_script=mixed_script,
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
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    words = body.get('words')
    hints = body.get('hints')
    if not isinstance(words, list) or not isinstance(hints, list):
        return JsonResponse({'ok': False, 'error': 'Нужны words и hints (списки)'}, status=400)
    emojis = body.get('emojis')
    if emojis is not None and not isinstance(emojis, list):
        return JsonResponse({'ok': False, 'error': 'emojis должен быть списком'}, status=400)
    try:
        mixed_script = json_boolean_field(body, 'mixed_script')
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        detail = update_ladder(
            int(link_id),
            words=words,
            hints=hints,
            emojis=emojis,
            intro=str(body.get('intro') or ''),
            author=str(body.get('author') or ''),
            mixed_script=mixed_script,
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
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
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
def censorly_dashboard(request):
    ctx = censorly_dashboard_context()
    return render(request, 'support/censorly.html', ctx)


@support_console_required
def censorly_pool(request):
    return render(request, 'support/censorly_pool.html', {'taxonomy': censorly_pool_taxonomy()})


@support_console_required
@require_GET
def censorly_pool_list(request):
    rows = pool_rows()
    parent = request.GET.get('category', '')
    child = request.GET.get('subcategory', '')
    difficulty = request.GET.get('difficulty', '')
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except (TypeError, ValueError):
        offset = 0
    if parent or child:
        wanted = parent + ('/' + child if child else '')
        rows = {title: row for title, row in rows.items() if any(
            path == wanted or (not child and (path == parent or path.startswith(parent + '/'))) for path in row['topics']
        )}
    if difficulty:
        rows = {title: row for title, row in rows.items() if row['difficulty'] == difficulty}
    if not parent and not child:
        return JsonResponse({'ok': True, 'groups': {}, 'total': 0, 'next_offset': None, 'select_category': True})
    # Avoid rendering thousands of article rows at once. Category lists can be
    # large, so the support UI requests another page when needed.
    ordered = sorted(rows.values(), key=lambda row: row['title'].casefold())
    total = len(ordered)
    page = ordered[offset:offset + 120]
    grouped = {level: [] for level in ('легко', 'средне', 'сложно', 'очень сложно')}
    for row in page:
        grouped.setdefault(row['difficulty'] or 'без сложности', []).append(row)
    next_offset = offset + len(page) if offset + len(page) < total else None
    return JsonResponse({'ok': True, 'groups': grouped, 'total': total, 'next_offset': next_offset})


@support_console_required
@require_POST
def censorly_pool_save(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    title = str(body.get('title') or '').strip()
    replacement = str(body.get('replacement_title') or title).strip()
    rows = pool_rows()
    old = rows.get(title)
    if title and old is None:
        return JsonResponse({'ok': False, 'error': 'Статья не найдена'}, status=404)
    if old is None and not replacement:
        return JsonResponse({'ok': False, 'error': 'Укажите статью'}, status=400)
    topics = body.get('topics', old['topics'] if old else [])
    types = body.get('types', old['types'] if old else [])
    difficulty = body.get('difficulty', old['difficulty'] if old else 'средне')
    enabled = body.get('enabled', old['enabled'] if old else True)
    if not isinstance(enabled, bool):
        return JsonResponse({'ok': False, 'error': 'Некорректный статус статьи'}, status=400)
    if old is None or replacement != title:
        try:
            article = fetch_article(replacement)
        except WikiFetchError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        payload = build_puzzle_payload(wiki_title=article.title, body_text=article.extract)
        if not title_content_lemmas(payload):
            return JsonResponse({'ok': False, 'error': 'В названии статьи нет угадываемых слов'}, status=400)
        replacement = article.title
    if replacement != title and replacement in rows:
        return JsonResponse(
            {'ok': False, 'error': 'Статья с таким названием уже есть в пуле'},
            status=409,
        )
    try:
        with transaction.atomic():
            save_pool_entry(replacement, topics, types, difficulty, enabled)
            if title and title != replacement:
                CensorlyPoolEntry.objects.update_or_create(title=title, defaults={
                    'topics': old['topics'], 'object_types': old['types'], 'difficulty': old['difficulty'], 'enabled': False,
                })
    except (TypeError, ValueError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except IntegrityError:
        return JsonResponse({'ok': False, 'error': 'Статья с таким названием уже есть в пуле'}, status=409)
    return JsonResponse({'ok': True})


@support_console_required
@require_POST
def censorly_pool_difficulty_save(request):
    body = _json_object_body(request)
    updates = body.get('updates') if body is not None else None
    if not isinstance(updates, list) or not updates or len(updates) > 120:
        return JsonResponse({'ok': False, 'error': 'Некорректный список изменений'}, status=400)

    rows = pool_rows()
    normalized = {}
    for item in updates:
        if not isinstance(item, dict):
            return JsonResponse({'ok': False, 'error': 'Некорректное изменение сложности'}, status=400)
        title = item.get('title')
        difficulty = item.get('difficulty')
        if not isinstance(title, str) or title not in rows:
            return JsonResponse({'ok': False, 'error': 'Статья не найдена в пуле'}, status=404)
        if difficulty not in CENSORLY_POOL_DIFFICULTIES:
            return JsonResponse({'ok': False, 'error': 'Некорректная сложность'}, status=400)
        normalized[title] = difficulty

    try:
        with transaction.atomic():
            for title, difficulty in normalized.items():
                row = rows[title]
                entry, _ = CensorlyPoolEntry.objects.get_or_create(
                    title=title,
                    defaults={
                        'topics': row['topics'],
                        'object_types': row['types'],
                        'difficulty': row['difficulty'],
                        'enabled': row['enabled'],
                    },
                )
                entry.difficulty = difficulty
                entry.save(update_fields=('difficulty', 'updated_at'))
    except IntegrityError:
        return JsonResponse({'ok': False, 'error': 'Не удалось сохранить сложность'}, status=409)
    return JsonResponse({'ok': True, 'saved': len(normalized)})


@support_console_required
@require_POST
def censorly_pool_taxonomy_save(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    kind = body.get('kind')
    action = body.get('action')
    name = str(body.get('name') or '').strip()
    try:
        if kind == 'category':
            if action == 'create' and body.get('id'):
                raise ValueError('Для создания категории не передавайте id')
            node = CensorlyCategory.objects.filter(pk=body.get('id')).first() if body.get('id') else None
            if action not in ('create', 'save', 'delete'):
                raise ValueError('Неизвестное действие')
            if action == 'delete':
                if node is None:
                    raise ValueError('Категория не найдена')
                if node.children.exists() or node.pool_entries.exists():
                    raise ValueError('Сначала перенесите подкатегории и снимите тег со статей')
                node.delete()
            else:
                if not name or len(name) > 120 or '/' in name:
                    raise ValueError('Название должно быть длиной до 120 символов и без символа /')
                sort_order = int(body.get('sort_order') or 0)
                if sort_order < 0:
                    raise ValueError('Порядок не может быть отрицательным')
                is_active = body.get('is_active', node.is_active if node else True)
                if not isinstance(is_active, bool):
                    raise ValueError('Некорректный статус категории')
                parent_id = body.get('parent_id') or None
                parent = CensorlyCategory.objects.filter(pk=parent_id).first() if parent_id else None
                if parent_id and parent is None:
                    raise ValueError('Родительская категория не найдена')
                if parent and not parent.is_active and (node is None or node.parent_id != parent.pk):
                    raise ValueError('Нельзя переместить категорию к выключенному родителю')
                if is_active and parent and not parent.is_active:
                    raise ValueError('Сначала включите родительскую категорию')
                if node and parent and (parent.pk == node.pk or node.pk in parent.get_ancestors_ids()):
                    raise ValueError('Нельзя переместить категорию внутрь самой себя')
                duplicate = CensorlyCategory.objects.filter(parent=parent, name__iexact=name)
                if node:
                    duplicate = duplicate.exclude(pk=node.pk)
                if duplicate.exists():
                    raise ValueError('Такое название уже есть на этом уровне')
                if node is None and action != 'create':
                    raise ValueError('Категория не найдена')
                node = node or CensorlyCategory()
                node.name, node.parent = name, parent
                node.sort_order = sort_order
                node.is_active = is_active
                node.save()
        elif kind == 'type':
            if action == 'create' and body.get('id'):
                raise ValueError('Для создания типа не передавайте id')
            item = CensorlyObjectType.objects.filter(pk=body.get('id')).first() if body.get('id') else None
            if action == 'delete':
                if item is None:
                    raise ValueError('Тип не найден')
                if item.pool_entries.exists():
                    raise ValueError('Сначала снимите этот тип со статей')
                item.delete()
            elif action in ('create', 'save'):
                if not name or len(name) > 120:
                    raise ValueError('Название должно быть длиной до 120 символов')
                sort_order = int(body.get('sort_order') or 0)
                if sort_order < 0:
                    raise ValueError('Порядок не может быть отрицательным')
                is_active = body.get('is_active', item.is_active if item else True)
                if not isinstance(is_active, bool):
                    raise ValueError('Некорректный статус типа')
                if item is None and action != 'create':
                    raise ValueError('Тип не найден')
                if CensorlyObjectType.objects.filter(name__iexact=name).exclude(pk=item.pk if item else None).exists():
                    raise ValueError('Такой тип уже существует')
                item = item or CensorlyObjectType()
                item.name, item.sort_order = name, sort_order
                item.is_active = is_active
                item.save()
            else:
                raise ValueError('Неизвестное действие')
        else:
            raise ValueError('Неизвестный вид тега')
    except (ValueError, TypeError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except IntegrityError:
        return JsonResponse({'ok': False, 'error': 'Такое название уже существует'}, status=409)
    return JsonResponse({'ok': True, 'taxonomy': censorly_pool_taxonomy()})


@support_console_required
@require_POST
@transaction.atomic
def censorly_schedule_delete(request, link_id):
    link = GameTaskGroup.objects.select_for_update().filter(pk=link_id, game_id='censorly').select_related('game').first()
    if link is None:
        return JsonResponse({'ok': False, 'error': 'Слот не найден'}, status=404)
    from games.censorly_daily import is_censorly_number_published
    from games.support.services.censorly import _renumber_censorly_links, list_schedule_rows
    try:
        number = int(link.number)
    except ValueError:
        number = 0
    if is_censorly_number_published(link.game, number):
        return JsonResponse({'ok': False, 'error': 'Опубликованный выпуск удалить нельзя'}, status=400)
    link.delete()  # Сохраняем статью и задачу; удаляется только место в расписании.
    rows = list(GameTaskGroup.objects.filter(game_id='censorly', is_deferred=False).select_related('task_group'))
    rows.sort(key=lambda item: int(item.number) if str(item.number).isdigit() else 10**9)
    _renumber_censorly_links(rows)
    return JsonResponse({'ok': True, 'rows': [r.to_dict() for r in list_schedule_rows()]})


@support_console_required
@require_POST
def censorly_schedule_edit(request, link_id):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    link = GameTaskGroup.objects.filter(pk=link_id, game_id='censorly').select_related('game').first()
    if link is None:
        return JsonResponse({'ok': False, 'error': 'Слот не найден'}, status=404)
    from games.censorly_daily import is_censorly_number_published
    from games.support.services.censorly import list_schedule_rows
    try:
        article = fetch_article(str(body.get('title') or '').strip())
    except WikiFetchError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    payload = build_puzzle_payload(
        wiki_title=article.title,
        body_text=article.extract,
        wiki_pageid=article.pageid,
        wiki_revid=article.revid,
        fetched_at=timezone.now().isoformat(),
        truncated=bool(getattr(article, 'truncated', False)),
    )
    if not title_content_lemmas(payload):
        return JsonResponse({'ok': False, 'error': 'В названии статьи нет угадываемых слов'}, status=400)
    with transaction.atomic():
        link = GameTaskGroup.objects.select_for_update().filter(pk=link_id, game_id='censorly').select_related('game').first()
        if link is None:
            return JsonResponse({'ok': False, 'error': 'Слот не найден'}, status=404)
        try:
            number = int(link.number)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'Некорректный номер слота'}, status=400)
        if is_censorly_number_published(link.game, number):
            return JsonResponse({'ok': False, 'error': 'Опубликованный выпуск редактировать нельзя'}, status=400)
        task = Task.objects.select_for_update().filter(task_group_id=link.task_group_id, number='1').first()
        if task is None:
            return JsonResponse({'ok': False, 'error': 'В выпуске нет задачи'}, status=400)
        tags = dict(task.tags) if isinstance(task.tags, dict) else {}
        tags[CENSORLY_TAGS_KEY] = payload
        task.answer = article.title
        task.tags = tags
        task.save(update_fields=['answer', 'tags'])
        # Progress for the previous answer cannot be carried over to a new puzzle.
        ChainTaskState.objects.filter(task=task, game=link.game).delete()
        Attempt.manager.filter(task=task, game=link.game).delete()
        link.name = ('Цензурка: ' + article.title)[:100]
        link.save(update_fields=['name'])
    return JsonResponse({'ok': True, 'rows': [r.to_dict() for r in list_schedule_rows()]})


@support_console_required
@require_GET
def censorly_random_list(request):
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in censorly_list_random_rows()]})


@support_console_required
@require_POST
def censorly_reorder(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        rows = reorder_censorly([int(value) for value in body.get('order', [])])
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен корректный порядок'}, status=400)
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'rows': [row.to_dict() for row in rows]})


@support_console_required
@require_GET
def censorly_offers_list(request):
    return JsonResponse({'ok': True, 'rows': list_censorly_offers()})


@support_console_required
@require_POST
def censorly_offer_accept(request, offer_id):
    try:
        offer = accept_censorly_offer(offer_id)
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'number': offer.accepted_link.number})


@support_console_required
@require_POST
def censorly_offer_reject(request, offer_id):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'JSON должен быть объектом'}, status=400)
    try:
        reject_censorly_offer(offer_id, note=str(body.get('note') or ''))
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True})


@support_console_required
@require_POST
def censorly_generate_random(request):
    try:
        row = censorly_create_random()
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        logging.getLogger('games.support.views').exception('censorly generate random failed')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, 'row': row.to_dict()})


@support_console_required
@require_POST
def censorly_generate_from_title(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    title = str(body.get('title') or body.get('url') or '').strip()
    if not title:
        return JsonResponse({'ok': False, 'error': 'Укажите заголовок или URL'}, status=400)
    try:
        row = censorly_create_from_title(title)
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        logging.getLogger('games.support.views').exception('censorly generate from title failed')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, 'row': row.to_dict()})


@support_console_required
@require_POST
def censorly_reset_progress(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    share_hash = str(body.get('share_hash') or '').strip()
    number = str(body.get('number') or '').strip()
    try:
        deleted = censorly_reset_my_progress(
            user=request.user,
            share_hash=share_hash,
            number=number,
        )
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'deleted': deleted})


@support_console_required
@require_POST
def censorly_refetch_text(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    share_hash = str(body.get('share_hash') or '').strip()
    number = str(body.get('number') or '').strip()
    if not share_hash and not number:
        return JsonResponse({'ok': False, 'error': 'Нужна партия'}, status=400)
    try:
        detail = censorly_refetch_article_text(share_hash=share_hash, number=number)
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        logging.getLogger('games.support.views').exception('censorly refetch text failed')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, **detail})


@support_console_required
@require_POST
def censorly_set_publish_start(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    date_iso = body.get('publish_start') or body.get('date')
    if not date_iso:
        return JsonResponse({'ok': False, 'error': 'Нужна publish_start (YYYY-MM-DD)'}, status=400)
    try:
        new_date = censorly_set_publish_start_service(str(date_iso))
        ctx = censorly_dashboard_context()
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({
        'ok': True,
        'publish_start': new_date,
        'rows': ctx['schedule_json'],
    })


@support_console_required
@require_POST
def censorly_schedule_create(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        at_number = int(body.get('at_number'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен at_number'}, status=400)
    title = str(body.get('title') or body.get('url') or '').strip() or None
    try:
        detail = censorly_create_at_number(at_number, title_or_url=title)
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        logging.getLogger('games.support.views').exception('censorly schedule create failed')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, **detail})


@support_console_required
@require_POST
def censorly_schedule_generate(request):
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        n = int(body.get('n') or 5)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Нужен n'}, status=400)
    try:
        result = censorly_generate_more(n)
    except CensorlySupportError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        logging.getLogger('games.support.views').exception('censorly schedule generate failed')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
    return JsonResponse({'ok': True, **result})


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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
    body = _json_object_body(request)
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
        body = _json_object_body(request)
        if body is None:
            return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
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
    body = _json_object_body(request)
    if body is None:
        return JsonResponse({'ok': False, 'error': 'Некорректный JSON'}, status=400)
    try:
        force = json_boolean_field(body, 'force')
        immediate = json_boolean_field(body, 'immediate', default=True)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    try:
        post = social_get_post(post_id)
        post = social_publish_network(
            post,
            str(body.get('network') or ''),
            force=force,
            immediate=immediate,
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
def social_regenerate_image(request, post_id):
    try:
        post = social_get_post(post_id)
        post = social_regenerate_post_image(post)
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
