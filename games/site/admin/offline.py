from django.contrib import admin

from games.models import OfflinePlayRule


@admin.register(OfflinePlayRule)
class OfflinePlayRuleAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'enabled', 'game', 'task_type', 'mode', 'instant_verdict',
        'offline_attempts', 'offline_timing', 'offline_hints', 'rollout_percent', 'updated_at',
    )
    list_filter = ('enabled', 'game', 'task_type', 'mode')
    autocomplete_fields = ('game',)
    readonly_fields = ('updated_at',)
    fieldsets = (
        ('Где действует', {
            'description': 'Пустые поля подходят ко всему. Из подходящих включённых правил '
                           'выигрывает самое точное: игра, затем тип задания, затем режим. '
                           'Ничего не работает, пока не включён OFFLINE_PLAY_ENABLED; '
                           'командная игра всегда онлайн.',
            'fields': ('enabled', 'game', 'task_type', 'mode', 'rollout_percent'),
        }),
        ('Что включено', {
            'fields': ('instant_verdict', 'offline_attempts', 'offline_timing', 'offline_hints'),
        }),
        (None, {'fields': ('note', 'updated_at')}),
    )
