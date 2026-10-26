import hashlib
import secrets

from django.contrib.auth.models import User
from django.db import models, transaction
from django.utils import timezone

class Team(models.Model):
    name = models.CharField(max_length=100, primary_key=True)
    visible_name = models.TextField(blank=True, null=True)
    name_hash = models.CharField(max_length=256, editable=False, null=True)
    is_tester = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    project = models.ForeignKey(
        'Project', related_name='teams',
        default='main', on_delete=models.CASCADE
    )

    tickets = models.IntegerField(default=0)
    ticket_price = models.IntegerField(default=2000)
    ticket_price_amd = models.IntegerField(default=10000)

    referer = models.ForeignKey('Team', related_name='referents', blank=True, null=True, on_delete=models.SET_NULL)
    join_password = models.CharField(max_length=32, blank=True, null=True)


    def save_name_hash(self):
        self.name_hash = hashlib.sha512((self.name + 'salt').encode()).hexdigest()[:50]

    def get_name_hash(self):
        self.save_name_hash()
        return str(self.name_hash)

    def save(self, *args, **kwargs):
        if not self.visible_name:
            self.visible_name = self.name
        if not self.join_password:
            # короткий пароль "для своих" — можно менять в /new/team/
            self.join_password = secrets.token_hex(4)  # 8 hex chars
        self.save_name_hash()
        super(Team, self).save(*args, **kwargs)

    def __str__(self):
        return self.visible_name
    
    def get_n_users_on(self):
        return self.member_links.count()

    def get_n_users_requested(self):
        return len(self.users_requested.all())

    @property
    def roster_profiles(self):
        """Все участники команды (многокомандность через ProfileTeamMembership)."""
        return Profile.objects.filter(team_memberships__team=self).select_related('user').order_by('pk')

    def get_team_reg_number(self, game):
        regs = [reg for reg in Registration.objects.filter(game=game) if not reg.team.is_hidden]
        regs.sort(key=lambda r: r.time)
        team_number = None
        for i, reg in enumerate(regs):
            if self == reg.team:
                team_number = i
                break
        return team_number

    # Для шаблона общих результатов (команда vs личный участник)
    is_team_results_row = True


class TeamInvite(models.Model):
    """Bearer link which allows a visitor to join a team after confirmation."""

    team = models.ForeignKey(Team, related_name='invites', on_delete=models.CASCADE)
    token = models.CharField(max_length=128, unique=True, db_index=True)
    created_by = models.ForeignKey(
        User, related_name='created_team_invites', on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=('team', 'revoked_at'))]

    @property
    def is_active(self):
        return self.revoked_at is None


class HiddenAnonKey(models.Model):
    """anon_key, скрытый из общих результатов (аналог Team.is_hidden)."""

    anon_key = models.CharField(max_length=64, unique=True, db_index=True)
    note = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        verbose_name = 'Скрытый аноним'
        verbose_name_plural = 'Скрытые анонимы'

    def __str__(self):
        tail = self.anon_key[-4:] if len(self.anon_key) >= 4 else self.anon_key
        return 'Аноним ··{}'.format(tail)


def hidden_anon_keys():
    return set(HiddenAnonKey.objects.values_list('anon_key', flat=True))


def anon_key_is_hidden(anon_key):
    if not anon_key:
        return False
    return HiddenAnonKey.objects.filter(anon_key=str(anon_key)).exists()


