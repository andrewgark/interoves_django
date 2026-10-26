import importlib.util
from pathlib import Path

from django import forms
import inlineedit

# Import BasicAdaptor without loading inlineedit.adaptors.__init__, which resolves
# games.site.http.adaptors from settings while this module is still initializing.
_basic_path = Path(inlineedit.__file__).resolve().parent / 'adaptors' / 'basic.py'
_basic_spec = importlib.util.spec_from_file_location('inlineedit.adaptors.basic', _basic_path)
_basic_module = importlib.util.module_from_spec(_basic_spec)
_basic_spec.loader.exec_module(_basic_module)
BasicAdaptor = _basic_module.BasicAdaptor


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
