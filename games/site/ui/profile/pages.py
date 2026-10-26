"""Main UI: hub, games folder, profile, team."""
import datetime
import hmac
import logging

import pytz
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST


from django.db import IntegrityError

from allauth.socialaccount.models import SocialAccount

from games.club.merge.account import social_provider_label

from games.models import (
    BugReport,
    Profile,
)
from games.identity import has_profile


from games.site.ui import (
    NEW_UI_PROJECT,
    _merge_nav_project_for_scope,
    _profile_redirect,
    _project_urls_context,
    _report_detail_redirect,
    _scoped_project_id,
)
from games.site.ui.profile.form import (
    ProfileSettingsForm,
    _profile_unavailable_redirect,
)

logger = logging.getLogger('games.views.new_ui')

@login_required
@require_http_methods(['GET'])
def new_profile_reports(request, project_id=None):
    if not has_profile(request.user):
        return _profile_unavailable_redirect(request)
    from games.site.ui.profile.feedback import preview_text, reports_for_user, unread_report_ids_for_user

    reports = list(reports_for_user(request.user))
    unread_ids = unread_report_ids_for_user(request.user)
    cards = []
    for report in reports:
        cards.append({
            'report': report,
            'preview': preview_text(report.text),
            'status_label': report.status_label_ru(),
            'unread': report.pk in unread_ids,
            'task_url': report.page_url or '',
        })
    scoped = _scoped_project_id(request) or project_id
    ctx = {
        'report_cards': cards,
        'page_title': 'Мои обращения',
    }
    ctx.update(_project_urls_context(scoped or NEW_UI_PROJECT))
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile_reports.html', ctx)


@login_required
@require_http_methods(['GET', 'POST'])
def new_profile_report_detail(request, report_id, project_id=None):
    if not has_profile(request.user):
        return _profile_unavailable_redirect(request)
    from games.site.ui.profile.feedback import add_user_reply, mark_report_read

    report = get_object_or_404(
        BugReport.objects.select_related('task', 'task__task_group', 'game', 'game__project', 'user'),
        pk=report_id,
        user=request.user,
    )
    if request.method == 'POST':
        text = (request.POST.get('text') or '').strip()
        if not text:
            messages.error(request, 'Напишите сообщение.')
        elif not report.user_can_reply():
            messages.error(request, 'Это обращение закрыто.')
        else:
            try:
                add_user_reply(report, request.user, text)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, 'Сообщение отправлено.')
                return _report_detail_redirect(request, report.pk)
    mark_report_read(report)
    scoped = _scoped_project_id(request) or project_id
    ctx = {
        'report': report,
        'thread': list(report.messages.select_related('author_user').all()),
        'can_reply': report.user_can_reply(),
        'status_label': report.status_label_ru(),
        'task_url': report.page_url or '',
        'page_title': 'Обращение',
    }
    ctx.update(_project_urls_context(scoped or NEW_UI_PROJECT))
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile_report_detail.html', ctx)


def _safe_profile_next(raw):
    path = str(raw or '').strip()
    if not path.startswith('/') or path.startswith('//') or '\\' in path:
        return '/profile/'
    return path[:500]


@login_required
@require_POST
def new_dismiss_unverified_telegram_handle(request):
    """Drop a typed Telegram nick when the person declines to verify it."""
    if has_profile(request.user):
        profile = request.user.profile
        if not profile.telegram_verified and profile.telegram_handle:
            profile.telegram_handle = ''
            profile.save(update_fields=['telegram_handle'])
    return redirect(_safe_profile_next(request.POST.get('next')))


