from django.forms import Form, ModelForm, ChoiceField
from games.models import Team


class CreateTeamForm(ModelForm):
    class Meta:
        model = Team
        fields = ['name']


class JoinTeamForm(Form):
    def __init__(self, *args, **kwargs):
        super(JoinTeamForm, self).__init__(*args, **kwargs)
        self.fields['name'].choices = [(x.name, x.name) for x in sorted(Team.objects.all(), key=lambda t: t.name)]

    name = ChoiceField(
        choices=(),
        initial='Выберите команду',
        required=True
    )
