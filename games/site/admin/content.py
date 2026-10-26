from django import forms
from django.contrib import admin, messages
from django.db import models
from django.forms import ModelForm, Textarea
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from games.models import (
    AlphabettyDictSuggestion,
    BugReport,
    BugReportMessage,
    CensorlyReviewCase,
    CorporateGameOrder,
    NextGameVoteAdjustment,
    NextGameVoteCampaignState,
    NextGameVoteEvent,
    NextGameVoteGoalMapping,
    OrderGameClient,
    OrderGameReview,
    PendingAlphabettyDictSuggestion,
    PendingBugReport,
)
from games.ops.social.models import SocialQueuePost


class EuroAmountField(forms.DecimalField):
    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace('\xa0', '').replace(' ', '').replace(',', '.')
        return super().to_python(value)


class NextGameVoteAdjustmentForm(ModelForm):
    amount_eur = EuroAmountField(
        label='Дельта, €',
        max_digits=12,
        decimal_places=2,
        help_text='На сколько сдвинуть шкалу этого кандидата. Плюс добавляет, минус убавляет. Платежи Tribute не переписываются.',
    )

    class Meta:
        model = NextGameVoteAdjustment
        fields = ('candidate', 'comment')

    def save(self, commit=True):
        from games.club.next_game import eur_major_to_cents

        obj = super().save(commit=False)
        obj.amount_eur_cents = eur_major_to_cents(self.cleaned_data['amount_eur'])
        if commit:
            obj.save()
        return obj


class NextGameVoteScaleForm(forms.Form):
    redactle = EuroAmountField(label='Redactle, €', min_value=0, max_digits=12, decimal_places=2)
    cryptic = EuroAmountField(label='Криптик, €', min_value=0, max_digits=12, decimal_places=2)
    logic = EuroAmountField(label='Логические пазлы, €', min_value=0, max_digits=12, decimal_places=2)
    comment = forms.CharField(
        label='Почему меняем',
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Обязательно. Например: «в Tribute у Redactle €25, вебхук не дошёл».',
    )

    def cleaned_totals_eur_cents(self):
        from games.club.next_game import CANDIDATE_CRYPTIC, CANDIDATE_LOGIC, CANDIDATE_REDACTLE, eur_major_to_cents

        return {
            CANDIDATE_REDACTLE: eur_major_to_cents(self.cleaned_data['redactle']),
            CANDIDATE_CRYPTIC: eur_major_to_cents(self.cleaned_data['cryptic']),
            CANDIDATE_LOGIC: eur_major_to_cents(self.cleaned_data['logic']),
        }


@admin.register(NextGameVoteGoalMapping)
class NextGameVoteGoalMappingAdmin(admin.ModelAdmin):
    list_display = ('candidate', 'donation_request_id', 'web_url', 'updated_at')
    search_fields = ('donation_request_id', 'web_url', 'note')


@admin.register(NextGameVoteEvent)
class NextGameVoteEventAdmin(admin.ModelAdmin):
    list_display = (
        'received_at', 'event_name', 'candidate', 'result',
        'normalized_amount_eur_cents', 'original_currency', 'original_amount_minor',
        'include_in_scoreboard', 'excluded', 'donation_request_id',
    )
    list_filter = ('event_name', 'result', 'candidate', 'excluded', 'include_in_scoreboard', 'original_currency')
    search_fields = ('idempotency_key', 'donation_request_id', 'tribute_donation_id', 'telegram_user_id')
    raw_id_fields = ('excluded_by', 'matched_user')
    readonly_fields = (
        'idempotency_key', 'event_name', 'donation_request_id', 'tribute_donation_id',
        'candidate', 'original_amount_minor', 'original_currency', 'fx_rate_to_eur',
        'normalized_amount_eur_cents', 'donation_created_at', 'period',
        'telegram_user_id', 'trb_user_id', 'result', 'raw_payload', 'payload_excerpt',
        'matched_user', 'analytics_goal_queued_at', 'analytics_goal_sent_at', 'received_at',
        'excluded_by', 'excluded_at', 'include_in_scoreboard',
    )
    actions = ('exclude_from_scoreboard', 'remap_unmapped')
    date_hierarchy = 'received_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if obj.excluded:
            obj.include_in_scoreboard = False
            if obj.excluded_at is None:
                obj.excluded_at = timezone.now()
                obj.excluded_by = request.user
        super().save_model(request, obj, form, change)
        from games.club.next_game import campaign_state, refresh_snapshot

        if campaign_state().frozen_at is not None:
            refresh_snapshot()

    def exclude_from_scoreboard(self, request, queryset):
        from games.club.next_game import exclude_event

        count = 0
        for event in queryset:
            if event.excluded:
                continue
            exclude_event(event, user=request.user, reason='Excluded from Django admin')
            count += 1
        self.message_user(request, 'Исключено записей: {}'.format(count))
    exclude_from_scoreboard.short_description = 'Исключить из официального результата'

    def remap_unmapped(self, request, queryset):
        from games.club.next_game import remap_unmapped_events

        updated = remap_unmapped_events()
        self.message_user(request, 'Перепривязано событий: {}'.format(updated))
    remap_unmapped.short_description = 'Проставить кандидатов по donation_request_id'