@login_required
@require_http_methods(['GET', 'POST'])
def new_profile(request, project_id=None):
    profile_next_path = '/subscription/' if (
        request.GET.get('next') == '/subscription/'
        or request.POST.get('next') == '/subscription/'
    ) else ''
    if not has_profile(request.user):
        profile, created = Profile.objects.get_or_create(
            user=request.user,
            defaults={'first_name': '', 'last_name': ''},
        )
        if created:
            messages.info(request, 'Заполните имя и фамилию профиля, чтобы продолжить оформление.')
    else:
        profile = request.user.profile
    # ModelForm can update its instance with valid fields even when another
    # field fails validation. Keep the read-only card on saved values.
    display_first_name = (profile.first_name or '').strip()
    display_last_name = (profile.last_name or '').strip()
    profile_avatar_url = profile.avatar_url
    profile_timezone = profile.timezone
    profile_telegram_handle = (
        (profile.telegram_username or profile.telegram_handle or '').strip()
    )
    profile_telegram_label = (
        ('@' + profile_telegram_handle if profile_telegram_handle else 'Подключён')
        if profile.telegram_verified else 'Не подключён'
    )
    connected_accounts = list(
        SocialAccount.objects.filter(user=request.user).order_by('provider', 'id')
    )
    connected = {account.provider for account in connected_accounts}
    connected_account_labels = {}
    connected_account_ids = {}
    for account in connected_accounts:
        extra = account.extra_data or {}
        label = (
            extra.get('email')
            or extra.get('default_email')
            or extra.get('name')
            or extra.get('display_name')
            or extra.get('login')
            or extra.get('preferred_username')
            or extra.get('screen_name')
            or str(account.uid)
        )
        connected_account_labels.setdefault(account.provider, label)
        connected_account_ids.setdefault(account.provider, account.pk)
    if request.method == 'POST':
        form = ProfileSettingsForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль сохранён.')
            if profile_next_path:
                return redirect('new_subscription')
            return _profile_redirect(request)
    else:
        form = ProfileSettingsForm(instance=profile)
    profile_has_complete_name = bool(display_first_name and display_last_name)
    profile_edit_open = (
        request.GET.get('edit') == '1'
        or bool(form.errors)
        or not profile_has_complete_name
    )
    def _utc_offset_label(tz_name):
        try:
            tz = pytz.timezone(tz_name)
            utc_now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
            off = utc_now.astimezone(tz).utcoffset()
            if off is None:
                return tz_name
            total = int(off.total_seconds())
            sign = '+' if total >= 0 else '-'
            total = abs(total)
            hh = total // 3600
            mm = (total % 3600) // 60
            return '{} (UTC{}{:02d}:{:02d})'.format(tz_name, sign, hh, mm)
        except Exception:
            return tz_name

    tz_options = [(tz, _utc_offset_label(tz)) for tz in pytz.common_timezones]

    scoped = _scoped_project_id(request)
    from games.site.ui.profile.feedback import profile_cabinet_flags
    ctx = {
        'form': form,
        'profile_display_name': (
            ' '.join(part for part in (display_first_name, display_last_name) if part)
            or 'Имя пока не указано'
        ),
        'profile_initials': ''.join(part[0] for part in (display_first_name, display_last_name) if part) or '?',
        'profile_avatar_url': profile_avatar_url,
        'profile_timezone_label': _utc_offset_label(profile_timezone),
        'profile_telegram_label': profile_telegram_label,
        'profile_has_complete_name': profile_has_complete_name,
        'profile_edit_open': profile_edit_open,
        'profile_next_path': profile_next_path,
        'connected_providers': connected,
        'connected_accounts': connected_accounts,
        'connected_account_labels': connected_account_labels,
        'connected_account_ids': connected_account_ids,
        'can_disconnect_social_account': len(connected_accounts) > 1,
        'tz_options': tz_options,
        'page_title': 'Профиль',
    }
    ctx.update(profile_cabinet_flags(request.user))
    ctx.update(_project_urls_context(scoped or NEW_UI_PROJECT))
    _merge_nav_project_for_scope(ctx, request, scoped)
    return render(request, 'ui/profile.html', ctx)


