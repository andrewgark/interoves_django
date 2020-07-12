from django.forms import Form, ModelForm, ChoiceField
from games.models import Team


class CreateTeamForm(ModelForm):
    def __init__(self, *args, **kwargs): 
        super(ModelForm, self).__init__(*args, **kwargs)
        self.fields['name'].label = 'Название'

    class Meta:
        model = Team
        fields = ['name']


class JoinTeamForm(Form):
    def __init__(self, *args, **kwargs):
        super(JoinTeamForm, self).__init__(*args, **kwargs)
        self.fields['name'].choices = [('', 'Выберите команду')]
        for x in sorted(Team.objects.all(), key=lambda t: t.name):
            self.fields['name'].choices.append((x.name, x.name))

    name = ChoiceField(
        choices=(),
        initial='Выберите команду',
        required=True,
        label='Название'
    )