@admin.register(NextGameVoteAdjustment)
class NextGameVoteAdjustmentAdmin(admin.ModelAdmin):
    form = NextGameVoteAdjustmentForm
    list_display = ('created_at', 'candidate', 'amount_eur_display', 'created_by', 'comment_excerpt')
    list_filter = ('candidate',)
    search_fields = ('comment',)
    raw_id_fields = ('created_by',)
    readonly_fields = ('created_at', 'amount_eur_cents')
    fields = ('candidate', 'amount_eur', 'comment', 'created_by', 'created_at', 'amount_eur_cents')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def amount_eur_display(self, obj):
        from games.club.next_game import format_eur_cents

        return format_eur_cents(obj.amount_eur_cents)
    amount_eur_display.short_description = 'Сумма'

    def comment_excerpt(self, obj):
        text = (obj.comment or '').strip()
        if len(text) <= 72:
            return text
        return text[:69] + '…'
    comment_excerpt.short_description = 'Комментарий'

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        from games.club.next_game import campaign_state, refresh_snapshot

        if campaign_state().frozen_at is not None:
            refresh_snapshot()

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context['scale_url'] = reverse(
            'admin:games_nextgamevotecampaignstate_change',
            args=[1],
        )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(NextGameVoteCampaignState)
class NextGameVoteCampaignStateAdmin(admin.ModelAdmin):
    change_form_template = 'admin/games/nextgamevotecampaignstate/change_form.html'
    list_display = ('frozen_at', 'freeze_reason', 'winner_slug', 'is_tie', 'mapping_blocker', 'updated_at')
    readonly_fields = (
        'frozen_at', 'frozen_by', 'freeze_reason', 'snapshot',
        'winner_slug', 'is_tie', 'mapping_blocker', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from games.club.next_game import CAMPAIGN_STATE_PK, campaign_state

        campaign_state()
        return HttpResponseRedirect(
            reverse('admin:games_nextgamevotecampaignstate_change', args=[CAMPAIGN_STATE_PK])
        )

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        from games.club.next_game import (
            CAMPAIGN_STATE_PK,
            CANDIDATE_SLUGS,
            VotePayloadError,
            campaign_state,
            cents_to_eur_major,
            freeze_manually,
            score_breakdown,
            set_candidate_totals,
        )

        campaign_state()
        if str(object_id) != str(CAMPAIGN_STATE_PK):
            return HttpResponseRedirect(
                reverse('admin:games_nextgamevotecampaignstate_change', args=[CAMPAIGN_STATE_PK])
            )
        if not self.has_change_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        breakdown = score_breakdown()
        if request.method == 'POST' and request.POST.get('operation') == 'freeze':
            freeze_manually(user=request.user)
            self.message_user(request, 'Голосование зафиксировано вручную.')
            return HttpResponseRedirect(request.path)
        if request.method == 'POST':
            form = NextGameVoteScaleForm(request.POST)
            if form.is_valid():
                try:
                    created = set_candidate_totals(
                        totals_eur_cents=form.cleaned_totals_eur_cents(),
                        comment=form.cleaned_data['comment'],
                        user=request.user,
                    )
                except VotePayloadError as exc:
                    form.add_error(None, str(exc))
                else:
                    if created:
                        self.message_user(
                            request,
                            'Шкала обновлена. Записано корректировок: {}.'.format(len(created)),
                        )
                    else:
                        self.message_user(request, 'Суммы уже такие — ничего не изменилось.')
                    return HttpResponseRedirect(request.path)
        else:
            initial = {slug: cents_to_eur_major(breakdown['live'][slug]) for slug in CANDIDATE_SLUGS}
            form = NextGameVoteScaleForm(initial=initial)

        context = {
            **self.admin_site.each_context(request),
            **(extra_context or {}),
            'opts': self.model._meta,
            'original': campaign_state(),
            'title': 'Шкала голосования за следующую игру',
            'form': form,
            'breakdown': breakdown['rows'],
            'state': campaign_state(),
            'adjustments_url': reverse('admin:games_nextgamevoteadjustment_changelist'),
            'has_view_permission': self.has_view_permission(request),
            'has_change_permission': self.has_change_permission(request),
        }
        return TemplateResponse(
            request,
            self.change_form_template,
            context,
        )


@admin.register(CorporateGameOrder)
class CorporateGameOrderAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'company_name', 'contact_name', 'contact_method', 'contact_value', 'email_sent')
    list_filter = ('email_sent', 'contact_method')
    search_fields = ('company_name', 'contact_name', 'contact_value', 'contact_other_label', 'message')
    readonly_fields = ('created_at', 'email_sent')


