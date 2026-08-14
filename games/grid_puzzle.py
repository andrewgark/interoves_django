"""Parsing and canonicalization for configurable rectangular grid puzzles."""

import json
import re


MIN_GRID_SIZE = 2
MAX_GRID_SIZE = 20
MAX_SUBMITTED_WALLS = MAX_GRID_SIZE * (MAX_GRID_SIZE - 1) * 2
EDGE_RE = re.compile(r'^(h|v):(\d+):(\d+)$')
CELL_OBJECT_VALUES = {
    'O', 'X', 'arrow-up', 'arrow-down', 'arrow-left', 'arrow-right', 'star',
}
SHADING_VALUES = {'B', 'G'}
GRID_CHECKER_IDS = {'grid-wall-checker', 'grid-shading-checker'}


class GridPuzzleDataError(ValueError):
    pass


def _load_object(raw, label):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '')
    except (TypeError, ValueError) as exc:
        raise GridPuzzleDataError('{} must be valid JSON'.format(label)) from exc
    if not isinstance(value, dict):
        raise GridPuzzleDataError('{} must be a JSON object'.format(label))
    return value


def validate_edge_id(edge_id, rows, cols):
    if not isinstance(edge_id, str):
        raise GridPuzzleDataError('wall IDs must be strings')
    match = EDGE_RE.fullmatch(edge_id)
    if not match:
        raise GridPuzzleDataError('invalid wall ID: {}'.format(edge_id))
    orientation, row_raw, col_raw = match.groups()
    row = int(row_raw)
    col = int(col_raw)
    if orientation == 'h':
        valid = 1 <= row < rows and 0 <= col < cols
    else:
        valid = 0 <= row < rows and 1 <= col < cols
    if not valid:
        raise GridPuzzleDataError('wall is outside the internal grid: {}'.format(edge_id))
    return '{}:{}:{}'.format(orientation, row, col)


def canonicalize_walls(walls, rows, cols, *, reject_duplicates=True):
    if not isinstance(walls, list):
        raise GridPuzzleDataError('walls must be a JSON array')
    if len(walls) > MAX_SUBMITTED_WALLS:
        raise GridPuzzleDataError('too many walls')
    canonical = [validate_edge_id(edge, rows, cols) for edge in walls]
    if reject_duplicates and len(set(canonical)) != len(canonical):
        raise GridPuzzleDataError('duplicate wall IDs are not allowed')
    return sorted(set(canonical))


def canonicalize_shading(shading, rows, cols):
    if not isinstance(shading, list) or len(shading) != rows:
        raise GridPuzzleDataError('shading must contain exactly one string per row')
    canonical = []
    for row in shading:
        if not isinstance(row, str) or len(row) != cols:
            raise GridPuzzleDataError('each shading row must contain exactly one value per cell')
        if any(value not in SHADING_VALUES for value in row):
            raise GridPuzzleDataError('shading may contain only B (black) and G (light-green)')
        canonical.append(row)
    return canonical


