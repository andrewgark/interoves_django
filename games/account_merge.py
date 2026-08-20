"""Safe, explicit merging of two authenticated Interoves users."""

import json
import secrets
import time

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from games.models import (
    AccountMerge,
    AnonAccountClaim,
    AlphabettyDictSuggestion,
    AlphabettyOffer,
    AlphabettyPersonalDictWord,
    Attempt,
    BugReport,
    ChainTaskState,
    Donation,
    HintAttempt,
    LadderOffer,
    Like,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    ProfileTeamMembership,
    StatisticsEvent,
    TicketRequest,
)


PENDING_ACCOUNT_MERGE_SESSION_KEY = 'interoves_pending_account_merge'
PENDING_ACCOUNT_MERGE_TTL_SECONDS = 10 * 60


class AccountMergeError(Exception):
    pass


def _safe_next_url(request, value):
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return reverse('ui_profile')


def stash_pending_account_merge(request, sociallogin):
    """Remember a provider-authenticated source account for confirmation."""
    request.session[PENDING_ACCOUNT_MERGE_SESSION_KEY] = {
        'target_user_id': request.user.pk,
        'source_user_id': sociallogin.user.pk,
        'provider': sociallogin.account.provider,
        'provider_uid': str(sociallogin.account.uid),
        'created_at': int(time.time()),
        'nonce': secrets.token_urlsafe(24),
        'next': _safe_next_url(
            request,
            (getattr(sociallogin, 'state', None) or {}).get('next'),
        ),
    }
    request.session.modified = True


def clear_pending_account_merge(request):
    request.session.pop(PENDING_ACCOUNT_MERGE_SESSION_KEY, None)
    request.session.modified = True


def get_pending_account_merge(request):
    pending = request.session.get(PENDING_ACCOUNT_MERGE_SESSION_KEY) or {}
    required = {
        'target_user_id', 'source_user_id', 'provider', 'provider_uid',
        'created_at', 'nonce', 'next',
    }
    if not required.issubset(pending):
        clear_pending_account_merge(request)
        return None
    if pending['target_user_id'] != request.user.pk:
        clear_pending_account_merge(request)
        return None
    try:
        age = int(time.time()) - int(pending['created_at'])
    except (TypeError, ValueError):
        age = PENDING_ACCOUNT_MERGE_TTL_SECONDS + 1
    if age < 0 or age > PENDING_ACCOUNT_MERGE_TTL_SECONDS:
        clear_pending_account_merge(request)
        return None
    if not SocialAccount.objects.filter(
        user_id=pending['source_user_id'],
        provider=pending['provider'],
        uid=pending['provider_uid'],
    ).exists():
        clear_pending_account_merge(request)
        return None
    return pending


def _display_name(user):
    try:
        profile = user.profile
    except Profile.DoesNotExist:
        profile = None
    if profile is not None:
        value = '{} {}'.format(profile.first_name or '', profile.last_name or '').strip()
        if value:
            return value
    return (user.get_full_name() or user.email or user.get_username()).strip()


def _privileged(user):
    if bool(
        user.is_staff
        or user.is_superuser
        or user.groups.exists()
        or user.user_permissions.exists()
    ):
        return True
    # Historical admin/SQL-explorer ownership should never be silently
    # reassigned by a self-service consumer account merge.
    for relation in ('logentry', 'promptlog', 'query', 'querylog', 'favorites'):
        manager = getattr(user, relation, None)
        if manager is not None and manager.exists():
            return True
    return False


def _provider_conflicts(target_user, source_user):
    target = {}
    for account in SocialAccount.objects.filter(user=target_user):
        target.setdefault(account.provider, set()).add(str(account.uid))
    source = {}
    for account in SocialAccount.objects.filter(user=source_user):
        source.setdefault(account.provider, set()).add(str(account.uid))
    conflicts = []
    for provider, uids in target.items():
        if len(uids) > 1:
            conflicts.append('multiple_provider:{}'.format(provider))
    for provider, uids in source.items():
        if len(uids) > 1:
            conflicts.append('multiple_provider:{}'.format(provider))
        target_uids = target.get(provider) or set()
        if target_uids and target_uids != uids:
            conflicts.append('provider:{}'.format(provider))
    return sorted(set(conflicts))


