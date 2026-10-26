"""Main UI: hub, games folder, profile, team."""
import datetime
import logging

import pytz
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.forms import ModelForm, TextInput
from django.shortcuts import redirect

from games.models import (
    Profile,
)
from games.site.pages import _scoped_project_id

logger = logging.getLogger('games.views.new_ui')

class ProfileSettingsForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].widget.attrs.update({'placeholder': 'Имя'})
        self.fields['last_name'].widget.attrs.update({'placeholder': 'Фамилия'})
        # keep model field, but render as text input with datalist
        self.fields['timezone'].widget = TextInput()
        self.fields['timezone'].required = True
        self.fields['timezone'].widget.attrs.update({
            'list': 'tz-list',
            'placeholder': 'Europe/Moscow (UTC+03:00)',
            'autocomplete': 'off',
        })
        if getattr(self.instance, 'timezone', None):
            # show with UTC offset (datalist in Chrome uses option.value only)
            try:
                tz_name = self.instance.timezone
                tz_obj = pytz.timezone(tz_name)
                utc_now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
                off = utc_now.astimezone(tz_obj).utcoffset()
                if off is not None:
                    total = int(off.total_seconds())
                    sign = '+' if total >= 0 else '-'
                    total = abs(total)
                    hh = total // 3600
                    mm = (total % 3600) // 60
                    self.initial['timezone'] = '{} (UTC{}{:02d}:{:02d})'.format(tz_name, sign, hh, mm)
                else:
                    self.initial['timezone'] = tz_name
            except Exception:
                self.initial['timezone'] = self.instance.timezone

    class Meta:
        model = Profile
        fields = ['first_name', 'last_name', 'avatar_url', 'timezone']
        widgets = {
            'first_name': TextInput(),
            'last_name': TextInput(),
            'avatar_url': TextInput(),
        }

    def clean_timezone(self):
        tz = (self.cleaned_data.get('timezone') or '').strip()
        # Allow values like "Europe/Moscow (UTC+03:00)" (Chrome datalist shows only value).
        if ' (UTC' in tz and tz.endswith(')'):
            tz = tz.split(' (UTC', 1)[0].strip()
        if not tz:
            raise ValidationError('Выберите таймзону.')
        if tz not in pytz.common_timezones and tz not in pytz.all_timezones:
            raise ValidationError('Неизвестная таймзона.')
        return tz


def _profile_unavailable_redirect(request):
    messages.error(request, 'Профиль недоступен.')
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_hub', project_id=scoped)
    return redirect('new_hub')


__all__ = [
    'ProfileSettingsForm',
    '_profile_unavailable_redirect',
]