class PersonalResultsParticipant:
    """
    Участник общей таблицы результатов без команды (личный или анонимный режим).
    Не модель БД — только для отображения и ключей в dict.
    """

    __slots__ = ('_user', 'user_id', 'anon_key', '_display_name_override')

    def __init__(self, *, user=None, user_id=None, anon_key=None, display_name=None):
        self._display_name_override = display_name
        if user is not None:
            self._user = user
            self.user_id = user.pk
            self.anon_key = None
            if user_id is not None and int(user_id) != int(user.pk):
                raise ValueError('user_id does not match user.pk')
        elif user_id is not None:
            self._user = None
            self.user_id = int(user_id)
            self.anon_key = None
        elif anon_key is not None and str(anon_key).strip() != '':
            self._user = None
            self.user_id = None
            self.anon_key = str(anon_key)
        else:
            raise ValueError('Pass user=, user_id=, or anon_key=')

    @property
    def pk(self):
        if self.user_id is not None:
            return 'personal:user:{}'.format(self.user_id)
        return 'personal:anon:{}'.format(self.anon_key)

    @property
    def visible_name(self):
        if self._display_name_override:
            return self._display_name_override
        if self._user is not None:
            u = self._user
            try:
                p = u.profile
            except Exception:
                p = None
            if p is not None:
                label = ('{} {}'.format(p.first_name or '', p.last_name or '')).strip()
                if label:
                    return label
            full = (u.get_full_name() or '').strip()
            if full:
                return full
            return u.get_username()
        if self.user_id is not None:
            # Projection/snapshot rows may carry only the user id.  Resolve it
            # lazily for the results template instead of treating it as an
            # anonymous participant (whose anon_key is None in this shape).
            user = User.objects.filter(pk=self.user_id).first()
            if user is not None:
                self._user = user
                try:
                    profile = user.profile
                except Exception:
                    profile = None
                if profile is not None:
                    label = ('{} {}'.format(profile.first_name or '', profile.last_name or '')).strip()
                    if label:
                        return label
                full = (user.get_full_name() or '').strip()
                return full or user.get_username()
            return 'Пользователь #{}'.format(self.user_id)
        tail = self.anon_key[-4:] if len(self.anon_key) >= 4 else self.anon_key
        return 'Аноним ··{}'.format(tail)

    is_team_results_row = False

    def __eq__(self, other):
        if not isinstance(other, PersonalResultsParticipant):
            return False
        return self.user_id == other.user_id and self.anon_key == other.anon_key

    def __hash__(self):
        if self.user_id is not None:
            return hash(('PersonalResultsParticipant', 'u', self.user_id))
        return hash(('PersonalResultsParticipant', 'a', self.anon_key))