def _conflict_message(code):
    if code == 'same_user':
        return 'Это уже один и тот же профиль.'
    if code == 'inactive_user':
        return 'Один из профилей уже был отключён.'
    if code == 'privileged_user':
        return 'В одном из профилей есть административные права или служебная история.'
    if code == 'team_request_conflict':
        return 'В профилях есть разные незавершённые заявки на вступление в команды.'
    if code == 'telegram_identity_conflict':
        return 'К профилям привязаны разные подтверждённые Telegram-аккаунты для оплаты.'
    if code.startswith('provider:'):
        provider = {'google': 'Google', 'vk': 'VK', 'telegram': 'Telegram'}.get(
            code.partition(':')[2], code.partition(':')[2],
        )
        return 'К обоим профилям подключены разные аккаунты {}.'.format(provider)
    if code.startswith('multiple_provider:'):
        provider = {'google': 'Google', 'vk': 'VK', 'telegram': 'Telegram'}.get(
            code.partition(':')[2], code.partition(':')[2],
        )
        return 'В одном профиле уже несколько аккаунтов {} — нужна ручная проверка.'.format(provider)
    return 'Обнаружен конфликт данных, который требует ручной проверки.'


def build_account_merge_preview(target_user, source_user):
    conflicts = []
    if target_user.pk == source_user.pk:
        conflicts.append('same_user')
    if not target_user.is_active or not source_user.is_active:
        conflicts.append('inactive_user')
    if _privileged(target_user) or _privileged(source_user):
        conflicts.append('privileged_user')
    conflicts.extend(_provider_conflicts(target_user, source_user))

    try:
        target_profile = target_user.profile
    except Profile.DoesNotExist:
        target_profile = None
    try:
        source_profile = source_user.profile
    except Profile.DoesNotExist:
        source_profile = None
    if (
        target_profile is not None
        and source_profile is not None
        and target_profile.team_requested_id is not None
        and source_profile.team_requested_id is not None
        and target_profile.team_requested_id != source_profile.team_requested_id
    ):
        conflicts.append('team_request_conflict')
    if (
        target_profile is not None
        and source_profile is not None
        and getattr(target_profile, 'telegram_verified', False)
        and getattr(target_profile, 'telegram_user_id', None)
        and getattr(source_profile, 'telegram_verified', False)
        and getattr(source_profile, 'telegram_user_id', None)
        and target_profile.telegram_user_id != source_profile.telegram_user_id
    ):
        # A verified Telegram id is also a payment identity. Choosing either
        # one automatically would make future payment matching ambiguous.
        conflicts.append('telegram_identity_conflict')
    team_count = (
        ProfileTeamMembership.objects.filter(profile=source_profile).count()
        if source_profile is not None else 0
    )
    return {
        'source_name': _display_name(source_user),
        'source_email': source_user.email or '',
        'source_providers': sorted(
            SocialAccount.objects.filter(user=source_user)
            .values_list('provider', flat=True)
            .distinct()
        ),
        'target_providers': sorted(
            SocialAccount.objects.filter(user=target_user)
            .values_list('provider', flat=True)
            .distinct()
        ),
        'attempts': Attempt.manager.filter(user=source_user).count(),
        'hints': HintAttempt.objects.filter(user=source_user).count(),
        'teams': team_count,
        'offers': (
            LadderOffer.objects.filter(user=source_user).count()
            + AlphabettyOffer.objects.filter(user=source_user).count()
        ),
        'conflicts': conflicts,
        'conflict_messages': [_conflict_message(code) for code in conflicts],
        'can_merge': not conflicts,
    }


