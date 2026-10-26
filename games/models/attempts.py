import uuid
import json

from django.core.validators import RegexValidator
from django.db import models, transaction
from games.results.util import better_status

class AttemptsInfo:
    def __init__(self, best_attempt, attempts, hint_attempts, letter_hint_penalty=None):
        self.best_attempt = best_attempt
        self.attempts = attempts
        self.hint_attempts = hint_attempts
        # Preloaded alphabetty letter-hint penalty; None looks it up per row.
        self.letter_hint_penalty = letter_hint_penalty
        self.last_attempt = None
        if attempts:
            self.last_attempt = attempts[-1]

    def get_n_attempts(self):
        return len(self.attempts)

    def is_solved(self):
        return self.best_attempt and self.best_attempt.status == 'Ok'

    def get_sum_hint_penalty(self):
        from games.puzzles.raddle import RaddlePuzzle
        total = 0
        if self.hint_attempts:
            for hint_attempt in self.hint_attempts:
                if not hint_attempt.is_real_request:
                    continue
                hint = hint_attempt.hint
                if hint is None:
                    continue
                if RaddlePuzzle.skips_hint_penalty(hint):
                    continue
                total += hint.points_penalty or 0
        from games.puzzles.alphabetty import AlphabettyPuzzle
        total += AlphabettyPuzzle.letter_hint_penalty(self)
        return total

    def get_result_points(self):
        from games.puzzles import get_puzzle
        sample = self.last_attempt or self.best_attempt
        puzzle = None
        if sample is not None:
            puzzle = get_puzzle(getattr(getattr(sample, 'task', None), 'task_type', None))
        reader = getattr(puzzle, 'result_points', None) if puzzle is not None else None
        if reader is not None:
            return reader(self)
        result_points = 0
        if self.best_attempt:
            result_points += self.best_attempt.points
        return max(0, result_points - self.get_sum_hint_penalty())

    def get_result_attempt(self):
        from games.puzzles import get_puzzle
        sample = self.last_attempt or self.best_attempt
        puzzle = None
        if sample is not None:
            puzzle = get_puzzle(getattr(getattr(sample, 'task', None), 'task_type', None))
        reader = getattr(puzzle, 'result_attempt', None) if puzzle is not None else None
        if reader is not None:
            return reader(self)
        return self.best_attempt

    def get_hint_numbers(self):
        from games.puzzles import get_puzzle
        sample = self.last_attempt or self.best_attempt
        puzzle = None
        if sample is not None:
            puzzle = get_puzzle(getattr(getattr(sample, 'task', None), 'task_type', None))
        reader = getattr(puzzle, 'hint_numbers', None) if puzzle is not None else None
        if reader is not None:
            return reader(self)
        return [
            ha.hint.number
            for ha in sorted(
                [ha for ha in (self.hint_attempts or []) if ha.is_real_request],
                key=lambda ha: ha.hint.key_sort(),
            )
        ]


