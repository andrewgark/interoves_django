from django.contrib import admin
from games.models import (
    AccountMerge,
    AnonAccountClaim,
)


@admin.register(AccountMerge)
class AccountMergeAdmin(admin.ModelAdmin):
    list_display = [
        'created_at', 'source_user_id_snapshot', 'target_user_id_snapshot', 'provider',
    ]
    search_fields = [
        'source_user_id_snapshot', 'target_user_id_snapshot',
        'source_user__username', 'target_user__username', 'provider_uid',
    ]
    raw_id_fields = ['source_user', 'target_user']
    readonly_fields = [
        'source_user', 'target_user', 'source_user_id_snapshot',
        'target_user_id_snapshot', 'provider', 'provider_uid', 'summary', 'created_at',
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AnonAccountClaim)
class AnonAccountClaimAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'anon_key', 'user']
    search_fields = ['anon_key', 'user__username', 'user__email']
    raw_id_fields = ['user']
    readonly_fields = ['anon_key', 'user', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
