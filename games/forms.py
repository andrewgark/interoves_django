from django.forms import Form, ModelForm, ChoiceField, TextInput, HiddenInput, BooleanField
from games.models import *


class CreateTeamForm(ModelForm):    
    def __init__(self, project, *args, **kwargs):
        super(ModelForm, self).__init__(*args, **kwargs)
        self.fields['name'].widget = TextInput(attrs={'placeholder': 'Придумайте название вашей команды'})
        self.fields['name'].widget.attrs['size'] = 45

        self.fields['referer'].widget.attrs['style'] = 'width: 356px'
        self.fields['referer'].choices = [('', 'Выберите команду, которая вас пригласила')]
        for team in sorted(Team.objects.filter(project=project), key=lambda t: t.visible_name):
            if not team.is_hidden:
                self.fields['referer'].choices.append((team.name, team.visible_name))
        
        self.fields['project'].widget = HiddenInput()
        self.fields['project'].initial = project

    class Meta:
        model = Team
        fields = ['name', 'project', 'referer']


class JoinTeamForm(Form):
    def __init__(self, project, *args, **kwargs):
        super(JoinTeamForm, self).__init__(*args, **kwargs)
        self.fields['name'].choices = [('', 'Выберите команду')]
        for team in sorted(Team.objects.filter(project=project), key=lambda t: t.visible_name):
            if not team.is_hidden:
                self.fields['name'].choices.append((team.name, team.visible_name))

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


class TicketRequestForm(ModelForm):
    class Meta:
        model = TicketRequest
        fields = ['money', 'tickets', 'yookassa_id']
        
