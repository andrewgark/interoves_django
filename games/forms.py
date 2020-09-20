from django.forms import Form, ModelForm, ChoiceField, TextInput, HiddenInput, BooleanField
from games.models import *


class CreateTeamForm(ModelForm):    
    def __init__(self, project, *args, **kwargs):
        super(ModelForm, self).__init__(*args, **kwargs)
        self.fields['name'].widget = TextInput(attrs={'placeholder': 'Название команды'})
        self.fields['project'].widget = HiddenInput()
        self.fields['project'].initial = project

    class Meta:
        model = Team
        fields = ['name', 'project']


class JoinTeamForm(Form):
    def __init__(self, project, *args, **kwargs):
        super(JoinTeamForm, self).__init__(*args, **kwargs)
        self.fields['name'].choices = [('', 'Выберите команду')]
        for team in sorted(Team.objects.filter(project=project), key=lambda t: t.name):
            if not team.is_hidden:
                self.fields['name'].choices.append((team.name, team.name))

    name = ChoiceField(
        choices=(),
        initial='Выберите команду',
        required=True
    )


class AttemptForm(ModelForm):
    def __init__(self, *args, **kwargs):
        attrs = {
            'placeholder': kwargs.get('placeholder', 'Ответ')
        }
        if kwargs.get('placeholder'):
            del kwargs['placeholder']

        if kwargs.get('style'):
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


class TicketRequestForm(ModelForm):
    class Meta:
        model = TicketRequest
        fields = ['money', 'tickets']
        
