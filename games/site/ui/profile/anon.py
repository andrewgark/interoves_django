"""Main UI: hub, games folder, profile, team."""
from datetime import timedelta
import hmac
import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST


from django.db import transaction
from django.utils import timezone


from games.site.http.auth_observability import log_auth_event, request_session_fingerprint
from games.site.session.gameplay_context import anonymous_actor_fingerprint

from games.ops.analytics.identity import (
    browser_anon_key,
    is_valid_anon_key,
    rotate_anonymous_identity,
)
from games.models import (
    Attempt,
    ChainTaskState,
    GameTaskGroup,
    HintAttempt,
)
from games.site.views.pages.util import (
    has_profile,
    personal_play_mode_locked,
)


from games.site.ui import (
    NEW_UI_PROJECT,
    _session_play_mode_key,
)
from games.site.ui.play.reactions import _game_from_next_path

logger = logging.getLogger('games.views.new_ui')

@require_http_methods(['GET'])
def new_set_play_mode(request):
    mode = request.GET.get('mode')
    next_url = request.GET.get('next') or '/'
    project_id = request.GET.get('project') or NEW_UI_PROJECT
    if mode == 'personal':
        g = _game_from_next_path(next_url)
        if g is not None and personal_play_mode_locked(g, user=request.user):
            mode = 'team'
    if mode in ('team', 'personal'):
        request.session[_session_play_mode_key(project_id)] = mode
    return redirect(next_url)


def _valid_anon_key(value):
    return is_valid_anon_key(value)


def _anon_key_matches_browser(request, anon_key):
    """Claim proof is the canonical browser cookie, not a posted/header UUID."""
    cookie_key = (browser_anon_key(request) or '').strip()
    presented = (anon_key or '').strip()
    if not cookie_key or not presented:
        return False
    return hmac.compare_digest(cookie_key, presented)


def _anon_task_group_link(attempt):
    """Ссылка (url, label) на круг с этим заданием, либо None, если её не построить.

    У нас нет отдельных страниц заданий — ведём на страницу круга (task group).
    """
    task = attempt.task
    if task is None or task.task_group_id is None:
        return None
    game = attempt.game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        return None
    gtg = GameTaskGroup.objects.filter(
        game=game, task_group_id=task.task_group_id
    ).first()
    if gtg is None:
        return None
    try:
        url = reverse('new_task_group', kwargs={
            'game_id': game.id,
            'task_group_number': gtg.number,
        })
    except Exception:
        return None
    label = (gtg.name or '').strip() or (game.name or '').strip()
    return url, label


@login_required
@require_http_methods(['GET'])
def new_anon_migrate_count(request):
    if not has_profile(request.user):
        raise Http404()
    canonical = (browser_anon_key(request) or '').strip()
    posted = (request.GET.get('anon_key') or '').strip()
    if not canonical:
        return JsonResponse({'status': 'anon_key_mismatch', 'show_prompt': False}, status=403)
    if posted:
        if not is_valid_anon_key(posted):
            return JsonResponse({'status': 'invalid_anon_key', 'show_prompt': False}, status=400)
        if not _anon_key_matches_browser(request, posted):
            return JsonResponse({'status': 'anon_key_mismatch', 'show_prompt': False}, status=403)
        anon_key = posted
    else:
        anon_key = canonical

    from games.club.merge.anon import anon_migration_counts
    from games.models import AnonAccountClaim, HiddenAnonKey

    if HiddenAnonKey.objects.filter(anon_key=anon_key).exists():
        return JsonResponse({'status': 'hidden_anon', 'show_prompt': False}, status=409)
    claim = AnonAccountClaim.objects.filter(anon_key=anon_key).first()
    if claim is not None and claim.user_id != request.user.pk:
        return JsonResponse({'status': 'claimed_elsewhere', 'show_prompt': False}, status=409)

    # Задания, уже сданные на OK на авторизованном профиле (личный режим),
    # не учитываем — их посылки восстанавливать незачем.
    solved_task_ids = set(
        Attempt.manager.filter(
            user=request.user, team__isnull=True, anon_key__isnull=True, status='Ok',
        ).values_list('task_id', flat=True)
    )

    anon_attempts = (
        Attempt.manager.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True, task__isnull=False,
        )
        .exclude(task_id__in=solved_task_ids)
    )

    # Count in SQL: materializing the history with full related tasks repeats
    # large puzzle JSON for every attempt and can exhaust a web host's RAM.
    n = anon_attempts.count()
    example = None
    examples = (
        anon_attempts.select_related('task', 'game')
        .only('task__task_group_id', 'game__name')
        .order_by('time')
    )
    for attempt in examples.iterator(chunk_size=100):
        example = _anon_task_group_link(attempt)
        if example is not None:
            break

    counts = anon_migration_counts(anon_key)
    counts['unsolved_attempts'] = n
    counts['transferable_hints'] = HintAttempt.objects.filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True,
    ).exclude(hint__task_id__in=solved_task_ids).count()
    counts['transferable_states'] = ChainTaskState.objects.filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True,
    ).exclude(task_id__in=solved_task_ids).count()
    total_items = (
        n
        + sum(
            value for key, value in counts.items()
            if key not in (
                'attempts', 'unsolved_attempts', 'hints', 'states',
                'analytics_states', 'transferable_hints', 'transferable_states',
            )
        )
        + counts['transferable_hints']
        + counts['transferable_states']
    )
    show_prompt = total_items > 0
    payload = {
        'status': 'ok',
        # Backward-compatible meaning: attempts that can add progress to this
        # user. The full raw count is available as counts.attempts.
        'attempts': n,
        'counts': counts,
        'total_items': total_items,
        'show_prompt': show_prompt,
    }
    if example is not None:
        payload['example_url'] = example[0]
        payload['example_label'] = example[1]
    return JsonResponse(payload)