def _merge_profiles(target_user, source_user, summary):
    target, _ = Profile.objects.get_or_create(
        user=target_user,
        defaults={'first_name': '', 'last_name': ''},
    )
    try:
        source = source_user.profile
    except Profile.DoesNotExist:
        return

    source_team_ids = list(
        ProfileTeamMembership.objects.filter(profile=source)
        .values_list('team_id', flat=True)
    )
    if source.team_on_id and source.team_on_id not in source_team_ids:
        source_team_ids.append(source.team_on_id)
    for team_id in source_team_ids:
        ProfileTeamMembership.objects.get_or_create(profile=target, team_id=team_id)
    summary['team_memberships'] = len(source_team_ids)

    updates = []
    for field in ('first_name', 'last_name', 'avatar_url', 'vk_url', 'email', 'telegram_handle'):
        source_value = getattr(source, field)
        if source_value and not getattr(target, field):
            setattr(target, field, source_value)
            updates.append(field)

    source_has_verified_telegram = bool(
        getattr(source, 'telegram_verified', False)
        and getattr(source, 'telegram_user_id', None)
    )
    target_has_verified_telegram = bool(
        getattr(target, 'telegram_verified', False)
        and getattr(target, 'telegram_user_id', None)
    )
    if source_has_verified_telegram and not target_has_verified_telegram:
        # Release the unique id on the source row before assigning it to the
        # surviving profile.
        telegram_user_id = source.telegram_user_id
        telegram_username = source.telegram_username
        telegram_linked_at = source.telegram_linked_at
        source.telegram_user_id = None
        source.telegram_username = ''
        source.telegram_verified = False
        source.telegram_linked_at = None
        source.save(update_fields=[
            'telegram_user_id', 'telegram_username', 'telegram_verified',
            'telegram_linked_at',
        ])
        target.telegram_user_id = telegram_user_id
        target.telegram_username = telegram_username
        target.telegram_verified = True
        target.telegram_linked_at = telegram_linked_at
        updates.extend([
            'telegram_user_id', 'telegram_username', 'telegram_verified',
            'telegram_linked_at',
        ])
        summary['telegram_identity'] = 1
    if target.team_requested_id is None and source.team_requested_id is not None:
        target.team_requested_id = source.team_requested_id
        target.join_accept_as_primary = source.join_accept_as_primary
        updates.extend(['team_requested', 'join_accept_as_primary'])
    elif (
        target.team_requested_id is not None
        and source.team_requested_id is not None
        and target.team_requested_id != source.team_requested_id
    ):
        summary['discarded_source_team_request'] = source.team_requested_id
    if updates:
        target.save(update_fields=list(dict.fromkeys(updates)))

    ProfileTeamMembership.objects.filter(profile=source).delete()
    source.team_on = None
    source.team_requested = None
    source_updates = ['team_on', 'team_requested']
    if hasattr(source, 'telegram_user_id'):
        source.telegram_user_id = None
        source.telegram_username = ''
        source.telegram_verified = False
        source.telegram_linked_at = None
        source_updates.extend([
            'telegram_user_id', 'telegram_username', 'telegram_verified',
            'telegram_linked_at',
        ])
    source.save(update_fields=source_updates)


def _merge_user_fields(target, source):
    updates = []
    for field in ('first_name', 'last_name', 'email'):
        if getattr(source, field) and not getattr(target, field):
            setattr(target, field, getattr(source, field))
            updates.append(field)
    if updates:
        target.save(update_fields=updates)


