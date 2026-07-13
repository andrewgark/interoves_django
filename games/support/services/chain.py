import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from django.urls import reverse

from games.models import CHAIN_TASK_TYPES, Attempt, ChainTaskState


@dataclass(frozen=True)
class ChainAttemptRow:
    attempt_id: int
    time: object
    status: str
    points: str
    skip: bool
    submission_text: str
    correct_answer: str
    state_preview: str


def _parse_state_preview(raw: Optional[str], *, max_len: int = 120) -> str:
    if not raw:
        return '—'
    try:
        compact = json.dumps(json.loads(raw), ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError):
        compact = (raw or '').replace('\n', ' ')
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1] + '…'


def is_chain_task(attempt: Attempt) -> bool:
    task = attempt.task
    return bool(task and task.task_type in CHAIN_TASK_TYPES)


def _attempt_display(attempt: Attempt) -> str:
    from games.support.services.feed import attempt_submission_text
    return attempt_submission_text(attempt, max_len=500)


def _attempt_answer(attempt: Attempt) -> str:
    from games.support.services.feed import attempt_correct_answer
    return attempt_correct_answer(attempt, max_len=500)


def build_chain_context(attempt_id: int) -> Dict:
    attempt = (
        Attempt.manager.filter(pk=attempt_id)
        .select_related('team', 'user', 'user__profile', 'task', 'task__task_group', 'game')
        .first()
    )
    if attempt is None:
        return None
    if not is_chain_task(attempt):
        return None

    game = attempt.game
    task = attempt.task
    chain_rows = list(
        ChainTaskState.objects.filter(
            task=task,
            game=game,
            team=attempt.team,
            user=attempt.user,
            anon_key=attempt.anon_key,
        ).select_related('last_attempt')
    )
    attempts = list(
        Attempt.manager.get_all_attempts(
            attempt.team,
            task,
            exclude_skip=False,
            user=attempt.user,
            anon_key=attempt.anon_key,
            game=game,
        )
    )
    attempt_rows = [
        ChainAttemptRow(
            attempt_id=a.pk,
            time=a.time,
            status=a.status,
            points=str(a.points) if a.points is not None else '—',
            skip=bool(a.skip),
            submission_text=_attempt_display(a),
            correct_answer=_attempt_answer(a),
            state_preview=_parse_state_preview(a.state),
        )
        for a in attempts
    ]

    from games.support.services.feed import actor_label_for_attempt, actor_url_for_attempt

    actor_label = actor_label_for_attempt(attempt)
    actor_url = actor_url_for_attempt(attempt)
    preview_url = None
    if game and task and task.task_group:
        from games.models import GameTaskGroup
        from games.support.services.preview import ActorSpec, preview_task_group_url

        link = GameTaskGroup.objects.filter(game=game, task_group=task.task_group).first()
        if link:
            if attempt.team_id:
                spec = ActorSpec(kind='team', team_name=attempt.team_id, play_mode='team')
            elif attempt.user_id:
                spec = ActorSpec(kind='user', user_id=attempt.user_id, play_mode='personal')
            elif attempt.anon_key:
                spec = ActorSpec(kind='anon', anon_key=attempt.anon_key, play_mode='personal')
            else:
                spec = None
            if spec:
                preview_url = preview_task_group_url(game.id, link.number, spec)

    return {
        'attempt': attempt,
        'game': game,
        'task': task,
        'actor_label': actor_label,
        'actor_url': actor_url,
        'chain_rows': chain_rows,
        'attempt_rows': attempt_rows,
        'is_chain': True,
        'preview_url': preview_url,
        'admin_attempt_url': reverse('admin:games_attempt_change', args=[attempt.pk]),
        'page_title': 'Chain · attempt #{}'.format(attempt.pk),
    }


def format_chain_state(state_text: Optional[str]) -> str:
    if not state_text:
        return ''
    try:
        return json.dumps(json.loads(state_text), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return state_text or ''
