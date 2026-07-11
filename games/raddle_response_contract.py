# Контракт ответов /send_attempt/ для task_type=raddle (авто-режим new UI).
# Используется в тестах и в agents/raddle.md — при изменении handler'ов обновлять оба.

RADDLE_UI_PRINCIPLE = (
    'UI может перерисовывать задание и переносить фокус только когда сервер '
    'явно сигнализирует продвижение состояния (raddle_correct, raddle_needs_sync, '
    'или duplicate с raddle_duplicate_solved).'
)

# Матрица: ответ сервера → ожидаемое поведение клиента (applyRaddleAttemptResponse).
RADDLE_RESPONSE_SCENARIOS = (
    {
        'id': 'correct',
        'description': 'Верное слово в этой попытке',
        'response': {
            'status': 'ok',
            'raddle_correct': True,
        },
        'ui': {
            'replace_html': True,
            'advance_focus': True,
            'mark_wrong': False,
            'keep_input': False,
        },
    },
    {
        'id': 'wrong',
        'description': 'Неверное слово',
        'response': {
            'status': 'ok',
            'raddle_correct': False,
            'raddle_needs_sync': False,
        },
        'ui': {
            'replace_html': False,
            'advance_focus': False,
            'mark_wrong': True,
            'keep_input': True,
        },
    },
    {
        'id': 'needs_sync',
        'description': 'Повтор по уже решённому слову (лаг сети)',
        'response': {
            'status': 'ok',
            'raddle_correct': False,
            'raddle_needs_sync': True,
        },
        'ui': {
            'replace_html': True,
            'advance_focus': True,
            'mark_wrong': False,
            'keep_input': False,
        },
    },
    {
        'id': 'duplicate_unsolved',
        'description': 'Дубликат неверной посылки (двойной fire / повтор Enter)',
        'response': {
            'status': 'duplicate',
            'raddle_duplicate_solved': False,
        },
        'ui': {
            'replace_html': False,
            'advance_focus': False,
            'mark_wrong': True,
            'keep_input': True,
            'show_message': 'duplicate',
        },
    },
    {
        'id': 'duplicate_solved',
        'description': 'Дубликат после успешной посылки (лаг сети)',
        'response': {
            'status': 'duplicate',
            'raddle_duplicate_solved': True,
        },
        'ui': {
            'replace_html': True,
            'advance_focus': True,
            'mark_wrong': False,
            'keep_input': False,
        },
    },
)

RADDLE_PR_CHECKLIST = (
    'Есть ли вызов applyNewUiTaskHtml без raddle_correct / raddle_needs_sync / '
    'raddle_duplicate_solved?',
    'Сбрасывается ли raddleLast при catch и когда postRaddleAutoForm вернул false?',
    'Может ли один жест (paste + input) отправить два одинаковых запроса?',
    'При duplicate без raddle_duplicate_solved фокус остаётся на текущей строке?',
)
