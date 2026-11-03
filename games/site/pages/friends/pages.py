"""Friends: list, requests and invite links."""

import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from games.community import friends, preview, visibility
from games.identity import has_profile
from games.models import Profile
from games.site.pages import NEW_UI_PROJECT, _project_urls_context
from games.site.pages.shared import share_url

_SAFE_NEXT = re.compile(r'/(u/[a-z0-9]{1,16}/|friends/)')

_ACTIONS = {
    'request': friends.request_friendship,
    'accept': friends.accept_request,
    'remove': friends.remove,
}

_ACTION_MESSAGES = {
    friends.RELATION_OUTGOING: 'Заявка отправлена.',
    friends.RELATION_FRIENDS: 'Теперь вы друзья.',
}


def _next_or_friends(request):
    target = (request.POST.get('next') or '').strip()
    return redirect(target if _SAFE_NEXT.fullmatch(target) else reverse('new_friends'))


def _identities(user_ids, viewer, *, are_friends=False):
    """Cards for a list of players; only friends see past a hidden page."""
    profiles = Profile.objects.filter(
        user_id__in=user_ids, is_hidden=False, user__is_active=True,
    ).order_by('first_name', 'last_name')
    return [
        {
            'public_id': p.public_id,
            'identity': visibility.public_identity(p, viewer, page_visible=are_friends),
        }
        for p in profiles
    ]


@login_required
@require_http_methods(['POST'])
def friends_action(request):
    action = _ACTIONS.get(request.POST.get('action'))
    other = Profile.objects.filter(
        public_id=(request.POST.get('player') or '').strip(), is_hidden=False, user__is_active=True,
    ).first()
    if action is None or other is None or not has_profile(request.user):
        raise Http404()
    try:
        result = action(request.user, other.user_id)
    except friends.FriendshipError as exc:
        messages.error(request, str(exc))
    else:
        text = _ACTION_MESSAGES.get(result)
        if text:
            messages.success(request, text)
    return _next_or_friends(request)


@login_required
@require_http_methods(['GET'])
def friends_page(request):
    user_id = request.user.pk
    incoming = list(friends.incoming_requests(user_id).values_list('requested_by_id', flat=True))
    outgoing = [row.other(user_id) for row in friends.outgoing_requests(user_id)]
    invite = friends.active_invite(request.user)
    ctx = {
        'friends_list': _identities(friends.friend_user_ids(user_id), request.user, are_friends=True),
        'friends_incoming': _identities(incoming, request.user),
        'friends_outgoing': _identities(outgoing, request.user),
        'friends_invite_url': share_url(
            request, reverse('new_friend_invite', kwargs={'token': invite.token}),
        ) if invite else '',
        'friends_telegram_hint': not getattr(getattr(request.user, 'profile', None), 'telegram_verified', False),
        'page_title': 'Друзья',
    }
    ctx.update(_project_urls_context(NEW_UI_PROJECT))
    return render(request, 'site/friends.html', ctx)


@login_required
@require_http_methods(['POST'])
def friend_invite_create(request):
    friends.new_invite(request.user)
    messages.success(request, 'Новая ссылка готова. Старая больше не работает.')
    return redirect('new_friends')


def _invite_context(request, invite):
    owner = getattr(invite.user, 'profile', None)
    if owner is None or owner.is_hidden or not invite.user.is_active:
        return None
    return {
        'invite': invite,
        'invite_owner': visibility.public_identity(owner, request.user),
        'invite_relation': friends.relation(request.user, invite.user_id),
        'invite_login_url': '/?' + urlencode({'login': '1', 'next': request.get_full_path()}),
        'page_title': 'Приглашение в друзья',
    }


@require_http_methods(['GET'])
def friend_invite(request, token):
    invite = friends.invite_by_token(token)
    ctx = _invite_context(request, invite) if invite else None
    if ctx is None:
        ctx = {'invite_invalid': True, 'page_title': 'Приглашение в друзья'}
        ctx.update(_project_urls_context(NEW_UI_PROJECT))
        return render(request, 'site/friend_invite.html', ctx, status=404)
    ctx['invite_has_profile'] = has_profile(request.user)
    owner = ctx['invite_owner']
    ctx['og'] = preview.og(
        request, title='{} зовёт вас в друзья'.format(owner.name),
        description='Inter Oves — ежедневные головоломки. Друзей видно отдельным фильтром в таблицах результатов.',
        image_url=owner.avatar_url if visibility.is_trusted_avatar_url(owner.avatar_url) else '',
    )
    ctx.update(_project_urls_context(NEW_UI_PROJECT))
    return render(request, 'site/friend_invite.html', ctx)


@login_required
@require_http_methods(['POST'])
def friend_invite_accept(request, token):
    invite = friends.invite_by_token(token)
    if invite is None or not has_profile(request.user):
        raise Http404()
    try:
        friends.accept_invite(invite, request.user)
    except friends.FriendshipError as exc:
        messages.error(request, str(exc))
        return redirect('new_friends')
    messages.success(request, 'Теперь вы друзья.')
    return redirect('new_player_profile', public_id=invite.user.profile.public_id)
