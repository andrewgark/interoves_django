"""Published work a player or a team wrote, for the "Автор" profile section.

Two kinds of authorship exist:

* daily releases — ``TaskGroup.authors`` (profiles); a team owns what its
  members wrote, like in the monthly tables;
* Десяточки — ``GameAuthor`` grants on the game (a profile or a team);
  a team page lists only the team's own grants.

Only what players can already open is listed: daily releases in their
public number order up to today's, Десяточки that are ready and started.

A daily-game release with no recorded author is shown as written by the
site's author (``settings.DAILY_DEFAULT_AUTHOR_USER_ID``). This is display only:
tables and author credit still use the recorded authors.
"""

from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from games.club.access.archive import DESYATOCHKA_ID_RE
from games.daily.page.section import DAILY_TIMING_GAME_IDS, MOSCOW, schedule_for
from games.models import Game, GameAuthor, TaskGroup


@dataclass
class AuthoredItem:
    title: str
    url: str
    number: int | None = None
    published_on: date | None = None


@dataclass
class AuthoredGame:
    game_id: str
    name: str
    items: list = field(default_factory=list)


@dataclass
class AuthorWork:
    daily: list = field(default_factory=list)  # AuthoredGame, one per daily game
    desyatochki: list = field(default_factory=list)  # AuthoredItem

    @property
    def daily_count(self):
        return sum(len(game.items) for game in self.daily)

    def daily_counts(self):
        return {game.game_id: len(game.items) for game in self.daily}

    @property
    def has_any(self):
        return bool(self.daily or self.desyatochki)


def player_author_work(user, *, now=None):
    from games.community import game_month

    return _author_work(
        game_month.user_actor(user),
        Q(profile_id=user.pk),
        now=now,
    )


def team_author_work(team, *, now=None):
    """Десяточки granted to the team itself; a member's own ones stay theirs."""
    from games.community import game_month

    return _author_work(game_month.team_actor(team), Q(team=team), now=now)


def _author_work(actor, desyatka_grants, *, now=None):
    now = now or timezone.now()
    return AuthorWork(
        daily=daily_releases(actor, now=now),
        desyatochki=_desyatochki(desyatka_grants, now=now),
    )


def daily_releases(actor, *, now=None):
    """Published daily releases ``actor`` (a ``game_month.Actor``) wrote, by game."""
    now = now or timezone.now()
    from games.community.game_month import authored_group_ids, month_games
    from games.results.standings.aggregate_leaderboard import _numbered_links
    from games.task_group.navigation import play_url_for_task_group

    authored = authored_group_ids(actor)
    # Weekly and Десяточка-round sections reuse other authors' rounds:
    # only the daily games proper default to the site's author.
    default_game_ids = (
        set(month_games())
        if not actor.is_team and actor.obj.pk == settings.DAILY_DEFAULT_AUTHOR_USER_ID else set()
    )
    game_ids = set(default_game_ids)
    if authored:
        game_ids |= set(
            Game.objects.filter(
                pk__in=DAILY_TIMING_GAME_IDS,
                task_group_links__task_group_id__in=authored,
            ).values_list('pk', flat=True)
        )
    if not game_ids:
        return []
    unauthored = TaskGroup.objects.filter(authors__isnull=True).values('pk')
    out = []
    for game in Game.objects.filter(pk__in=game_ids):
        mine = Q(task_group_id__in=authored)
        if game.pk in default_game_ids:
            mine |= Q(task_group_id__in=unauthored)
        # The same "published" rule as the monthly tables and "как автор".
        links = sorted(
            _numbered_links(game, now).filter(mine),
            key=lambda link: link._release_number, reverse=True,
        )
        if not links:
            continue
        schedule = schedule_for(game.pk)
        game_name = game.get_no_html_name() or game.name
        items = []
        for link in links:
            published_at = schedule.publish_at(game, link.number) if schedule else None
            items.append(AuthoredItem(
                title=_release_title(link, game_name),
                url=play_url_for_task_group(game, link.number),
                number=link._release_number,
                published_on=published_at.astimezone(MOSCOW).date() if published_at else None,
            ))
        out.append(AuthoredGame(
            game_id=str(game.pk),
            name=game_name,
            items=items,
        ))
    out.sort(key=lambda game: (-len(game.items), game.name))
    return out


def _release_title(link, game_name):
    """``Лесенка #120`` under the "Лесенка" heading reads as ``#120``."""
    number = str(link.number)
    name = (link.name or '').strip()
    rest = name[len(game_name):]
    if name.startswith(game_name) and (not rest or not rest[0].isalnum()):
        name = rest.strip(' ·:—-')
    if not name or name in (number, '#' + number, '№' + number):
        return '#{}'.format(number)
    return name


def _desyatochki(grants, *, now):
    game_ids = set(GameAuthor.objects.filter(grants).values_list('game_id', flat=True))
    games = Game.objects.filter(
        pk__in=game_ids, project_id='main', is_ready=True, start_time__lte=now,
    )
    out = []
    for game in games:
        match = DESYATOCHKA_ID_RE.fullmatch(str(game.pk))
        if match is None:
            continue
        start = game.get_visible_start_time()
        out.append(AuthoredItem(
            title=game.get_no_html_name() or game.name,
            url='/games/{}/'.format(game.pk),
            number=int(match.group(1)),
            published_on=start.astimezone(MOSCOW).date() if start else None,
        ))
    out.sort(key=lambda item: -item.number)
    return out
