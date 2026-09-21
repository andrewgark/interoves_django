from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from io import StringIO
from django.core.management import call_command

from games.leaderboard import (
    apply_release_policy, canonical_leaderboard_durations, eligible_public_actors,
    individual_sports_key, sports_rank,
)
from games.models import (
    DailySolveTiming, Game, GameTaskGroup, HiddenAnonKey, PersonalResultsParticipant,
    Profile, Project, ReplaySlot, Task, TaskGroup, Team,
)


class LeaderboardSharedTests(TestCase):
    def test_eligibility_hides_profiles_teams_and_anonymous_keys(self):
        user = User.objects.create_user(username='hidden-profile')
        profile = Profile.objects.create(user=user, first_name='Hidden', last_name='Player', is_hidden=True)
        visible_user = User.objects.create_user(username='visible-profile')
        Profile.objects.create(user=visible_user, first_name='Visible', last_name='Player')
        hidden_team = Team.objects.create(name='hidden-team', is_hidden=True)
        visible_team = Team.objects.create(name='visible-team')
        HiddenAnonKey.objects.create(anon_key='anonymous-hidden-key')
        actors = [
            PersonalResultsParticipant(user_id=profile.pk),
            PersonalResultsParticipant(user_id=visible_user.pk),
            hidden_team, visible_team,
            PersonalResultsParticipant(anon_key='anonymous-hidden-key'),
            PersonalResultsParticipant(anon_key='anonymous-visible-key'),
        ]
        self.assertEqual(
            eligible_public_actors(actors),
            [actors[1], visible_team, actors[5]],
        )

    def test_sports_rank_uses_all_official_criteria_and_ties_shared_place(self):
        a, b, c = object(), object(), object()
        ordered = [a, b, c]
        self.assertEqual(sports_rank(ordered, {a: (-10, 1), b: (-10, 2), c: (-9, 0)}), {a: 1, b: 2, c: 3})
        self.assertEqual(sports_rank(ordered, {a: (-10, 1), b: (-10, 1), c: (-9, 0)}), {a: 1, b: 1, c: 3})

    def test_tournament_snapshot_place_uses_finish_time_but_general_snapshot_does_not(self):
        from games.results_snapshot import snapshot_to_results_context
        early = Team.objects.create(name='snapshot-early')
        late = Team.objects.create(name='snapshot-late')
        rows = [
            {'row_kind': 'team', 'team_id': late.pk, 'score': 10, 'place': 1,
             'max_best_time': '2026-09-01T12:02:00+00:00', 'cells': []},
            {'row_kind': 'team', 'team_id': early.pk, 'score': 10, 'place': 1,
             'max_best_time': '2026-09-01T12:01:00+00:00', 'cells': []},
        ]
        tournament = snapshot_to_results_context(
            self.game, {'mode': 'tournament', 'task_groups': [], 'rows': rows},
        )
        self.assertEqual(tournament['teams_sorted'], [early, late])
        self.assertEqual(tournament['team_to_place'], {early: 1, late: 2})
        general = snapshot_to_results_context(
            self.game, {'mode': 'general', 'task_groups': [], 'rows': rows},
        )
        self.assertEqual(general['team_to_place'], {early: 1, late: 1})

    def test_alphabetty_official_attempts_and_time_modes_keep_score_primary(self):
        fewer_attempts = individual_sports_key(score=10, duration=90, game_id='alphabetty', attempts=4)
        faster = individual_sports_key(score=10, duration=60, game_id='alphabetty', attempts=5)
        self.assertLess(fewer_attempts, faster)
        self.assertLess(
            individual_sports_key(score=10, duration=60, game_id='alphabetty', attempts=5, alphabetty_sort='time'),
            individual_sports_key(score=10, duration=90, game_id='alphabetty', attempts=4, alphabetty_sort='time'),
        )
        self.assertLess(
            individual_sports_key(score=11, duration=None, game_id='alphabetty', attempts=99),
            individual_sports_key(score=10, duration=1, game_id='alphabetty', attempts=1),
        )
        self.assertGreater(
            individual_sports_key(score=10, duration=None),
            individual_sports_key(score=10, duration=999),
        )

    def setUp(self):
        self.project, _ = Project.objects.get_or_create(pk='sections', defaults={'name': 'Sections'})
        self.game = Game.objects.get(pk='ladder')
        self.group = TaskGroup.objects.create(label='Ladder 1')
        self.link = GameTaskGroup.objects.create(game=self.game, task_group=self.group, number='1')
        self.author = User.objects.create_user(username='author')
        self.coauthor = User.objects.create_user(username='coauthor')
        self.other = User.objects.create_user(username='other')
        for user in (self.author, self.coauthor, self.other):
            Profile.objects.create(user=user, first_name=user.username, last_name='Player')
        self.group.authors.add(self.author.profile, self.coauthor.profile)

    def test_task_authors_excluded_for_personal_and_team_only_on_that_task(self):
        personal_author = PersonalResultsParticipant(user=self.author)
        personal_coauthor = PersonalResultsParticipant(user=self.coauthor)
        ordinary = PersonalResultsParticipant(user=self.other)
        anonymous = PersonalResultsParticipant(anon_key='some-anon-key')
        team_with_author = Team.objects.create(name='author-team')
        self.author.profile.add_team_membership(team_with_author, make_primary=True)
        team_without_author = Team.objects.create(name='ordinary-team')
        other_group = TaskGroup.objects.create(label='Unrelated task')
        other_group.authors.add(self.other.profile)

        self.assertEqual(
            eligible_public_actors(
                [personal_author, personal_coauthor, ordinary, anonymous, team_with_author, team_without_author],
                task_group=self.group,
            ),
            [ordinary, anonymous, team_without_author],
        )
        self.assertEqual(
            eligible_public_actors([personal_author], task_group=other_group),
            [personal_author],
        )

    def test_prepublication_timing_start_hides_even_if_result_is_later(self):
        actor = PersonalResultsParticipant(user=self.other)
        publish = timezone.now()
        row = DailySolveTiming.objects.create(
            user=self.other, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=9000, frozen_ms=9000,
        )
        DailySolveTiming.objects.filter(pk=row.pk).update(created_at=publish - timedelta(seconds=2))
        self.assertEqual(eligible_public_actors(
            [actor], task_group=self.group, game=self.game, published_at=publish,
            result_times={actor: publish + timedelta(seconds=2)},
        ), [])

        team = Team.objects.create(name='prepublication-team')
        team_result = DailySolveTiming.objects.create(
            team=team, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=9000, frozen_ms=9000,
        )
        DailySolveTiming.objects.filter(pk=team_result.pk).update(created_at=publish - timedelta(seconds=1))
        self.assertEqual(eligible_public_actors(
            [team], task_group=self.group, game=self.game, published_at=publish,
            result_times={team: publish + timedelta(seconds=2)},
        ), [])
        postpublication_team = Team.objects.create(name='postpublication-team')
        postpublication_row = DailySolveTiming.objects.create(
            team=postpublication_team, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=4000, frozen_ms=4000,
        )
        DailySolveTiming.objects.filter(pk=postpublication_row.pk).update(
            created_at=publish + timedelta(seconds=1),
        )
        self.assertEqual(eligible_public_actors(
            [postpublication_team], task_group=self.group, game=self.game, published_at=publish,
            result_times={postpublication_team: publish + timedelta(seconds=2)},
        ), [postpublication_team])
        replay = ReplaySlot.objects.create(actor_key='team:{}'.format(team.pk), team=team,
                                           game=self.game, task_group=self.group)
        DailySolveTiming.objects.create(
            team=team, game=self.game, task_group=self.group, replay_slot=replay,
            team_timing_key='replay:{}'.format(replay.pk),
            status=DailySolveTiming.STATUS_COMPLETED, frozen_ms=1000, accumulated_ms=1000,
            created_at=publish + timedelta(minutes=1),
        )
        self.assertEqual(eligible_public_actors(
            [team], task_group=self.group, game=self.game, published_at=publish,
            result_times={team: publish + timedelta(seconds=2)},
        ), [])
        replay = ReplaySlot.objects.create(
            actor_key='user:{}'.format(self.other.pk), user=self.other,
            game=self.game, task_group=self.group,
        )
        DailySolveTiming.objects.create(
            user=self.other, game=self.game, task_group=self.group, replay_slot=replay,
            status=DailySolveTiming.STATUS_COMPLETED, frozen_ms=1000, accumulated_ms=1000,
            created_at=publish + timedelta(minutes=1),
        )
        self.assertEqual(eligible_public_actors(
            [actor], task_group=self.group, game=self.game, published_at=publish,
            result_times={actor: publish + timedelta(seconds=2)},
        ), [])

    def test_unknown_start_is_conservatively_visible_and_duration_has_no_legacy_guess(self):
        actor = PersonalResultsParticipant(user=self.other)
        publish = timezone.now()
        self.assertEqual(eligible_public_actors(
            [actor], task_group=self.group, game=self.game, published_at=publish,
        ), [actor])
        self.assertEqual(canonical_leaderboard_durations(
            game=self.game, task_group=self.group, actors=[actor],
        ), {})
        old_team_result = DailySolveTiming.objects.create(
            team=Team.objects.create(name='legacy-no-time'), game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=50000, frozen_ms=50000,
            timing_version=0,
        )
        self.assertEqual(canonical_leaderboard_durations(
            game=self.game, task_group=self.group, actors=[old_team_result.team],
        ), {})
        self.assertEqual(eligible_public_actors(
            [actor], task_group=self.group, game=self.game, published_at=publish,
            result_times={actor: publish - timedelta(seconds=1)},
        ), [])
        self.assertEqual(eligible_public_actors(
            [actor], task_group=self.group, game=self.game, published_at=publish,
            result_times={actor: publish + timedelta(seconds=1)},
        ), [actor])

    def test_release_order_uses_known_duration_then_null_and_shares_exact_sports_rank(self):
        fast_user = User.objects.create_user(username='fast')
        slow_user = User.objects.create_user(username='slow')
        Profile.objects.create(user=fast_user, first_name='Fast', last_name='Player')
        Profile.objects.create(user=slow_user, first_name='Slow', last_name='Player')
        fast = PersonalResultsParticipant(user=fast_user)
        slow = PersonalResultsParticipant(user=slow_user)
        missing = Team.objects.create(name='missing-time-team')
        DailySolveTiming.objects.create(
            user=fast_user, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=60000, frozen_ms=60000,
        )
        DailySolveTiming.objects.create(
            user=slow_user, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=120000, frozen_ms=120000,
        )
        context = apply_release_policy({
            'teams_sorted': [missing, slow, fast],
            'team_to_score': {missing: 10, slow: 10, fast: 10},
            'team_to_cells': {missing: [], slow: [], fast: []},
            'team_to_max_best_time': {},
        }, game=self.game, task_group=self.group)
        self.assertEqual(context['teams_sorted'], [fast, slow, missing])
        self.assertEqual(context['team_to_place'], {fast: 1, slow: 2, missing: 3})

    def test_canonical_duration_uses_frozen_active_milliseconds_and_ignores_replay(self):
        actor = PersonalResultsParticipant(user=self.other)
        DailySolveTiming.objects.create(
            user=self.other, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=999999,
            frozen_ms=277000,
        )
        with self.assertNumQueries(1):
            durations = canonical_leaderboard_durations(
                game=self.game, task_group=self.group, actors=[actor],
            )
        self.assertEqual(durations[actor], 277)
        replay = ReplaySlot.objects.create(actor_key='user:{}'.format(self.other.pk), user=self.other,
                                           game=self.game, task_group=self.group)
        DailySolveTiming.objects.create(
            user=self.other, game=self.game, task_group=self.group, replay_slot=replay,
            status=DailySolveTiming.STATUS_COMPLETED, frozen_ms=1000, accumulated_ms=1000,
        )
        self.assertEqual(canonical_leaderboard_durations(
            game=self.game, task_group=self.group, actors=[actor],
        )[actor], 277)

    def test_team_canonical_duration_is_available_to_individual_leaderboard(self):
        team = Team.objects.create(name='timed-team')
        DailySolveTiming.objects.create(
            team=team, game=self.game, task_group=self.group,
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=111000, frozen_ms=111000,
        )
        self.assertEqual(canonical_leaderboard_durations(
            game=self.game, task_group=self.group, actors=[team],
        )[team], 111)

    def test_author_backfill_dry_run_apply_idempotency_and_ambiguity(self):
        self.group.authors.clear()
        task = self.group.tasks.create(number='1', tags={'author': 'author Player; coauthor Player'})
        out = StringIO()
        call_command('backfill_task_authors', stdout=out)
        self.assertIn('status=unique', out.getvalue())
        self.assertIn('Dry run only', out.getvalue())
        self.assertFalse(self.group.authors.filter(user=self.author).exists())
        call_command('backfill_task_authors', '--apply', stdout=StringIO())
        self.assertEqual(set(self.group.authors.values_list('user_id', flat=True)), {self.author.pk, self.coauthor.pk})
        out = StringIO()
        call_command('backfill_task_authors', '--apply', stdout=out)
        self.assertIn('status=already linked', out.getvalue())

        duplicate = User.objects.create_user(username='author-duplicate')
        Profile.objects.create(user=duplicate, first_name='Author', last_name='Player')
        self.group.authors.remove(self.author.profile)
        out = StringIO()
        call_command('backfill_task_authors', '--apply', stdout=out)
        self.assertIn('status=ambiguous', out.getvalue())
        self.assertFalse(self.group.authors.filter(user=self.author).exists())

    def test_salad_breakdown_uses_saved_word_state_and_preserves_aggregate_score(self):
        from types import SimpleNamespace
        from games.views.new_ui import _word_salad_release_breakdown
        from games.word_salad import serialize_task_data

        task = Task.objects.create(
            task_group=self.group, number='salad', task_type='word_salad',
            checker_data=serialize_task_data('A B C D\nH G F E\nI J K L\nP O N M', 'ABCD\nBCDA'),
        )
        actor = PersonalResultsParticipant(user=self.other)
        state = {'solved_indices': [0], 'hint_counts': {'0': 1, '1': 2}}
        attempt = SimpleNamespace(state=__import__('json').dumps(state))
        info = SimpleNamespace(attempts=[attempt])
        data = {
            'teams_sorted': [actor], 'team_to_score': {actor: 0},
            'team_to_list_attempts_info': {actor: [info]},
            'team_to_cells': {actor: []},
        }
        out = _word_salad_release_breakdown(data, self.game, self.link.number)
        self.assertEqual([h.number for h in out['task_group_to_tasks']['1']], ['ABCD', 'BCDA'])
        self.assertEqual([cell['result_points'] for cell in out['team_to_cells'][actor]], [0.5, -1.0])
        self.assertEqual(out['team_to_score'][actor], 0)