class ProfileTeamMembership(models.Model):
    """Участие профиля в команде (полный состав). Активная команда для игр — Profile.team_on."""

    profile = models.ForeignKey('Profile', related_name='team_memberships', on_delete=models.CASCADE)
    team = models.ForeignKey(Team, related_name='member_links', on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('profile', 'team'), name='games_profileteammembership_uniq_profile_team'),
        ]

    def __str__(self):
        return '{} @ {}'.format(self.profile, self.team)


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', primary_key=True, on_delete=models.CASCADE)
    is_hidden = models.BooleanField(
        default=False,
        verbose_name='Скрыт из публичных результатов',
        help_text='Не удаляет результаты; скрывает профиль из публичных таблиц.',
    )
    first_name = models.TextField()
    last_name = models.TextField()
    avatar_url = models.TextField(blank=True, null=True)
    timezone = models.CharField(max_length=64, default='Europe/Moscow')
    vk_url = models.TextField(blank=True, null=True)
    email = models.TextField(blank=True, null=True)
    # Публичный Telegram без @. Пишется только из подтверждённого входа или
    # привязки ботом, не из текстового поля профиля.
    telegram_handle = models.CharField(max_length=64, blank=True, default='')
    # Подтвержденная через Telegram identity. telegram_handle никогда не
    # используется для платежного matching.
    telegram_user_id = models.BigIntegerField(blank=True, null=True, unique=True)
    # Telegram OIDC ``sub`` is a separate stable subject, not the Bot API user ID.
    # Keep it for account identity bookkeeping; Tribute matching uses telegram_user_id.
    telegram_oidc_sub = models.CharField(
        max_length=255, blank=True, null=True, unique=True, db_index=True,
    )
    telegram_username = models.CharField(max_length=64, blank=True, default='')
    telegram_verified = models.BooleanField(default=False)
    telegram_linked_at = models.DateTimeField(blank=True, null=True)
    team_on = models.ForeignKey(
        Team, related_name='primary_profiles', blank=True, null=True, on_delete=models.SET_NULL
    )
    team_requested = models.ForeignKey(Team, related_name='users_requested', blank=True, null=True, on_delete=models.SET_NULL)
    # При подаче заявки в команду: сделать принятую команду активной (учитывается в confirm).
    join_accept_as_primary = models.BooleanField(default=True)
    # Клубный upsell не должен надоедать чаще одного раза в две недели.
    club_archive_offer_last_shown_at = models.DateTimeField(blank=True, null=True)
    club_archive_offer_deferred_until = models.DateTimeField(blank=True, null=True)
    club_archive_offer_never = models.BooleanField(default=False)

    def __str__(self):
        return self.first_name + ' ' + self.last_name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.team_on_id:
            ProfileTeamMembership.objects.get_or_create(profile=self, team_id=self.team_on_id)

    def repair_primary_team(self):
        """Есть членства, но team_on пустой или не из списка — выставить детерминированно."""
        ids = list(ProfileTeamMembership.objects.filter(profile=self).values_list('team_id', flat=True))
        if not ids:
            if self.team_on_id is not None:
                Profile.objects.filter(pk=self.pk).update(team_on=None)
                self.team_on_id = None
            return
        id_set = frozenset(ids)
        if self.team_on_id is None or self.team_on_id not in id_set:
            chosen = sorted(id_set)[0]
            Profile.objects.filter(pk=self.pk).update(team_on_id=chosen)
            self.team_on_id = chosen

    def sync_membership_for_team_on(self):
        """Если задана активная команда — гарантировать строку членства (админка, миграции)."""
        if self.team_on_id:
            ProfileTeamMembership.objects.get_or_create(profile=self, team_id=self.team_on_id)

    def add_team_membership(self, team, *, make_primary=False):
        from django.db import transaction
        with transaction.atomic():
            ProfileTeamMembership.objects.get_or_create(profile=self, team=team)
            if make_primary or not self.team_on_id:
                Profile.objects.filter(pk=self.pk).update(team_on=team)
                self.team_on_id = team.pk

    def remove_team_membership(self, team):
        from django.db import transaction
        with transaction.atomic():
            ProfileTeamMembership.objects.filter(profile=self, team=team).delete()
            next_id = (
                ProfileTeamMembership.objects.filter(profile=self)
                .order_by('team_id')
                .values_list('team_id', flat=True)
                .first()
            )
            Profile.objects.filter(pk=self.pk).update(team_on_id=next_id)
            self.team_on_id = next_id

    def set_primary_team(self, team):
        if not ProfileTeamMembership.objects.filter(profile=self, team=team).exists():
            return False
        if self.team_on_id != team.pk:
            Profile.objects.filter(pk=self.pk).update(team_on=team)
            self.team_on_id = team.pk
        return True

    def other_member_teams(self):
        """Команды членства кроме активной (для UI «второстепенных»)."""
        if not self.team_on_id:
            return Team.objects.filter(member_links__profile=self).order_by('visible_name', 'name').distinct()
        return (
            Team.objects.filter(member_links__profile=self)
            .exclude(pk=self.team_on_id)
            .order_by('visible_name', 'name')
            .distinct()
        )


class TelegramLinkToken(models.Model):
    """Short-lived one-time proof used by /start to link a Telegram account."""

    user = models.ForeignKey('auth.User', related_name='telegram_link_tokens', on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(blank=True, null=True)
    # Relative path after a successful bot /start link (pay or subscription).
    next_path = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return 'Telegram link token #{} for user {}'.format(self.pk, self.user_id)


class Registration(models.Model):
    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(Team, related_name='registrations', blank=True, null=True, on_delete=models.SET_NULL)
    game = models.ForeignKey('Game', related_name='registrations', blank=True, null=True, on_delete=models.SET_NULL)
    time = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    with_referent = models.ForeignKey(Team, related_name='registrations_with_referent', blank=True, null=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [
            models.Index(fields=['game', 'team'], name='games_reg_game_team_idx'),
        ]

    def __str__(self):
        return '{} --- {} ({}){}'.format(
            self.game,
            self.team,
            self.time.strftime('%Y-%m-%d %H:%M:%S'),
            ' [with referent {}]'.format(self.with_referent) if self.with_referent is not None else ''
        )