class AttemptManager(models.Manager):
    def _namespace(self, queryset, replay_slot=None):
        return queryset.filter(replay_slot=replay_slot)

    def get_all_task_attempts(self, task, exclude_skip=True, game=None, replay_slot=None):
        queryset = super().get_queryset().filter(task=task).select_related('team', 'user')
        if game is not None:
            queryset = queryset.filter(game=game)
        queryset = self._namespace(queryset, replay_slot)
        if exclude_skip:
            queryset = queryset.exclude(skip=True)
        return sorted(queryset, key=lambda x: (x.time, x.pk))

    def _filter_by_actor(self, queryset, team=None, user=None, anon_key=None):
        if team is not None:
            return queryset.filter(team=team, user__isnull=True, anon_key__isnull=True)
        if user is not None:
            return queryset.filter(user=user, team__isnull=True, anon_key__isnull=True)
        if anon_key is not None:
            return queryset.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        return queryset.none()

    def _attempt_row_game(self, row, hint_game=None):
        """Game for tournament filtering: Attempt.game, or HintAttempt + hint_game context."""
        if isinstance(row, Attempt):
            if row.game_id:
                return row.game
            return GameTaskGroup.resolve_game_for_task(row.task)
        if hint_game is not None:
            return hint_game
        return GameTaskGroup.resolve_game_for_task(row.hint.task)

    def get_all_attempts(self, team, task, exclude_skip=True, user=None, anon_key=None, game=None, replay_slot=None):
        queryset = super().get_queryset()
        if not isinstance(task, Task):
            queryset = queryset.select_related('task')
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task)
        if game is not None:
            queryset = queryset.filter(game=game)
        queryset = self._namespace(queryset, replay_slot)
        attempts = sorted(queryset, key=lambda x: (x.time, x.pk))
        if isinstance(task, Task):
            # Reuse the task the caller already loaded. Joining it to every
            # attempt duplicates large puzzle JSON in both the DB response and
            # Python objects (hundreds of MB for a long Censorly history).
            for attempt in attempts:
                attempt.task = task
        return attempts

    def get_all_attempts_after_equal(self, team, task, time, exclude_skip=True, user=None, anon_key=None, game=None, replay_slot=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task, time__gte=time)
        if game is not None:
            queryset = queryset.filter(game=game)
        queryset = self._namespace(queryset, replay_slot)
        return sorted(queryset, key=lambda x: (x.time, x.pk))

    def get_all_attempts_after(self, team, task, time, exclude_skip=True, user=None, anon_key=None, game=None, replay_slot=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task, time__gt=time)
        if game is not None:
            queryset = queryset.filter(game=game)
        queryset = self._namespace(queryset, replay_slot)
        return sorted(queryset, key=lambda x: (x.time, x.pk))

    def get_all_attempts_before(self, team, task, time, exclude_skip=True, user=None, anon_key=None, game=None, replay_slot=None):
        queryset = super().get_queryset()
        if exclude_skip:
            queryset = queryset.exclude(skip=exclude_skip)
        queryset = self._filter_by_actor(queryset, team=team, user=user, anon_key=anon_key)
        queryset = queryset.filter(task=task, time__lt=time)
        if game is not None:
            queryset = queryset.filter(game=game)
        queryset = self._namespace(queryset, replay_slot)
        return sorted(queryset, key=lambda x: (x.time, x.pk))

    def filter_attempts_with_mode(self, attempts, mode='general', is_hint_attempts=False, hint_game=None):
        if mode == 'general' or not attempts:
            return attempts
        if mode == 'tournament':
            return [
                attempt for attempt in attempts
                if self._attempt_row_game(attempt, hint_game=hint_game).has_access(
                    'attempt_is_tournament', attempt=attempt, team=attempt.team, mode=mode,
                )
            ]
        raise Exception('Unknown mode: {}'.format(mode))

    def get_attempts(self, team, task, mode="general", user=None, anon_key=None, game=None, replay_slot=None):
        attempts = self.get_all_attempts(team, task, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot)
        return self.filter_attempts_with_mode(attempts, mode, hint_game=game)

    def get_hint_attempts(self, team, task, mode="general", user=None, anon_key=None, game=None, replay_slot=None):
        # The previous implementation queried HintAttempt once per hint.  Raddle
        # cards can contain many hints, so rendering a card after a correct
        # answer turned into an N+1 query path.  Filter by task in one query;
        # hint_id/time preserves the old task.hints/default-id ordering.
        hint_attempts_qs = self._filter_by_actor(
            HintAttempt.objects.filter(
                hint__task=task,
                replay_slot=replay_slot,
            ).select_related('hint'),
            team=team,
            user=user,
            anon_key=anon_key,
        ).order_by('hint_id', 'time', 'pk')
        hint_attempts = list(hint_attempts_qs)
        return self.filter_attempts_with_mode(hint_attempts, mode, is_hint_attempts=True, hint_game=game)

    def get_attempts_before(self, team, task, time, mode="general", user=None, anon_key=None, game=None, replay_slot=None):
        attempts = self.get_all_attempts_before(team, task, time, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot)
        return self.filter_attempts_with_mode(attempts, mode, hint_game=game)

    def get_task_attempts(self, task, mode="general", game=None, replay_slot=None):
        attempts = self.get_all_task_attempts(task, game=game, replay_slot=replay_slot)
        return self.filter_attempts_with_mode(attempts, mode)

    def get_task_hint_attempts(self, task, mode="general", game=None, replay_slot=None):
        hint_attempts = list(
            HintAttempt.objects.filter(hint__task=task, replay_slot=replay_slot).select_related('hint', 'team', 'user')
        )
        return self.filter_attempts_with_mode(hint_attempts, mode, is_hint_attempts=True, hint_game=game)

    def get_best_attempt(self, attempts, mode="general"):
        best_attempt = None
        for attempt in attempts:
            if best_attempt is None or \
               attempt.points > best_attempt.points or \
               (attempt.points == best_attempt.points and better_status(attempt.status, best_attempt.status)):
                best_attempt = attempt
        return best_attempt

    def get_attempts_info(self, team, task, mode="general", user=None, anon_key=None, game=None, replay_slot=None):
        attempts = self.get_attempts(team, task, mode, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot)
        hint_attempts = self.get_hint_attempts(team, task, mode, user=user, anon_key=anon_key, game=game, replay_slot=replay_slot)
        best_attempt = self.get_best_attempt(attempts, mode)
        return AttemptsInfo(best_attempt, attempts, hint_attempts)

    def get_bulk_actor_attempts_infos(
        self,
        task_ids,
        *,
        team=None,
        user=None,
        anon_key=None,
        mode="general",
        game=None,
        replay_slot=None,
    ):
        """Load one actor's AttemptsInfo for many tasks in two queries."""
        task_ids = [int(task_id) for task_id in (task_ids or []) if task_id is not None]
        if not task_ids:
            return {}

        attempt_related = []
        if mode != 'general':
            attempt_related.append('game')
        if mode != 'general' and game is None:
            attempt_related.append('task')
        # Results are grouped by task below, so only per-task chronology matters.
        # Putting task_id first lets the existing (task, actor, time) indexes
        # provide the requested order without sorting all tasks together.
        attempts_qs = self._filter_by_actor(
            self.filter(task_id__in=task_ids, replay_slot=replay_slot).exclude(skip=True),
            team=team,
            user=user,
            anon_key=anon_key,
        ).order_by('task_id', 'time', 'pk')
        if attempt_related:
            attempts_qs = attempts_qs.select_related(*attempt_related)
        if game is not None:
            attempts_qs = attempts_qs.filter(game=game)
        attempts = list(attempts_qs)
        attempts = list(self.filter_attempts_with_mode(attempts, mode, hint_game=game))

        hint_related = ['hint']
        if mode != 'general' and game is None:
            hint_related.append('hint__task')
        hint_attempts_qs = self._filter_by_actor(
            HintAttempt.objects.filter(hint__task_id__in=task_ids, replay_slot=replay_slot),
            team=team,
            user=user,
            anon_key=anon_key,
        ).select_related(*hint_related).order_by('time', 'pk')
        hint_attempts = list(hint_attempts_qs)
        hint_attempts = list(self.filter_attempts_with_mode(
            hint_attempts,
            mode,
            is_hint_attempts=True,
            hint_game=game,
        ))

        task_attempts = {task_id: [] for task_id in task_ids}
        task_hint_attempts = {task_id: [] for task_id in task_ids}
        for attempt in attempts:
            task_attempts.setdefault(attempt.task_id, []).append(attempt)
        for hint_attempt in hint_attempts:
            task_hint_attempts.setdefault(hint_attempt.hint.task_id, []).append(hint_attempt)

        return {
            task_id: AttemptsInfo(
                self.get_best_attempt(task_attempts.get(task_id, []), mode),
                task_attempts.get(task_id, []),
                task_hint_attempts.get(task_id, []),
            )
            for task_id in task_ids
        }

    # for results page
    def get_task_attempts_infos(self, task, mode="general", game=None, replay_slot=None):
        attempts = self.get_task_attempts(task, mode, game=game, replay_slot=replay_slot)
        hint_attempts = self.get_task_hint_attempts(task, mode, game=game, replay_slot=replay_slot)

        teams = set()

        team_to_attempts = {}
        for attempt in attempts:
            if attempt.team not in team_to_attempts:
                team_to_attempts[attempt.team] = []
            team_to_attempts[attempt.team].append(attempt)
            teams.add(attempt.team)

        team_to_hint_attempts = {}
        for hint_attempt in hint_attempts:
            if hint_attempt.team not in team_to_hint_attempts:
                team_to_hint_attempts[hint_attempt.team] = []
            team_to_hint_attempts[hint_attempt.team].append(hint_attempt)
            teams.add(hint_attempt.team)

        attempts_infos = []
        for team in teams:
            attempts = team_to_attempts.get(team, [])
            hint_attempts = team_to_hint_attempts.get(team, [])
            best_attempt = self.get_best_attempt(attempts, mode)
            attempts_info = AttemptsInfo(best_attempt, attempts, hint_attempts)
            attempts_infos.append(attempts_info)
        return attempts_infos

    def _general_results_actor_bucket(self, attempt_or_hint):
        if attempt_or_hint.team_id:
            return ('team', attempt_or_hint.team_id)
        uid = getattr(attempt_or_hint, 'user_id', None)
        if uid:
            return ('user', uid)
        ak = getattr(attempt_or_hint, 'anon_key', None)
        if ak:
            return ('anon', str(ak))
        return None

    def get_bulk_game_actor_rows(self, task_ids, mode='general', game=None, replay_slot=None, actor_filter=None, include_hidden=False):
        """
        O(1) bulk alternative to calling get_general_results_task_actor_rows or
        get_task_attempts_infos for every task individually.

        Loads tasks, attempts, hint attempts, hidden anon keys and (for
        alphabetty) letter-hint states in a fixed number of queries, groups by
        (task_id, actor) in Python, and returns:
            {task_id: [(actor, AttemptsInfo), ...]}
        Scoring the returned AttemptsInfo objects needs no further queries.

        Hidden teams are already filtered out.  For 'general' mode all actor
        types (team / personal user / anon) are returned; for 'tournament' mode
        personal rows are included but callers typically filter them.

        Pass ``game`` when the caller already has the Game object — this avoids
        the costly task__task_group__game JOIN chain used by filter_attempts_with_mode
        to look up the tournament window.
        """
        from collections import defaultdict

        if not task_ids:
            return {}

        hidden_anons = hidden_anon_keys()
        # 1 query: one shared Task per id instead of a lazy load per attempt.
        tasks_by_id = Task.objects.in_bulk(task_ids)

        # 1 query: all attempts for all tasks (optionally scoped to one game).
        attempt_related = ['team', 'user', 'game']
        attempt_qs = self.filter(task_id__in=task_ids, skip=False, replay_slot=replay_slot).select_related(*attempt_related).order_by('time', 'pk')
        if actor_filter:
            attempt_qs = attempt_qs.filter(**actor_filter)
        if game is not None:
            attempt_qs = attempt_qs.filter(game=game)
        all_attempts = list(attempt_qs)
        for a in all_attempts:
            task = tasks_by_id.get(a.task_id)
            if task is not None:
                a.task = task
        if mode == 'tournament':
            all_attempts = self.filter_attempts_with_mode(all_attempts, mode)

        # 1 query: all hint attempts for all hints in these tasks.
        hint_related = ['hint', 'team', 'user']
        all_hint_attempts = list(
            HintAttempt.objects.filter(hint__task_id__in=task_ids, replay_slot=replay_slot)
            .filter(**(actor_filter or {}))
            .select_related(*hint_related)
        )
        if mode == 'tournament':
            all_hint_attempts = self.filter_attempts_with_mode(
                all_hint_attempts, mode, is_hint_attempts=True, hint_game=game,
            )

        # Group by (task_id, actor_bucket) in Python
        task_actor_attempts = defaultdict(lambda: defaultdict(list))
        task_actor_hints = defaultdict(lambda: defaultdict(list))
        actor_obj_cache = {}  # bucket -> Team / User object / anon_key string

        for a in all_attempts:
            b = self._general_results_actor_bucket(a)
            if b is None:
                continue
            task_actor_attempts[a.task_id][b].append(a)
            if b not in actor_obj_cache:
                kind, _ = b
                actor_obj_cache[b] = a.team if kind == 'team' else (a.user if kind == 'user' else a.anon_key)

        for ha in all_hint_attempts:
            b = self._general_results_actor_bucket(ha)
            if b is None:
                continue
            task_actor_hints[ha.hint.task_id][b].append(ha)
            if b not in actor_obj_cache:
                kind, _ = b
                actor_obj_cache[b] = ha.team if kind == 'team' else (ha.user if kind == 'user' else ha.anon_key)

        # 1 query when alphabetty tasks are present.  Like the per-row lookup,
        # the penalty is keyed by the sample attempt's game and always reads
        # the official (replay_slot NULL) state.
        letter_penalties = {}
        alphabetty_ids = [pk for pk, task in tasks_by_id.items() if task.task_type == 'alphabetty']
        if alphabetty_ids:
            from games.alphabetty.play import letter_hint_penalties_by_actor
            letter_penalties = letter_hint_penalties_by_actor(
                alphabetty_ids, game=game, actor_filter=actor_filter,
            )

        # Build {task_id: [(actor, AttemptsInfo), ...]}
        result = {}
        for task_id in task_ids:
            all_buckets = (
                set(task_actor_attempts[task_id].keys())
                | set(task_actor_hints[task_id].keys())
            )
            rows = []
            for b in all_buckets:
                att = task_actor_attempts[task_id].get(b, [])
                hints = task_actor_hints[task_id].get(b, [])
                if not att and not hints:
                    continue
                best = self.get_best_attempt(att)
                sample = best or (att[-1] if att else None)
                letter_penalty = 0
                if sample is not None:
                    letter_penalty = letter_penalties.get((sample.task_id, sample.game_id, b), 0)
                ai = AttemptsInfo(best, att, hints, letter_hint_penalty=letter_penalty)
                kind, key = b
                obj = actor_obj_cache.get(b)
                if kind == 'team':
                    if obj is None or (obj.is_hidden and not include_hidden):
                        continue
                    rows.append((obj, ai))
                elif kind == 'user':
                    rows.append((PersonalResultsParticipant(user=obj), ai))
                else:
                    if key in hidden_anons and not include_hidden:
                        continue
                    rows.append((PersonalResultsParticipant(anon_key=key), ai))
            result[task_id] = rows

        return result

    def get_general_results_task_actor_rows(self, task, game=None, replay_slot=None):
        """
        Для общей таблицы: по одному AttemptsInfo на команду или на личного/анонимного участника.
        """
        attempts = self.get_task_attempts(task, mode='general', game=game, replay_slot=replay_slot)
        hint_attempts = self.get_task_hint_attempts(task, mode='general', game=game, replay_slot=replay_slot)

        buckets = {}
        for attempt in attempts:
            b = self._general_results_actor_bucket(attempt)
            if b is None:
                continue
            buckets.setdefault(b, {'attempts': [], 'hints': []})
            buckets[b]['attempts'].append(attempt)

        for ha in hint_attempts:
            b = self._general_results_actor_bucket(ha)
            if b is None:
                continue
            buckets.setdefault(b, {'attempts': [], 'hints': []})
            buckets[b]['hints'].append(ha)

        hidden_anons = hidden_anon_keys()
        rows = []
        for b, data in buckets.items():
            att = data['attempts']
            hints = data['hints']
            if not att and not hints:
                continue
            best_attempt = self.get_best_attempt(att, mode='general')
            ai = AttemptsInfo(best_attempt, att, hints)
            kind, key = b
            if kind == 'team':
                team = att[0].team if att else hints[0].team
                if team is None or team.is_hidden:
                    continue
                actor = team
            elif kind == 'user':
                user = att[0].user if att else hints[0].user
                actor = PersonalResultsParticipant(user=user)
            else:
                if key in hidden_anons:
                    continue
                actor = PersonalResultsParticipant(anon_key=key)
            rows.append((actor, ai))
        return rows


