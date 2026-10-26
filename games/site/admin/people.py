from django.contrib import admin
from games.models import (
    HiddenAnonKey,
    Profile,
    ProfileTeamMembership,
    Registration,
    Team,
    TelegramLinkToken,
)


@admin.register(TelegramLinkToken)
class TelegramLinkTokenAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'expires_at', 'used_at')
    list_filter = ('used_at',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'token_hash', 'created_at', 'expires_at', 'used_at', 'next_path')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = [
        'visible_name', 'project', 'ticket_price', 'ticket_price_amd',
        'get_n_users_on', 'get_n_users_requested', 'is_tester', 'is_hidden',
    ]
    search_fields = ['name', 'visible_name']
    raw_id_fields = ['referer']


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ['game', 'team', 'time', 'with_referent']
    search_fields = ['team__name', 'team__visible_name', 'game__id', 'game__name']
    raw_id_fields = ['team', 'game', 'with_referent']
    date_hierarchy = 'time'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('team', 'game', 'with_referent')


@admin.register(HiddenAnonKey)
class HiddenAnonKeyAdmin(admin.ModelAdmin):
    list_display = ['anon_key', 'note']
    search_fields = ['anon_key', 'note']


def confirm_profile_team_request(modeladmin, request, queryset):
    for profile in queryset:
        if not profile.team_requested:
            continue
        team = profile.team_requested
        mk_primary = profile.join_accept_as_primary
        profile.team_requested = None
        profile.join_accept_as_primary = True
        profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
        profile.add_team_membership(team, make_primary=mk_primary)


def clear_profile_team(modeladmin, request, queryset):
    for profile in queryset:
        ProfileTeamMembership.objects.filter(profile=profile).delete()
        profile.team_on = None
        profile.team_requested = None
        profile.join_accept_as_primary = True
        profile.save()


confirm_profile_team_request.short_description = "Confirm Team Request"
clear_profile_team.short_description = "Clear Profile Team"


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = [
        '__str__', 'is_hidden', 'team_on', 'team_requested', 'telegram_handle', 'telegram_user_id',
        'telegram_username', 'telegram_verified', 'telegram_linked_at', 'vk_url',
    ]
    list_filter = ['telegram_verified', 'is_hidden']
    search_fields = ['user__username', 'telegram_handle', 'telegram_user_id', 'telegram_username']
    raw_id_fields = ['user', 'team_on', 'team_requested']
    actions = [confirm_profile_team_request, clear_profile_team]
