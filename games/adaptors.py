from django import forms

from inlineedit.adaptors import BasicAdaptor


class PersonAdaptor(BasicAdaptor):
    def has_edit_perm(self, user):
        if user.profile != self._model:
            return False
        return self._field.name in [
            'first_name',
            'last_name'
        ]

    def form_field(self):
            f = self._field.formfield()
            f.widget = forms.TextInput(attrs={'size': '10'})
            return f