def _merge_email_addresses(target, source, summary):
    target_rows = {
        row.email.lower(): row
        for row in EmailAddress.objects.select_for_update().filter(user=target)
    }
    moved = 0
    for row in EmailAddress.objects.select_for_update().filter(user=source).order_by('pk'):
        existing = target_rows.get(row.email.lower())
        if existing is not None:
            source_verified = row.verified
            # Delete the source row before promoting the target row. The
            # allauth unique_verified_email constraint would otherwise see two
            # verified copies of the same address inside this transaction.
            row.delete()
            if source_verified and not existing.verified:
                existing.verified = True
                existing.save(update_fields=['verified'])
            moved += 1
            continue
        row.user = target
        row.primary = False
        row.save(update_fields=['user', 'primary'])
        target_rows[row.email.lower()] = row
        moved += 1

    if target_rows and not any(row.primary for row in target_rows.values()):
        primary = next((row for row in target_rows.values() if row.verified), None)
        primary = primary or next(iter(target_rows.values()))
        primary.primary = True
        primary.save(update_fields=['primary'])
    summary['email_addresses'] = moved


def _merge_likes(target, source):
    moved = 0
    rows = list(Like.manager.select_for_update().filter(user=source).order_by('id'))
    for row in rows:
        existing = Like.manager.select_for_update().filter(user=target, task_id=row.task_id).first()
        if existing is not None:
            row.delete()
        else:
            row.user = target
            row.save(update_fields=['user'])
        moved += 1
    return moved


def _merge_personal_words(target, source):
    moved = 0
    for row in AlphabettyPersonalDictWord.objects.select_for_update().filter(user=source):
        if AlphabettyPersonalDictWord.objects.filter(user=target, word=row.word).exists():
            row.delete()
        else:
            row.user = target
            row.save(update_fields=['user'])
        moved += 1
    return moved


def _merge_chain_state_json(task, source_json, target_json):
    """Union compatible task progress so disjoint solutions are not lost."""
    try:
        source = json.loads(source_json or '{}')
        target = json.loads(target_json or '{}')
    except (TypeError, ValueError):
        return None
    if not isinstance(source, dict) or not isinstance(target, dict):
        return None

    task_type = getattr(task, 'task_type', None)
    if task_type == 'replacements_lines':
        solved = sorted(set(source.get('solved_lines') or []) | set(target.get('solved_lines') or []))
        return json.dumps({'solved_lines': solved, 'total': len(solved)}, ensure_ascii=False)

    if task_type == 'raddle':
        from games.raddle import (
            load_raddle_state,
            parse_raddle_data,
            resolve_assist_tiers,
            word_solve_credit,
        )

        parsed = parse_raddle_data(task)
        if not parsed:
            return None
        source_state = load_raddle_state(source_json, parsed['n_words'])
        target_state = load_raddle_state(target_json, parsed['n_words'])
        solved = sorted(
            set(source_state.get('solved_indices') or [])
            | set(target_state.get('solved_indices') or [])
        )
        used_hints = sorted(
            set(source_state.get('used_hints') or [])
            | set(target_state.get('used_hints') or [])
        )
        assist = resolve_assist_tiers(source_state)
        for index, tier in resolve_assist_tiers(target_state).items():
            assist[index] = max(assist.get(index, 0), tier)
        total = sum(
            word_solve_credit(assist.get(index, 0), parsed.get('assist') or {})
            for index in solved
            if index not in (0, parsed['n_words'] - 1)
        )
        return json.dumps({
            'solved_indices': solved,
            'used_hints': used_hints,
            'assist_tier': {str(index): tier for index, tier in assist.items()},
            'total': float(total),
        }, ensure_ascii=False)

    if task_type == 'word_salad':
        from games.word_salad import dump_state, load_state, parse_task_data, removable_cells

        source_state = load_state(source)
        target_state = load_state(target)
        solved = set(source_state.get('solved_indices') or []) | set(target_state.get('solved_indices') or [])
        hint_counts = dict(source_state.get('hint_counts') or {})
        for index, count in (target_state.get('hint_counts') or {}).items():
            hint_counts[index] = max(int(hint_counts.get(index, 0) or 0), int(count or 0))
        try:
            grid, words = parse_task_data(task.checker_data, task.answer)
        except (TypeError, ValueError):
            return None
        active = set(range(16))
        while True:
            removable = removable_cells(grid, words, active, solved)
            if not removable:
                break
            active.discard(removable[0])
        return dump_state({
            'solved_indices': sorted(solved),
            'hints': sorted(hint_counts),
            'hint_counts': hint_counts,
            'active': sorted(active),
        })

    if task_type == 'wall':
        def union_rows(primary, secondary):
            result = list(primary or [])
            for value in secondary or []:
                if value not in result:
                    result.append(value)
            return result

        source_words = source.get('guessed_words') or []
        target_words = target.get('guessed_words') or []
        primary, secondary = (
            (source, target) if len(source_words) >= len(target_words) else (target, source)
        )
        guessed_words = union_rows(primary.get('guessed_words'), secondary.get('guessed_words'))
        guessed_explanations = union_rows(
            primary.get('guessed_explanations'), secondary.get('guessed_explanations'),
        )
        guessed_explanations = [row for row in guessed_explanations if row in guessed_words]
        try:
            checker_data = json.loads(task.checker_data or '{}')
            points_words = float(checker_data.get('points_words', 0) or 0)
            points_explanation = float(checker_data.get('points_explanation', 0) or 0)
            points_bonus = float(checker_data.get('points_bonus', 0) or 0)
            answers_count = len(checker_data.get('answers') or [])
            best_points = len(guessed_words) * points_words + len(guessed_explanations) * points_explanation
            if answers_count and len(guessed_words) >= answers_count and len(guessed_explanations) >= answers_count:
                best_points += points_bonus
                best_status = 'Ok'
            else:
                best_status = 'Partial' if best_points else 'Wrong'
        except (TypeError, ValueError):
            best_points = max(source.get('best_points', 0) or 0, target.get('best_points', 0) or 0)
            best_status = primary.get('best_status') or 'Partial'
        return json.dumps({
            'best_status': best_status,
            'best_points': best_points,
            'guessed_words': guessed_words,
            'guessed_explanations': guessed_explanations,
            'last_attempt': primary.get('last_attempt') or secondary.get('last_attempt') or {},
        }, ensure_ascii=False)

    return None