@login_required
@require_POST
def new_club_archive_offer_action(request):
    if not has_profile(request.user):
        raise Http404()
    action = (request.POST.get('action') or '').strip()
    profile = request.user.profile
    if action == 'shown':
        if not profile.club_archive_offer_never:
            profile.club_archive_offer_last_shown_at = timezone.now()
            profile.save(update_fields=['club_archive_offer_last_shown_at'])
        return JsonResponse({'status': 'ok'})
    if action == 'defer':
        if not profile.club_archive_offer_never:
            profile.club_archive_offer_deferred_until = timezone.now() + timedelta(days=14)
            profile.save(update_fields=['club_archive_offer_deferred_until'])
        return JsonResponse({'status': 'ok'})
    if action == 'never':
        profile.club_archive_offer_never = True
        profile.club_archive_offer_deferred_until = None
        profile.save(update_fields=['club_archive_offer_never', 'club_archive_offer_deferred_until'])
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'invalid_action'}, status=400)


@login_required
@require_http_methods(['POST'])
@transaction.atomic
def new_migrate_anon_attempts(request):
    if not has_profile(request.user):
        raise Http404()
    # The cookie is the authority.  The posted value is retained only as a
    # compatibility check for old clients and is never used to select a source.
    anon_key = (browser_anon_key(request) or '').strip()
    posted_anon_key = (request.POST.get('anon_key') or '').strip()
    if not anon_key:
        raise Http404()
    if posted_anon_key and not is_valid_anon_key(posted_anon_key):
        return JsonResponse({'status': 'invalid_anon_key'}, status=400)
    if posted_anon_key and not _anon_key_matches_browser(request, posted_anon_key):
        return JsonResponse({'status': 'anon_key_mismatch'}, status=403)
    from games.club.merge.anonymous import enqueue_anonymous_merge, serialize_merge_job

    job, status = enqueue_anonymous_merge(request.user, anon_key)
    if status == 'hidden_anon':
        return JsonResponse({'status': status}, status=409)
    if status == 'claimed_elsewhere':
        return JsonResponse({'status': status}, status=409)
    if status == 'empty':
        return JsonResponse({'status': 'empty', 'show_prompt': False})
    log_auth_event(
        'auth_account_claim',
        request,
        user_id=str(request.user.pk),
        session_fingerprint=request_session_fingerprint(request),
        anon_fingerprint=anonymous_actor_fingerprint(anon_key),
        success=True,
        claim_status='queued',
        merge_job_id=str(job.id),
    )
    # Stop accepting new anonymous gameplay under the claimed identity.  The
    # job retains the source key server-side and the status endpoint is user-scoped.
    rotate_anonymous_identity(request)
    return JsonResponse({'status': 'ok', 'queued': True, 'job': serialize_merge_job(job)}, status=202)


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def new_anon_merge_job_status(request, job_id=None):
    from games.club.merge.anonymous import active_merge_job_for_user, serialize_merge_job
    from games.models import AnonymousMergeJob

    if job_id:
        job = AnonymousMergeJob.objects.filter(pk=job_id, user=request.user).first()
    else:
        job = active_merge_job_for_user(request.user)
    if job is None:
        # The base template probes this endpoint on page loads to resume a
        # merge after navigation/reload.  No active job is an expected state,
        # not a missing resource/error for the browser to report.
        if job_id is None:
            return HttpResponse(status=204)
        return JsonResponse({'status': 'not_found'}, status=404)
    if request.method == 'POST':
        from games.club.merge.anonymous import retry_merge_job
        job = retry_merge_job(request.user, job.id)
        if job is None:
            return JsonResponse({'status': 'not_retryable'}, status=409)
    return JsonResponse({'status': 'ok', 'job': serialize_merge_job(job)})



__all__ = [
    'new_set_play_mode',
    '_valid_anon_key',
    '_anon_key_matches_browser',
    '_anon_task_group_link',
    'new_anon_migrate_count',
    'new_club_archive_offer_action',
    'new_migrate_anon_attempts',
    'new_anon_merge_job_status',
]
