# Agent instructions (Interoves Django)

## Python virtual environment

Use this virtualenv for **every** Python command in this project (install deps, `manage.py`, tests, one-off scripts):

| Item | Path |
|------|------|
| Root | `../venv/interoves_django` (sibling of the repo: parent directory `venv/`) |
| Python | `../venv/interoves_django/bin/python` |
| pip | `../venv/interoves_django/bin/pip` |

From the repository root (`interoves_django/`):

```bash
source ../venv/interoves_django/bin/activate
python manage.py check
python manage.py migrate
python manage.py test
```

Or without activating:

```bash
../venv/interoves_django/bin/python manage.py test
```

**Do not** use bare `python3` / system `pip` for this project unless the user explicitly wants that. **Do not** create a new venv under this repo by default.

The same requirement is mirrored in `.cursor/rules/python-venv.mdc` (`alwaysApply: true`).

## Modals / popups (new UI)

For **new** pages under `static/templates/new/`, use **one** pattern: the `new-rules-modal` stack (see `static/css/new.css` and examples in `new/base.html`, `new/team.html`, `new/task_group.html`).

- Outer: `class="new-rules-modal"`, `role="dialog"`, `aria-modal="true"`, `hidden` by default; open with `classList.add('is-open')` and remove `hidden`, close the reverse; lock scroll on `document.body` while open.
- Backdrop: `new-rules-overlay` with a `data-*-close` handler (or equivalent) where needed.
- Content: `new-rules-modal__box pal-card`, close button `new-rules-modal__close`, **Escape** closes.
- Prefer unique `id` and `aria-labelledby` per dialog.

Do **not** add another modal library (e.g. Bootstrap modal, Magnific Popup) for new UI work; legacy templates may still use older stacks—keep new code consistent with `new-rules-modal` only.