def _merge_chain_states(target, source):
    from games.anon_migrate import (
        _merge_alphabetty_states,
        _rebuild_alphabetty_state_from_attempts,
        _solved_count,
    )

    moved = 0
    rows = list(
        ChainTaskState.objects.select_for_update()
        .filter(user=source, team__isnull=True, anon_key__isnull=True)
        .select_related('task', 'game')
    )
    for row in rows:
        existing = ChainTaskState.objects.select_for_update().filter(
            user=target,
            team__isnull=True,
            anon_key__isnull=True,
            task_id=row.task_id,
            game_id=row.game_id,
            game_mode=row.game_mode,
        ).first()
        if existing is None:
            row.user = target
            row.save(update_fields=['user', 'updated_at'])
            moved += 1
            continue
        merged = _merge_chain_state_json(row.task, row.state, existing.state)
        if getattr(row.task, 'task_type', None) == 'alphabetty':
            merged = _rebuild_alphabetty_state_from_attempts(
                user=target,
                task=row.task,
                game=row.game,
                anon_json=row.state,
                user_json=existing.state,
            )
        if merged is None:
            merged = _merge_alphabetty_states(row.state, existing.state)
        if merged is not None:
            source_is_richer = _solved_count(row.state) > _solved_count(existing.state)
            existing.state = merged
            if source_is_richer:
                existing.last_attempt = row.last_attempt
            existing.save(update_fields=['state', 'last_attempt', 'updated_at'])
        elif _solved_count(row.state) > _solved_count(existing.state):
            existing.state = row.state
            existing.last_attempt = row.last_attempt
            existing.save(update_fields=['state', 'last_attempt', 'updated_at'])
        row.delete()
        moved += 1
    return moved