def parse_grid_puzzle_data(raw):
    data = _load_object(raw, 'checker_data')
    allowed = {
        'version', 'rows', 'cols', 'marks', 'solution_walls', 'solution_shading',
        'can_set_walls', 'can_set_path', 'can_set_shading',
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise GridPuzzleDataError('unknown fields: {}'.format(', '.join(unknown)))
    if data.get('version') != 1:
        raise GridPuzzleDataError('version must be 1')
    rows = data.get('rows')
    cols = data.get('cols')
    if isinstance(rows, bool) or not isinstance(rows, int):
        raise GridPuzzleDataError('rows must be an integer')
    if isinstance(cols, bool) or not isinstance(cols, int):
        raise GridPuzzleDataError('cols must be an integer')
    if not MIN_GRID_SIZE <= rows <= MAX_GRID_SIZE:
        raise GridPuzzleDataError('rows must be between 2 and 20')
    if not MIN_GRID_SIZE <= cols <= MAX_GRID_SIZE:
        raise GridPuzzleDataError('cols must be between 2 and 20')
    can_set_walls = data.get('can_set_walls', True)
    can_set_path = data.get('can_set_path', True)
    can_set_shading = data.get('can_set_shading', False)
    if not isinstance(can_set_walls, bool):
        raise GridPuzzleDataError('can_set_walls must be true or false')
    if not isinstance(can_set_path, bool):
        raise GridPuzzleDataError('can_set_path must be true or false')
    if not isinstance(can_set_shading, bool):
        raise GridPuzzleDataError('can_set_shading must be true or false')

    marks_raw = data.get('marks', [])
    if not isinstance(marks_raw, list):
        raise GridPuzzleDataError('marks must be a JSON array')
    marks = []
    seen_cells = set()
    for mark in marks_raw:
        if not isinstance(mark, dict) or set(mark) != {'row', 'col', 'value'}:
            raise GridPuzzleDataError('each mark must contain only row, col, and value')
        row = mark['row']
        col = mark['col']
        value = mark['value']
        if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row < rows:
            raise GridPuzzleDataError('mark row is outside the grid')
        if isinstance(col, bool) or not isinstance(col, int) or not 0 <= col < cols:
            raise GridPuzzleDataError('mark col is outside the grid')
        if value not in CELL_OBJECT_VALUES:
            raise GridPuzzleDataError(
                'mark value must be O, X, an orthogonal arrow, or star'
            )
        cell = (row, col)
        if cell in seen_cells:
            raise GridPuzzleDataError('a cell may contain only one mark')
        seen_cells.add(cell)
        marks.append({'row': row, 'col': col, 'value': value})

    solution_walls = None
    solution_shading = None
    if 'solution_walls' in data:
        solution_walls = canonicalize_walls(data['solution_walls'], rows, cols)
    if 'solution_shading' in data:
        solution_shading = canonicalize_shading(data['solution_shading'], rows, cols)
    if solution_walls is None and solution_shading is None:
        raise GridPuzzleDataError('solution_walls or solution_shading is required')
    return {
        'version': 1,
        'rows': rows,
        'cols': cols,
        'marks': marks,
        'solution_walls': solution_walls,
        'can_set_walls': can_set_walls,
        'can_set_path': can_set_path,
        'can_set_shading': can_set_shading,
        'solution_shading': solution_shading,
    }


def parse_grid_puzzle_attempt(raw, rows, cols, *, can_set_walls=True):
    payload = _load_object(raw, 'attempt')
    if set(payload) != {'walls'}:
        raise GridPuzzleDataError('attempt must contain only walls')
    walls = canonicalize_walls(payload['walls'], rows, cols)
    if walls and not can_set_walls:
        raise GridPuzzleDataError('wall editing is disabled for this puzzle')
    return {'walls': walls}


def parse_grid_shading_attempt(raw, rows, cols):
    payload = _load_object(raw, 'attempt')
    if set(payload) != {'shading'}:
        raise GridPuzzleDataError('attempt must contain only shading')
    return {'shading': canonicalize_shading(payload['shading'], rows, cols)}


def grid_checker_id(task):
    checker = getattr(task, 'checker', None)
    if checker is None:
        task_group = getattr(task, 'task_group', None)
        checker = getattr(task_group, 'checker', None)
    if checker is None:
        raise GridPuzzleDataError('grid-puzzle checker is required')
    checker_id = getattr(checker, 'id', None) or getattr(checker, 'pk', None)
    if checker_id not in GRID_CHECKER_IDS:
        raise GridPuzzleDataError(
            'grid-puzzle checker must be grid-wall-checker or grid-shading-checker'
        )
    return checker_id


def validate_grid_checker_data(parsed, checker_id):
    if checker_id not in GRID_CHECKER_IDS:
        raise GridPuzzleDataError(
            'grid-puzzle checker must be grid-wall-checker or grid-shading-checker'
        )
    if checker_id == 'grid-wall-checker':
        if parsed['solution_walls'] is None:
            raise GridPuzzleDataError('solution_walls is required for grid-wall-checker')
        if not parsed['can_set_walls']:
            raise GridPuzzleDataError('can_set_walls must be true for grid-wall-checker')
    if checker_id == 'grid-shading-checker':
        if parsed['solution_shading'] is None:
            raise GridPuzzleDataError('solution_shading is required for grid-shading-checker')
        if not parsed['can_set_shading']:
            raise GridPuzzleDataError('can_set_shading must be true for grid-shading-checker')


def public_grid_puzzle_context(task, *, reveal_solution=False, readonly=False):
    parsed = parse_grid_puzzle_data(task.checker_data)
    checker_id = grid_checker_id(task)
    validate_grid_checker_data(parsed, checker_id)
    result = {
        'version': parsed['version'],
        'rows': parsed['rows'],
        'cols': parsed['cols'],
        'marks': parsed['marks'],
        'task_id': task.pk,
        'revision': str(getattr(task, 'attempt_revision', '') or ''),
        'readonly': bool(readonly),
        'can_set_walls': parsed['can_set_walls'],
        'can_set_path': parsed['can_set_path'],
        'can_set_shading': parsed['can_set_shading'],
        'checker_id': checker_id,
    }
    if reveal_solution:
        if checker_id == 'grid-wall-checker':
            result['walls'] = parsed['solution_walls']
        else:
            result['shading'] = parsed['solution_shading']
    return result