CHAIN_TASK_TYPES = ('wall', 'replacements_lines', 'html_forms', 'raddle', 'alphabetty', 'word_salad')


class ChainTaskState(models.Model):
    """
    Authoritative accumulated chain state for wall and replacements_lines tasks.

    One row per (actor, task, game, game_mode).  Protected by SELECT FOR UPDATE during
    attempt submission so concurrent submissions for the same actor+task+mode are
    serialised at the DB level and cannot corrupt each other's chain.

    game_mode mirrors game.get_current_mode() at submission time:
      'general'    – outside tournament window (includes all historical attempts)
      'tournament' – inside tournament window (fresh start, independent chain)

    Both wall and replacements_lines use current_mode as the key, so tournament
    progress is always isolated from general progress.
    """
    team = models.ForeignKey(
        'Team', related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User', related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    task = models.ForeignKey(
        'Task', related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    game = models.ForeignKey(
        'Game', related_name='chain_task_states',
        on_delete=models.CASCADE,
    )
    replay_slot = models.ForeignKey(
        'ReplaySlot', related_name='chain_task_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    actor_key = models.CharField(max_length=128, editable=False)
    # MySQL treats NULL values as distinct in a unique index.  Keep a
    # non-null namespace key so the official (no replay) state can also be
    # unique per actor/task/mode.
    replay_slot_key = models.PositiveBigIntegerField(default=0, editable=False)
    game_mode = models.CharField(max_length=20)   # 'general' | 'tournament'

    state = models.TextField(blank=True, null=True)
    last_attempt = models.ForeignKey(
        'Attempt', blank=True, null=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Partial unique indexes per actor type: correct NULL handling on all DBs.
        constraints = [
            models.UniqueConstraint(
                fields=['actor_key', 'task', 'game', 'game_mode', 'replay_slot_key'],
                name='unique_chain_state_actor_context',
            ),
            models.UniqueConstraint(
                fields=['team', 'task', 'game', 'game_mode', 'replay_slot_key'],
                condition=models.Q(team__isnull=False),
                name='unique_chain_state_team_game',
            ),
            models.UniqueConstraint(
                fields=['user', 'task', 'game', 'game_mode', 'replay_slot_key'],
                condition=models.Q(user__isnull=False),
                name='unique_chain_state_user_game',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'task', 'game', 'game_mode', 'replay_slot_key'],
                condition=models.Q(anon_key__isnull=False),
                name='unique_chain_state_anon_key_game',
            ),
        ]
        indexes = [
            models.Index(fields=['team', 'task', 'game', 'game_mode']),
            models.Index(fields=['user', 'task', 'game', 'game_mode']),
            models.Index(fields=['anon_key', 'task', 'game', 'game_mode']),
            models.Index(fields=['replay_slot', 'task', 'game', 'game_mode']),
        ]

    def save(self, *args, **kwargs):
        if self.team_id is not None:
            self.actor_key = 'team:{}'.format(self.team_id)
        elif self.user_id is not None:
            self.actor_key = 'user:{}'.format(self.user_id)
        else:
            self.actor_key = 'anon:{}'.format(self.anon_key)
        self.replay_slot_key = self.replay_slot_id or 0
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'replay_slot_key' not in update_fields:
            kwargs['update_fields'] = set(update_fields) | {'actor_key', 'replay_slot_key'}
        super().save(*args, **kwargs)

    def __str__(self):
        actor = self.team if self.team_id else (self.user if self.user_id else self.anon_key)
        return 'ChainTaskState[{}][{}][{}]'.format(actor, self.task, self.game_mode)

    def state_summary(self):
        if not self.state:
            return '—'
        try:
            s = json.loads(self.state)
            task = self.task
            from games.puzzles import get_puzzle
            puzzle = get_puzzle(getattr(task, 'task_type', None)) if task else None
            summary = getattr(puzzle, 'state_summary', None) if puzzle is not None else None
            if summary is not None:
                return summary(s)
            if task and task.task_type == 'alphabetty':
                guesses = len(s.get('guesses') or [])
                won = 'won' if s.get('won') else 'playing'
                return '{} guesses ({})'.format(guesses, won)
        except Exception:
            pass
        return (self.state or '')[:100]


class Attempt(models.Model):
    STATUS_VARIANTS = (
        ('Ok', 'Ok'),
        ('Pending', 'Pending'),
        ('Partial', 'Partial'),
        ('Wrong', 'Wrong'),
    )

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey('Team', related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    submitted_by_profile = models.ForeignKey(
        'Profile', related_name='submitted_attempts', blank=True, null=True,
        on_delete=models.SET_NULL, verbose_name='submitted by profile',
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    task = models.ForeignKey('Task', related_name='attempts', blank=True, null=True, on_delete=models.SET_NULL)
    game = models.ForeignKey(
        'Game', related_name='game_attempts',
        blank=True, null=True, on_delete=models.SET_NULL,
    )
    replay_slot = models.ForeignKey(
        'ReplaySlot', related_name='attempts',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    manager = AttemptManager()

    # Task revision seen when this attempt was checked. Null is allowed for
    # historical/imported rows whose task is no longer available.
    task_revision = models.UUIDField(blank=True, null=True, editable=False)
    text = models.TextField()
    status = models.CharField(max_length=100, choices=STATUS_VARIANTS)
    possible_status = models.CharField(blank=True, null=True, max_length=100, choices=STATUS_VARIANTS)
    points = models.DecimalField(default=0, decimal_places=3, max_digits=10, blank=True, null=True)
    time = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    # Canonical active duration of this daily solve at this successful answer.
    # Nullable for legacy attempts which predate per-answer instrumentation.
    active_time_ms = models.BigIntegerField(blank=True, null=True, db_index=True)
    state = models.TextField(blank=True, null=True)

    comment = models.TextField(blank=True, null=True)

    skip = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['status'], name='games_attempt_status_idx'),
            models.Index(
                fields=['task', 'team', 'replay_slot', 'game', 'skip', 'points', 'time'],
                name='games_att_task_team_scope_idx',
            ),
            models.Index(
                fields=['task', 'user', 'replay_slot', 'game', 'skip', 'points', 'time'],
                name='games_att_task_user_scope_idx',
            ),
            models.Index(
                fields=['task', 'anon_key', 'replay_slot', 'game', 'skip', 'points', 'time'],
                name='games_att_task_anon_scope_idx',
            ),
            models.Index(fields=['task', 'team', 'time']),
            models.Index(fields=['task', 'user', 'time']),
            models.Index(fields=['task', 'anon_key', 'time']),
            models.Index(fields=['task', 'status']),
            models.Index(fields=['skip', 'time'], name='games_attempt_skip_time_idx'),
            models.Index(fields=['replay_slot', 'task', 'game', 'time'], name='games_att_rep_task_time_idx'),
        ]

    def __str__(self):
        actor = self.team if self.team is not None else (self.user if self.user is not None else self.anon_key)
        time_s = self.time.strftime('%Y-%m-%d %H:%M:%S') if self.time else ''
        return '[{}]: ({}) - {} [{}] ({})'.format(
            actor, self.task, self.get_pretty_text(), self.status, time_s
        )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.task_id and self.game_id and self.replay_slot_id is None:
                group_id = Task.objects.filter(
                    pk=self.task_id,
                ).values_list('task_group_id', flat=True).first()
                game = Game.objects.filter(
                    pk=self.game_id, project_id='sections',
                ).first()
                if group_id and game is not None:
                    from games.daily.projection.outbox import enqueue_actor_projection_refresh
                    enqueue_actor_projection_refresh(
                        game, TaskGroup.objects.get(pk=group_id),
                        team_id=self.team_id,
                        user_id=self.user_id,
                        anon_key=self.anon_key,
                    )

    def get_answer(self):
        if self.task is None:
            return 'DELETED'
        from games.puzzles import get_puzzle
        puzzle = get_puzzle(self.task.task_type)
        reader = getattr(puzzle, 'attempt_answer', None) if puzzle is not None else None
        if reader is not None:
            return reader(self)
        return self.task.answer

    def get_max_points(self):
        if self.task is None:
            return 'DELETED'
        return self.task.get_points()

    def get_pretty_text(self):
        if self.task is None:
            return 'DELETED TASK'
        from games.puzzles import get_puzzle
        puzzle = get_puzzle(self.task.task_type)
        if puzzle is None and self.task.task_type == 'grid-puzzle':
            from games.puzzles.replacements.grid import GridPuzzle
            puzzle = GridPuzzle
        if puzzle is None and self.task.task_type == 'distribute_to_teams':
            from games.puzzles.core.short.distribute_to_teams import DistributeToTeamsPuzzle
            puzzle = DistributeToTeamsPuzzle
        reader = getattr(puzzle, 'pretty_text', None) if puzzle is not None else None
        if reader is not None:
            return reader(self)
        if self.task.task_type in ('default', 'with_tag', 'text_with_forms', 'autohint', 'proportions'):
            return self.text
        if self.task.task_type == 'alphabetty':
            return self.text
        return self.text


class PendingAttempt(Attempt):
    class Meta:
        proxy=True


class LikeManager(models.Manager):
    def _actor_filter(self, team=None, user=None, anon_key=None):
        if team is not None:
            return {'team': team, 'user__isnull': True, 'anon_key__isnull': True}
        if user is not None:
            return {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
        return {'anon_key': anon_key, 'team__isnull': True, 'user__isnull': True}

    def get_likes(self, task, team=None):
        # По умолчанию показываем сумму КОМАНДНЫХ лайков (как раньше).
        if team is None:
            return super().get_queryset().filter(task=task, value=1, team__isnull=False, user__isnull=True, anon_key__isnull=True).count()
        return super().get_queryset().filter(task=task, value=1, **self._actor_filter(team=team)).count()

    def get_dislikes(self, task, team=None):
        # По умолчанию показываем сумму КОМАНДНЫХ дизлайков (как раньше).
        if team is None:
            return super().get_queryset().filter(task=task, value=-1, team__isnull=False, user__isnull=True, anon_key__isnull=True).count()
        return super().get_queryset().filter(task=task, value=-1, **self._actor_filter(team=team)).count()

    def get_total_likes(self, task):
        return super().get_queryset().filter(task=task, value=1).count()

    def get_total_dislikes(self, task):
        return super().get_queryset().filter(task=task, value=-1).count()

    def team_has_like(self, task, team):
        return self.get_likes(task, team) > 0

    def team_has_dislike(self, task, team):
        return self.get_dislikes(task, team) > 0

    def actor_has_like(self, task, team=None, user=None, anon_key=None):
        return super().get_queryset().filter(task=task, value=1, **self._actor_filter(team=team, user=user, anon_key=anon_key)).exists()

    def actor_has_dislike(self, task, team=None, user=None, anon_key=None):
        return super().get_queryset().filter(task=task, value=-1, **self._actor_filter(team=team, user=user, anon_key=anon_key)).exists()

    def set_actor_reaction(self, task, value, team=None, user=None, anon_key=None):
        """Atomically replace an actor's reaction, healing any duplicate rows."""
        if value not in (-1, 0, 1):
            raise ValueError('Reaction value must be -1, 0, or 1')
        if sum(actor is not None for actor in (team, user, anon_key)) != 1:
            raise ValueError('Exactly one reaction actor is required')

        actor_filter = self._actor_filter(team=team, user=user, anon_key=anon_key)
        with transaction.atomic():
            # A reaction row may not exist yet, so lock the always-present task row.
            # This serializes concurrent reaction changes for the same task.
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            super().get_queryset().filter(task=task, **actor_filter).delete()
            if value:
                self.create(
                    task=task,
                    team=team,
                    user=user,
                    anon_key=anon_key,
                    value=value,
                )

    def add_like(self, task, team):
        self.set_actor_reaction(task, 1, team=team)

    def add_dislike(self, task, team):
        self.set_actor_reaction(task, -1, team=team)

    def delete_like(self, task, team):
        with transaction.atomic():
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            super().get_queryset().filter(
                task=task, value=1, **self._actor_filter(team=team)
            ).delete()

    def delete_dislike(self, task, team):
        with transaction.atomic():
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            super().get_queryset().filter(
                task=task, value=-1, **self._actor_filter(team=team)
            ).delete()

    def add_like_actor(self, task, team=None, user=None, anon_key=None):
        self.set_actor_reaction(task, 1, team=team, user=user, anon_key=anon_key)

    def add_dislike_actor(self, task, team=None, user=None, anon_key=None):
        self.set_actor_reaction(task, -1, team=team, user=user, anon_key=anon_key)

    def delete_like_actor(self, task, team=None, user=None, anon_key=None):
        with transaction.atomic():
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            super().get_queryset().filter(
                task=task,
                value=1,
                **self._actor_filter(team=team, user=user, anon_key=anon_key),
            ).delete()

    def delete_dislike_actor(self, task, team=None, user=None, anon_key=None):
        with transaction.atomic():
            Task.objects.select_for_update().only('pk').get(pk=task.pk)
            super().get_queryset().filter(
                task=task,
                value=-1,
                **self._actor_filter(team=team, user=user, anon_key=anon_key),
            ).delete()


class Like(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey('Team', related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    task = models.ForeignKey('Task', related_name='likes', blank=True, null=True, on_delete=models.SET_NULL)
    value = models.IntegerField()
    manager = LikeManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('task', 'team'), name='uniq_like_task_team'),
            models.UniqueConstraint(fields=('task', 'user'), name='uniq_like_task_user'),
            models.UniqueConstraint(fields=('task', 'anon_key'), name='uniq_like_task_anon'),
        ]

    def __str__(self):
        actor = self.team if self.team is not None else (self.user if self.user is not None else self.anon_key)
        return '{} to task {} by {}'.format('Like' if self.value == 1 else 'Dislike', self.task, actor)


HINT_NUMBER_VALIDATOR = RegexValidator(
    regex=r'^\d+(?:\.\d+)?$',
    message='Номер подсказки: целое число или число с точкой (например 1 или 1.5).',
)


class Hint(models.Model):
    id = models.AutoField(primary_key=True)
    task = models.ForeignKey('Task', related_name='hints', blank=True, null=True, on_delete=models.SET_NULL)
    number = models.CharField(max_length=20, null=True, blank=True, validators=[HINT_NUMBER_VALIDATOR])
    desc = models.TextField(null=True, blank=True)
    text = models.TextField(null=True, blank=True)
    points_penalty = models.DecimalField(decimal_places=3, max_digits=10, blank=True, null=True, default=1)
    required_hints = models.ManyToManyField('Hint', blank=True)

    def __str__(self):
        return '{} - Подсказка #{} [-{}]'.format(self.task, self.number, self.points_penalty)

    @staticmethod
    def number_key(number):
        return GameTaskGroup.number_key(number)

    def key_sort(self):
        if self.number is None:
            return ()
        return self.number_key(self.number)

    def save(self, *args, **kwargs):
        from games.realtime.track_compat import track_task_change
        super(Hint, self).save(*args, **kwargs)
        if self.task_id and self.task.task_group_id:
            from games.daily_result_projection import mark_projection_dirty
            for link in GameTaskGroup.objects.filter(
                task_group_id=self.task.task_group_id, game__project_id='sections',
            ).select_related('game', 'task_group'):
                transaction.on_commit(
                    lambda game=link.game, task_group=link.task_group:
                    mark_projection_dirty(game, task_group, full=True),
                )
        track_task_change(self.task)

class HintAttempt(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey('Team', related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    requested_by_profile = models.ForeignKey(
        'Profile', related_name='requested_hint_attempts', blank=True, null=True,
        on_delete=models.SET_NULL, verbose_name='requested by profile',
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    hint = models.ForeignKey(Hint, related_name='hint_attempts', blank=True, null=True, on_delete=models.SET_NULL)
    replay_slot = models.ForeignKey(
        'ReplaySlot', related_name='hint_attempts',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    time = models.DateTimeField(auto_now_add=True, blank=True)
    is_real_request = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['hint', 'team', 'time']),
            models.Index(fields=['hint', 'user', 'time']),
            models.Index(fields=['hint', 'anon_key', 'time']),
            models.Index(fields=['hint', 'is_real_request']),
            models.Index(fields=['replay_slot', 'hint', 'time']),
        ]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.hint_id and self.replay_slot_id is None:
                group_id = Hint.objects.filter(
                    pk=self.hint_id,
                ).values_list('task__task_group_id', flat=True).first()
                task_group = TaskGroup.objects.filter(pk=group_id).first() if group_id else None
                game_ids = GameTaskGroup.objects.filter(
                    task_group_id=group_id, game__project_id='sections',
                ).values_list('game_id', flat=True) if group_id else []
                if task_group is not None:
                    from games.daily.projection.outbox import enqueue_actor_projection_refresh
                    for game in Game.objects.filter(pk__in=game_ids, project_id='sections'):
                        enqueue_actor_projection_refresh(
                            game, task_group,
                            team_id=self.team_id,
                            user_id=self.user_id,
                            anon_key=self.anon_key,
                        )

    def __str__(self):
        actor = self.team if self.team is not None else (self.user if self.user is not None else self.anon_key)
        return '{}[{}]: {}'.format(
            '(just watching)' if self.is_real_request else '',
            actor,
            self.hint
        )


class ReplaySlot(models.Model):
    """The single private replay slot for one actor/game/task-group pair."""

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(
        'Team', related_name='replay_slots', blank=True, null=True, on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User', related_name='replay_slots', blank=True, null=True, on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    actor_key = models.CharField(max_length=128, editable=False)
    game = models.ForeignKey('Game', related_name='replay_slots', on_delete=models.CASCADE)
    task_group = models.ForeignKey(
        'TaskGroup', related_name='replay_slots', on_delete=models.CASCADE,
    )
    run_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(max_length=16, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['actor_key', 'game', 'task_group'],
                name='uniq_replay_slot_actor_key_game_tg',
            ),
            models.UniqueConstraint(
                fields=['team', 'game', 'task_group'],
                condition=models.Q(team__isnull=False),
                name='uniq_replay_slot_team_game_tg',
            ),
            models.UniqueConstraint(
                fields=['user', 'game', 'task_group'],
                condition=models.Q(user__isnull=False),
                name='uniq_replay_slot_user_game_tg',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'game', 'task_group'],
                condition=models.Q(anon_key__isnull=False),
                name='uniq_replay_slot_anon_game_tg',
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(team__isnull=False) & models.Q(user__isnull=True) & models.Q(anon_key__isnull=True))
                    | (models.Q(team__isnull=True) & models.Q(user__isnull=False) & models.Q(anon_key__isnull=True))
                    | (models.Q(team__isnull=True) & models.Q(user__isnull=True) & models.Q(anon_key__isnull=False))
                ),
                name='replay_slot_exactly_one_actor',
            ),
        ]
        indexes = [
            models.Index(fields=['game', 'task_group', 'updated_at'], name='games_replay_slot_game_tg_idx'),
            models.Index(fields=['run_id'], name='games_replay_slot_run_idx'),
        ]

    def __str__(self):
        actor = self.team or self.user or self.anon_key or '—'
        return '{} · {}:{} · {}'.format(actor, self.game_id, self.task_group_id, self.run_id)

from games.models.catalog import Game, GameTaskGroup, Task, TaskGroup  # noqa: E402
from games.models.people import PersonalResultsParticipant, hidden_anon_keys  # noqa: E402