def _merge_started_games(target, source):
    moved = 0
    for row in PlayerStartedGame.objects.select_for_update().filter(user=source):
        existing = PlayerStartedGame.objects.select_for_update().filter(
            user=target, game_instance_id=row.game_instance_id,
        ).first()
        if existing is None:
            row.user = target
            row.save(update_fields=['user'])
        else:
            updates = []
            if row.started_at and (not existing.started_at or row.started_at < existing.started_at):
                existing.started_at = row.started_at
                updates.append('started_at')
            if existing.metrika_acked_at is None and row.metrika_acked_at is not None:
                existing.metrika_acked_at = row.metrika_acked_at
                updates.append('metrika_acked_at')
            merged_backfilled = bool(existing.is_backfilled and row.is_backfilled)
            if existing.is_backfilled != merged_backfilled:
                existing.is_backfilled = merged_backfilled
                updates.append('is_backfilled')
            if updates:
                existing.save(update_fields=updates)
            row.delete()
        moved += 1
    return moved


def _merge_completed_games(target, source):
    rank = {
        PlayerCompletedGame.RESULT_FAILED: 0,
        PlayerCompletedGame.RESULT_COMPLETED: 1,
        PlayerCompletedGame.RESULT_SOLVED: 2,
    }
    moved = 0
    for row in PlayerCompletedGame.objects.select_for_update().filter(user=source):
        existing = PlayerCompletedGame.objects.select_for_update().filter(
            user=target, game_instance_id=row.game_instance_id,
        ).first()
        if existing is None:
            row.user = target
            row.save(update_fields=['user'])
        else:
            updates = []
            if row.completed_at and row.completed_at < existing.completed_at:
                existing.completed_at = row.completed_at
                updates.append('completed_at')
            if rank.get(row.result, 0) > rank.get(existing.result, 0):
                existing.result = row.result
                updates.append('result')
            if existing.metrika_acked_at is None and row.metrika_acked_at is not None:
                existing.metrika_acked_at = row.metrika_acked_at
                updates.append('metrika_acked_at')
            merged_backfilled = bool(existing.is_backfilled and row.is_backfilled)
            if existing.is_backfilled != merged_backfilled:
                existing.is_backfilled = merged_backfilled
                updates.append('is_backfilled')
            if updates:
                existing.save(update_fields=updates)
            row.delete()
        moved += 1
    return moved


def _merge_analytics(target, source):
    row = PlayerAnalyticsState.objects.select_for_update().filter(user=source).first()
    if row is None:
        return 0
    existing = PlayerAnalyticsState.objects.select_for_update().filter(user=target).first()
    if existing is None:
        row.user = target
        row.save(update_fields=['user', 'updated_at'])
        return 1

    updates = []
    for timestamp_field in ('signup_at', 'activated_at'):
        source_value = getattr(row, timestamp_field)
        target_value = getattr(existing, timestamp_field)
        if source_value and (not target_value or source_value < target_value):
            setattr(existing, timestamp_field, source_value)
            updates.append(timestamp_field)
    for ack_field in ('signup_goal_acked_at', 'activation_goal_acked_at'):
        if getattr(existing, ack_field) is None and getattr(row, ack_field) is not None:
            setattr(existing, ack_field, getattr(row, ack_field))
            updates.append(ack_field)
    if not existing.signup_method and row.signup_method:
        existing.signup_method = row.signup_method
        updates.append('signup_method')
    merged_backfilled = bool(existing.activation_is_backfilled and row.activation_is_backfilled)
    if existing.activation_is_backfilled != merged_backfilled:
        existing.activation_is_backfilled = merged_backfilled
        updates.append('activation_is_backfilled')
    if updates:
        existing.save(update_fields=updates + ['updated_at'])
    row.delete()
    return 1


