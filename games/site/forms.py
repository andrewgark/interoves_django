from django import forms
from django.core.validators import validate_email
from django.forms import ModelForm, RadioSelect, TextInput

from games.models import Attempt, CorporateGameOrder


class AttemptForm(ModelForm):
    def __init__(self, *args, **kwargs):
        attrs = {
            'placeholder': kwargs.get('placeholder', 'Ответ')
        }
        if 'placeholder' in kwargs:
            del kwargs['placeholder']
        if 'style' in kwargs:
            attrs['style'] = kwargs['style']
            del kwargs['style']

        if not kwargs.get('field_text_width'):
            kwargs['field_text_width'] = 25
        attrs['size'] = kwargs['field_text_width']
        del kwargs['field_text_width']

        super(ModelForm, self).__init__(*args, **kwargs)
        self.fields['text'].widget = TextInput(attrs=attrs)

    class Meta:
        model = Attempt
        fields = ['text']


class CorporateGameOrderForm(ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput, label='')

    class Meta:
        model = CorporateGameOrder
        fields = [
            'company_name',
            'contact_name',
            'contact_method',
            'contact_value',
            'contact_other_label',
            'team_size',
            'preferred_date',
            'message',
        ]
        widgets = {
            'company_name': TextInput(attrs={'placeholder': 'Название компании'}),
            'contact_name': TextInput(attrs={'placeholder': 'Ваше имя'}),
            'contact_method': RadioSelect(),
            'contact_value': TextInput(attrs={
                'placeholder': '@username',
                'autocomplete': 'username',
            }),
            'contact_other_label': TextInput(attrs={'placeholder': 'WhatsApp, VK, телефон…'}),
            'team_size': TextInput(attrs={'placeholder': 'Например, 6–20 человек'}),
            'preferred_date': TextInput(attrs={'placeholder': 'Желаемая дата или период'}),
            'message': forms.Textarea(attrs={'placeholder': 'Тема, формат, особые пожелания…', 'rows': 4}),
        }
        labels = {
            'company_name': 'Компания',
            'contact_name': 'Контактное лицо',
            'contact_method': 'Способ связи',
            'contact_value': 'Контакт',
            'contact_other_label': 'Тип контакта',
            'team_size': 'Размер команды',
            'preferred_date': 'Когда провести',
            'message': 'Комментарий',
        }

    def clean(self):
        cleaned_data = super().clean()
        method = cleaned_data.get('contact_method')
        value = (cleaned_data.get('contact_value') or '').strip()
        other_label = (cleaned_data.get('contact_other_label') or '').strip()

        if not value:
            self.add_error('contact_value', 'Укажите, как с вами связаться.')

        if method == CorporateGameOrder.ContactMethod.EMAIL:
            try:
                validate_email(value)
            except forms.ValidationError:
                self.add_error('contact_value', 'Введите корректный email.')

        if method == CorporateGameOrder.ContactMethod.OTHER and not other_label:
            self.add_error('contact_other_label', 'Укажите тип контакта.')

        cleaned_data['contact_value'] = value
        cleaned_data['contact_other_label'] = other_label
        return cleaned_data

    def clean_website(self):
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('Spam detected.')
        return ''
