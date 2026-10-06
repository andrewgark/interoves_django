import json
import re

from django.template.loader import render_to_string

from games.util import clean_text


TOKEN_RE = re.compile(r'\{\{\s*html_form:([A-Za-z0-9_.:-]+)\s*\}\}')


def normalize_html_form_answer(value):
    value = clean_text(value)
    value = re.sub(r"[.,\/#!?$%\^&\*;:{}=\"\-_`~()—–]+", "", value)
    value = re.sub(r"[^\S\r\n]+", "", value)
    return value


def parse_html_forms_data(raw):
    try:
        payload = json.loads(raw or '{}')
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get('forms') or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    forms = []
    seen = set()
    for index, item in enumerate(items):
        if isinstance(item, str):
            item = {'answer': item}
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or index + 1).strip()
        if not key or key in seen:
            continue
        answers = item.get('answers')
        if answers is None:
            answers = item.get('answer')
        if isinstance(answers, str):
            answers = answers.splitlines()
        if not isinstance(answers, list):
            answers = []
        answers = [str(answer).strip() for answer in answers if str(answer).strip()]
        if not answers:
            continue
        seen.add(key)
        forms.append({
            'key': key,
            'index': len(forms) + 1,
            'label': str(item.get('label') or key),
            'placeholder': str(item.get('placeholder') or 'Ответ'),
            'answers': answers,
            'answer': answers[0],
        })
    return forms


def html_forms_state_payload(solved_keys, forms):
    ordered_keys = [form['key'] for form in forms]
    solved = [key for key in ordered_keys if key in solved_keys]
    return {
        'solved_keys': solved,
        'total': len(solved),
        'n_forms': len(ordered_keys),
    }


def html_forms_solved_keys_from_state(state_raw):
    if not state_raw:
        return set()
    try:
        payload = json.loads(state_raw)
    except (TypeError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()
    solved_keys = payload.get('solved_keys') or []
    if not isinstance(solved_keys, list):
        return set()
    return {str(key) for key in solved_keys}


def html_forms_answer_matches(user_answer, accepted_answers):
    normalized = normalize_html_form_answer(user_answer)
    return any(
        normalized == normalize_html_form_answer(answer)
        for answer in accepted_answers
    )


def html_forms_is_complete(task, state_raw):
    forms = parse_html_forms_data(getattr(task, 'checker_data', None))
    if not forms:
        return False
    solved = html_forms_solved_keys_from_state(state_raw)
    return all(form['key'] in solved for form in forms)


def render_html_forms_task(request, task, attempts_info, gameplay_context_token, *, game=None, new_ui=False):
    forms = parse_html_forms_data(task.checker_data)
    solved = set()
    if attempts_info and attempts_info.best_attempt and attempts_info.best_attempt.state:
        solved = html_forms_solved_keys_from_state(attempts_info.best_attempt.state)

    by_key = {form['key']: form for form in forms}
    rendered_keys = set()

    def render_form(form):
        rendered_keys.add(form['key'])
        return render_to_string(
            'task-content/html-form-answer.html',
            {
                'task': task,
                'form_item': form,
                'solved': form['key'] in solved,
                'game': game,
                'gameplay_context_token': gameplay_context_token,
                'new_ui': new_ui,
            },
            request=request,
        )

    def replace_token(match):
        key = match.group(1)
        form = by_key.get(key)
        if form is None:
            return match.group(0)
        return render_form(form)

    html = TOKEN_RE.sub(replace_token, task.text or '')
    missing = [form for form in forms if form['key'] not in rendered_keys]
    if missing:
        html += ''.join(render_form(form) for form in missing)
    return html