@transaction.atomic
def merge_accounts(*, target_user, source_user, provider, provider_uid):
    """Merge source into target, preserving target identity and profile choices."""
    User = get_user_model()
    ids = sorted((target_user.pk, source_user.pk))
    locked = User.objects.select_for_update().in_bulk(ids)
    target = locked.get(target_user.pk)
    source = locked.get(source_user.pk)
    if target is None or source is None:
        raise AccountMergeError('Один из профилей больше не существует.')

    previous = AccountMerge.objects.filter(
        source_user_id_snapshot=source.pk,
        target_user_id_snapshot=target.pk,
    ).first()
    if previous is not None:
        return previous

    preview = build_account_merge_preview(target, source)
    if not preview['can_merge']:
        raise AccountMergeError('Профили нельзя безопасно объединить: {}.'.format(
            ', '.join(preview['conflicts']),
        ))
    if not SocialAccount.objects.select_for_update().filter(
        user=source, provider=provider, uid=provider_uid,
    ).exists():
        raise AccountMergeError('Подтверждённый аккаунт провайдера больше не найден.')

    summary = {}
    _merge_profiles(target, source, summary)
    _merge_user_fields(target, source)
    _merge_email_addresses(target, source, summary)

    # Attempts must move before chain state so Alphabetty can rebuild its exact
    # chronological guess order from both histories.
    summary['attempts'] = Attempt.manager.filter(user=source).update(user=target)
    summary['hints'] = HintAttempt.objects.filter(user=source).update(user=target)
    summary['chain_states'] = _merge_chain_states(target, source)
    summary['likes'] = _merge_likes(target, source)
    summary['personal_dict_words'] = _merge_personal_words(target, source)
    summary['started_games'] = _merge_started_games(target, source)
    summary['completed_games'] = _merge_completed_games(target, source)
    summary['analytics_states'] = _merge_analytics(target, source)
    summary['anon_claims'] = AnonAccountClaim.objects.filter(user=source).update(user=target)
    # A source-profile deep link must not remain usable after deactivation.
    telegram_link_tokens = getattr(source, 'telegram_link_tokens', None)
    if telegram_link_tokens is not None:
        summary['telegram_link_tokens'] = telegram_link_tokens.count()
        telegram_link_tokens.all().delete()

    related_models = [
        ('ticket_requests', TicketRequest, 'created_by'),
        ('donations', Donation, 'user'),
        ('bug_reports', BugReport, 'user'),
        ('dict_suggestions', AlphabettyDictSuggestion, 'user'),
        ('statistics_events', StatisticsEvent, 'user'),
        ('ladder_offers', LadderOffer, 'user'),
        ('alphabetty_offers', AlphabettyOffer, 'user'),
    ]
    # Payment models can be installed independently of account merging. When
    # present, keep their audit records attached to the surviving user.
    for label, model_name, field in (
        ('tribute_payment_intents', 'TributePaymentIntent', 'user'),
        ('tribute_purchases', 'TributePurchase', 'matched_user'),
    ):
        try:
            model = apps.get_model('games', model_name)
        except LookupError:
            continue
        related_models.append((label, model, field))
    for label, model, field in related_models:
        summary[label] = model.objects.filter(**{field: source}).update(**{field: target})

    source_accounts = list(SocialAccount.objects.select_for_update().filter(user=source))
    for account in source_accounts:
        account.user = target
        account.save(update_fields=['user'])
    summary['social_accounts'] = len(source_accounts)
    summary['prior_merge_records'] = AccountMerge.objects.filter(
        target_user=source,
    ).update(target_user=target)

    merge = AccountMerge.objects.create(
        target_user=target,
        source_user=source,
        target_user_id_snapshot=target.pk,
        source_user_id_snapshot=source.pk,
        provider=provider,
        provider_uid=str(provider_uid),
        summary=summary,
    )

    source.is_active = False
    source.email = ''
    source.set_unusable_password()
    source.save(update_fields=['is_active', 'email', 'password'])
    return merge