@admin.register(OrderGameClient)
class OrderGameClientAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'sort_order', 'is_published')
    list_editable = ('sort_order', 'is_published')
    search_fields = ('company_name',)


@admin.register(OrderGameReview)
class OrderGameReviewAdmin(admin.ModelAdmin):
    list_display = ('name', 'caption', 'is_important', 'is_published')
    list_filter = ('is_important', 'is_published')
    list_editable = ('is_important', 'is_published')
    search_fields = ('name', 'caption', 'text')


def mark_bug_report_reviewed(modeladmin, request, queryset):
    queryset.update(status=BugReport.STATUS_REVIEWED)


def mark_bug_report_fixed(modeladmin, request, queryset):
    queryset.update(status=BugReport.STATUS_FIXED)


def mark_bug_report_dismissed(modeladmin, request, queryset):
    queryset.update(status=BugReport.STATUS_DISMISSED)


mark_bug_report_reviewed.short_description = 'Mark Reviewed'
mark_bug_report_fixed.short_description = 'Mark Fixed'
mark_bug_report_dismissed.short_description = 'Mark Dismissed'


def approve_alphabetty_dict_suggestions(modeladmin, request, queryset):
    from games.puzzles.alphabetty.suggestions import approve_suggestions

    n = approve_suggestions(queryset)
    modeladmin.message_user(
        request,
        'Одобрено слов: {}'.format(n),
        level=messages.SUCCESS,
    )


def approve_alphabetty_dict_suggestions_for_answer(modeladmin, request, queryset):
    from games.puzzles.alphabetty.suggestions import approve_suggestions_for_answer

    n = approve_suggestions_for_answer(queryset)
    modeladmin.message_user(
        request,
        'Одобрено для загадывания: {}'.format(n),
        level=messages.SUCCESS,
    )


def reject_alphabetty_dict_suggestions(modeladmin, request, queryset):
    from games.puzzles.alphabetty.suggestions import reject_suggestions

    n = reject_suggestions(queryset)
    modeladmin.message_user(
        request,
        'Отклонено слов: {}'.format(n),
        level=messages.SUCCESS,
    )


approve_alphabetty_dict_suggestions.short_description = 'Одобрить (добавить в словарь)'
approve_alphabetty_dict_suggestions_for_answer.short_description = 'Одобрить для загадывания'
reject_alphabetty_dict_suggestions.short_description = 'Отклонить'


class AlphabettyDictSuggestionAdminBase(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 60})},
    }
    raw_id_fields = ['user']
    readonly_fields = [
        'word', 'suggest_count', 'user', 'anon_key', 'created_at', 'updated_at', 'reviewed_at',
    ]
    fields = [
        'status',
        'word',
        'suggest_count',
        'admin_notes',
        'user',
        'anon_key',
        'created_at',
        'updated_at',
        'reviewed_at',
    ]
    list_display = ['word', 'status', 'suggest_count', 'user', 'updated_at', 'created_at']
    list_filter = ['status']
    search_fields = ['word', 'admin_notes', 'anon_key']
    actions = [
        approve_alphabetty_dict_suggestions,
        approve_alphabetty_dict_suggestions_for_answer,
        reject_alphabetty_dict_suggestions,
    ]
    ordering = ['-updated_at']

    def save_model(self, request, obj, form, change):
        from django.utils import timezone

        from games.puzzles.alphabetty.dicts import invalidate_dict_caches

        if change and 'status' in form.changed_data:
            if obj.status in (
                AlphabettyDictSuggestion.STATUS_APPROVED,
                AlphabettyDictSuggestion.STATUS_APPROVED_ANSWER,
                AlphabettyDictSuggestion.STATUS_REJECTED,
            ):
                obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)
        invalidate_dict_caches()


@admin.register(AlphabettyDictSuggestion)
class AlphabettyDictSuggestionAdmin(AlphabettyDictSuggestionAdminBase):
    pass


@admin.register(PendingAlphabettyDictSuggestion)
class PendingAlphabettyDictSuggestionAdmin(AlphabettyDictSuggestionAdminBase):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(status=AlphabettyDictSuggestion.STATUS_PENDING)