@login_required
@require_http_methods(['GET', 'POST'])
def new_account_merge_confirm(request):
    from django.contrib.auth import get_user_model
    from games.club.merge.account import (
        AccountMergeError,
        build_account_merge_preview,
        clear_pending_account_merge,
        get_pending_account_merge,
    )
    from games.club.merge.queue import enqueue_account_merge

    pending = get_pending_account_merge(request)
    if pending is None:
        messages.info(request, 'Запрос на объединение аккаунтов истёк или уже был использован.')
        return redirect('ui_profile')

    source = get_user_model().objects.filter(pk=pending['source_user_id']).first()
    if source is None:
        clear_pending_account_merge(request)
        messages.error(request, 'Второй профиль больше не найден.')
        return redirect('ui_profile')

    preview = build_account_merge_preview(request.user, source)
    provider_label = social_provider_label(pending['provider'])
    if request.method == 'POST':
        if request.POST.get('action') != 'merge':
            next_url = pending['next']
            clear_pending_account_merge(request)
            messages.info(request, 'Аккаунты оставлены раздельными.')
            return redirect(next_url)
        if not hmac.compare_digest(
            request.POST.get('nonce') or '', pending['nonce'],
        ):
            clear_pending_account_merge(request)
            messages.error(request, 'Запрос устарел. Подключите аккаунт ещё раз.')
            return redirect('ui_profile')
        try:
            job = enqueue_account_merge(
                target_user=request.user,
                source_user=source,
                provider=pending['provider'],
                provider_uid=pending['provider_uid'],
                next_url=pending['next'],
            )
        except AccountMergeError as exc:
            messages.error(request, str(exc))
        except IntegrityError:
            logger.exception(
                'Account merge database conflict target=%s source=%s provider=%s',
                request.user.pk,
                pending['source_user_id'],
                pending['provider'],
            )
            messages.error(
                request,
                'Не удалось объединить профили из-за конфликта данных. '
                'Попробуйте ещё раз позже или обратитесь в поддержку.',
            )
        else:
            clear_pending_account_merge(request)
            messages.info(
                request,
                'Профили поставлены в очередь на объединение. Можно продолжать работу; '
                'вход через {} будет доступен после завершения.'.format(
                    provider_label,
                ),
            )
            return redirect('ui_account_merge_status', job_id=job.id)

    return render(request, 'ui/account_merge_confirm.html', {
        'preview': preview,
        'pending_merge': pending,
        'provider_label': provider_label,
        'page_title': 'Объединение аккаунтов',
    })


@login_required
@require_http_methods(['GET'])
def new_account_merge_status(request, job_id):
    from games.club.merge.queue import serialize_account_merge_job
    from games.models import AccountMergeJob

    job = AccountMergeJob.objects.filter(
        pk=job_id,
        target_user=request.user,
    ).first()
    if job is None:
        raise Http404()
    payload = serialize_account_merge_job(job)
    if request.headers.get('Accept') == 'application/json':
        return JsonResponse(payload)
    return render(request, 'new/account_merge_status.html', {
        'job': payload,
        'job_id': job.id,
        'page_title': 'Объединение аккаунтов',
    })


@login_required
@require_http_methods(['POST'])
def new_account_merge_retry(request, job_id):
    from django.contrib.auth import get_user_model
    from games.club.merge.account import AccountMergeError
    from games.club.merge.queue import enqueue_account_merge
    from games.models import AccountMergeJob

    job = AccountMergeJob.objects.filter(
        pk=job_id,
        target_user=request.user,
        status=AccountMergeJob.STATUS_FAILED,
    ).first()
    if job is None:
        raise Http404()
    source = get_user_model().objects.filter(
        pk=job.source_user_id_snapshot,
    ).first()
    if source is None:
        messages.error(request, 'Второй профиль больше не найден.')
        return redirect('ui_account_merge_status', job_id=job.id)
    try:
        job = enqueue_account_merge(
            target_user=request.user,
            source_user=source,
            provider=job.provider,
            provider_uid=job.provider_uid,
            next_url=job.next_url,
        )
    except AccountMergeError as exc:
        messages.error(request, str(exc))
        return redirect('ui_account_merge_status', job_id=job.id)
    messages.info(request, 'Повторная попытка объединения поставлена в очередь.')
    return redirect('ui_account_merge_status', job_id=job.id)


@login_required
@require_http_methods(['POST'])
def new_social_account_disconnect(request):
    account = SocialAccount.objects.filter(
        pk=request.POST.get('account_id'), user=request.user,
    ).first()
    if account is None:
        raise Http404()
    if not SocialAccount.objects.filter(user=request.user).exclude(pk=account.pk).exists():
        messages.error(request, 'Нельзя отключить единственный способ входа.')
        return _profile_redirect(request)

    from allauth.socialaccount import signals as socialaccount_signals
    from allauth.socialaccount.internal.flows.connect import validate_disconnect
    from django.utils.http import url_has_allowed_host_and_scheme

    provider_label = social_provider_label(account.provider)
    validate_disconnect(request, account)
    account.delete()
    socialaccount_signals.social_account_removed.send(
        sender=SocialAccount, request=request, socialaccount=account,
    )
    messages.success(request, '{} отключён. Остальные способы входа продолжают работать.'.format(
        provider_label,
    ))
    next_url = request.POST.get('next') or ''
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return _profile_redirect(request)


__all__ = [
    'new_profile_reports',
    'new_profile_report_detail',
    '_safe_profile_next',
    'new_dismiss_unverified_telegram_handle',
    'new_profile',
    'new_account_merge_confirm',
    'new_account_merge_status',
    'new_account_merge_retry',
    'new_social_account_disconnect',
]