class BugReportMessageInline(admin.TabularInline):
    model = BugReportMessage
    extra = 1
    fields = ['author_role', 'author_user', 'text', 'created_at']
    readonly_fields = ['created_at']
    raw_id_fields = ['author_user']


class BugReportAdminBase(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 4, 'cols': 60})},
    }
    raw_id_fields = ['task', 'game', 'team', 'user']
    readonly_fields = [
        'time', 'page_url', 'anon_key', 'report_type', 'metadata',
        'censorly_review_case', 'context_links',
    ]
    fields = [
        'report_type',
        'status',
        'text',
        'metadata',
        'censorly_review_case',
        'admin_notes',
        'context_links',
        'task',
        'game',
        'team',
        'user',
        'anon_key',
        'page_url',
        'time',
    ]
    list_display = ['__str__', 'report_type', 'game', 'task', 'team', 'user', 'status', 'time']
    list_filter = ['status']
    search_fields = ['text', 'task__number', 'game__id', 'game__name']
    actions = [mark_bug_report_reviewed, mark_bug_report_fixed, mark_bug_report_dismissed]
    inlines = [BugReportMessageInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('task', 'task__task_group', 'game', 'game__project', 'team', 'user')

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, BugReportMessage) and not obj.pk:
                if not obj.author_user_id:
                    obj.author_user = request.user
                if not obj.author_role:
                    obj.author_role = BugReportMessage.ROLE_ADMIN
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

    def context_links(self, obj):
        from games.ops.telegram.links import (
            game_admin_url,
            game_site_url,
            task_admin_url,
            task_group_admin_url,
            task_play_url,
        )

        if obj is None or not obj.pk:
            return '—'
        rows = []
        if obj.game_id:
            rows.append(('Игра на сайте', game_site_url(obj.game)))
            rows.append(('Игра в админке', game_admin_url(obj.game)))
        if obj.task_id:
            rows.append(('Задание на сайте', task_play_url(obj.game, obj.task)))
            rows.append(('Task в админке', task_admin_url(obj.task)))
            if obj.task.task_group_id:
                rows.append(('TaskGroup в админке', task_group_admin_url(obj.task.task_group)))
        if obj.page_url:
            rows.append(('page_url', obj.page_url))
        rows.append(('Тред пользователя', '/profile/reports/{}/'.format(obj.pk)))
        if not rows:
            return '—'
        return format_html(
            '<ul style="margin:0;padding-left:1.2em">{}</ul>',
            format_html_join(
                '',
                '<li><a href="{}" target="_blank" rel="noopener">{}</a></li>',
                ((url, label) for label, url in rows),
            ),
        )

    context_links.short_description = 'Ссылки'


@admin.register(BugReport)
class BugReportAdmin(BugReportAdminBase):
    pass


@admin.register(CensorlyReviewCase)
class CensorlyReviewCaseAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'guess', 'target', 'expected', 'actual', 'status',
        'report_count', 'updated_at',
    ]
    list_filter = ['expected', 'status', 'fix_layer', 'classification']
    search_fields = ['guess', 'target', 'pair_key', 'note']
    readonly_fields = ['pair_key', 'created_at', 'updated_at', 'report_count']
    fields = [
        'pair_key', 'guess', 'target', 'expected', 'actual',
        'classification', 'fix_layer', 'status', 'decision', 'note',
        'report_count', 'created_at', 'updated_at',
    ]


@admin.register(PendingBugReport)
class PendingBugReportAdmin(BugReportAdminBase):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(status='Pending')


@admin.register(SocialQueuePost)
class SocialQueuePostAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'source',
        'ladder_number',
        'caption_short',
        'telegram_status',
        'twitter_status',
        'instagram_status',
        'threads_status',
        'telegram_queued_for',
        'twitter_queued_for',
        'instagram_queued_for',
        'threads_queued_for',
        'telegram_scheduled_for',
        'created_at',
    ]
    list_filter = [
        'source',
        'telegram_status',
        'twitter_status',
        'instagram_status',
        'threads_status',
    ]
    search_fields = ['caption', 'ladder_number', 'play_url', 'telegram_external_id', 'twitter_external_id']
    readonly_fields = [
        'created_at',
        'updated_at',
        'telegram_claimed_at',
        'telegram_claim_token',
    ]
    date_hierarchy = 'created_at'

    def caption_short(self, obj):
        text = (obj.caption or '').replace('\n', ' ')
        return text[:60] + ('…' if len(text) > 60 else '')

    caption_short.short_description = 'caption'
