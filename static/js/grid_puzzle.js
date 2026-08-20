(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) {
    root.GridPuzzle = api;
    root.initGridPuzzle = api.initAll;
  }
})(typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : this), function (root) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var CELL = 48;
  var HISTORY_LIMIT = 100;

  function sortedValues(set) { return Array.from(set || []).sort(); }

  function parseEdge(edge) {
    var match = /^(h|v):(\d+):(\d+)$/.exec(String(edge || ''));
    return match ? { type: match[1], row: Number(match[2]), col: Number(match[3]) } : null;
  }

  function validEdge(edge, rows, cols) {
    var p = parseEdge(edge);
    if (!p) return false;
    if (p.type === 'h') return p.row >= 1 && p.row < rows && p.col >= 0 && p.col < cols;
    return p.row >= 0 && p.row < rows && p.col >= 1 && p.col < cols;
  }

  function edgeBetweenCells(a, b) {
    if (!a || !b) return null;
    var dr = b.row - a.row;
    var dc = b.col - a.col;
    if (Math.abs(dr) + Math.abs(dc) !== 1) return null;
    if (dr === 1) return 'h:' + b.row + ':' + a.col;
    if (dr === -1) return 'h:' + a.row + ':' + a.col;
    if (dc === 1) return 'v:' + a.row + ':' + b.col;
    return 'v:' + a.row + ':' + a.col;
  }

  function snapshot(state) {
    return {
      walls: sortedValues(state.walls),
      notes: sortedValues(state.notes),
      shading: Object.keys(state.shading || {}).sort().map(function (cell) {
        return [cell, state.shading[cell]];
      }),
      notesVisible: state.notesVisible !== false,
    };
  }

  function shadingFromRows(rows, rowCount, colCount) {
    var result = {};
    if (!Array.isArray(rows) || rows.length !== rowCount) return result;
    rows.forEach(function (row, r) {
      if (typeof row !== 'string' || row.length !== colCount) return;
      row.split('').forEach(function (value, c) {
        if (value === 'B' || value === 'G') result[r + ':' + c] = value;
      });
    });
    return result;
  }

  function shadingRows(shading, rows, cols) {
    var result = [];
    for (var r = 0; r < rows; r += 1) {
      var row = '';
      for (var c = 0; c < cols; c += 1) row += shading[r + ':' + c] || 'W';
      result.push(row);
    }
    return result;
  }

  function snapshotsEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function svgEl(name, attrs) {
    var el = root.document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === 'className') el.setAttribute('class', attrs[key]);
      else el.setAttribute(key, String(attrs[key]));
    });
    return el;
  }

  function clear(el) {
    while (el && el.firstChild) el.removeChild(el.firstChild);
  }

  function starPoints(cx, cy) {
    var points = [];
    for (var i = 0; i < 10; i += 1) {
      var radius = i % 2 === 0 ? 13 : 5.5;
      var angle = (-90 + i * 36) * Math.PI / 180;
      points.push((cx + Math.cos(angle) * radius) + ',' + (cy + Math.sin(angle) * radius));
    }
    return points.join(' ');
  }

  function Controller(container, config) {
    this.container = container;
    this.config = config;
    this.rows = Number(config.rows);
    this.cols = Number(config.cols);
    this.readonly = !!config.readonly;
    this.canSetWalls = config.can_set_walls !== false;
    this.canSetPath = config.can_set_path !== false;
    this.canSetShading = config.can_set_shading === true;
    this.checkerId = config.checker_id || 'grid-wall-checker';
    this.shadeMode = 'B';
    this.coarsePointer = !!(
      root.matchMedia && root.matchMedia('(pointer: coarse)').matches
    );
    this.panMode = this.coarsePointer;
    this.instanceId = String(
      container.getAttribute('data-config-id') || ('grid-puzzle-' + config.task_id)
    ).replace(/[^a-zA-Z0-9_-]/g, '-');
    this.svg = container.querySelector('[data-grid-board]');
    this.viewport = container.querySelector('[data-grid-viewport]');
    this.form = container.querySelector('[data-grid-form]');
    this.message = container.querySelector('[data-grid-message]');
    this.saved = container.querySelector('[data-grid-saved]');
    this.wallInput = container.querySelector('[data-grid-walls-input]');
    this.shadingInput = container.querySelector('[data-grid-shading-input]');
    this.wallCount = container.querySelector('[data-grid-wall-count]');
    this.shadingCount = container.querySelector('[data-grid-shading-count]');
    this.selected = { row: 0, col: 0 };
    this.gesture = null;
    this.recentEdge = null;
    this.recentTimer = null;
    this.helpReturnFocus = null;
    this.refreshFrame = null;
    this.state = {
      walls: new Set(config.walls || []),
      notes: new Set(),
      shading: shadingFromRows(config.shading, this.rows, this.cols),
      notesVisible: true,
    };
    this.past = [];
    this.future = [];
    this.storageKey = 'interoves_grid_puzzle_v1:' + config.task_id + ':' + config.revision;
    if (!this.readonly) this.loadDraft();
    this.buildBoard();
    this.refresh();
    if (!this.readonly) this.bind();
  }

  Controller.prototype.cleanEdges = function (values) {
    var self = this;
    return (Array.isArray(values) ? values : []).filter(function (edge, index, arr) {
      return validEdge(edge, self.rows, self.cols) && arr.indexOf(edge) === index;
    });
  };

  Controller.prototype.cleanShading = function (values) {
    var result = {};
    var self = this;
    (Array.isArray(values) ? values : []).forEach(function (entry) {
      if (!Array.isArray(entry) || entry.length !== 2) return;
      var match = /^(\d+):(\d+)$/.exec(String(entry[0]));
      if (!match || (entry[1] !== 'B' && entry[1] !== 'G')) return;
      var row = Number(match[1]);
      var col = Number(match[2]);
      if (row < self.rows && col < self.cols) result[row + ':' + col] = entry[1];
    });
    return result;
  };

  Controller.prototype.loadDraft = function () {
    try {
      var raw = root.localStorage.getItem(this.storageKey);
      if (!raw) return;
      var data = JSON.parse(raw);
      this.state.walls = new Set(this.canSetWalls ? this.cleanEdges(data.walls) : []);
      this.state.notes = new Set(this.canSetPath ? this.cleanEdges(data.notes) : []);
      this.state.shading = this.canSetShading ? this.cleanShading(data.shading) : {};
      this.state.notesVisible = data.notesVisible !== false;
      this.past = (Array.isArray(data.past) ? data.past : []).slice(-HISTORY_LIMIT);
      this.future = (Array.isArray(data.future) ? data.future : []).slice(-HISTORY_LIMIT);
    } catch (e) {}
  };

  Controller.prototype.persist = function () {
    if (this.readonly) return;
    try {
      var current = snapshot(this.state);
      root.localStorage.setItem(this.storageKey, JSON.stringify({
        walls: current.walls,
        notes: current.notes,
        shading: current.shading,
        notesVisible: current.notesVisible,
        past: this.past,
        future: this.future,
      }));
      if (this.saved) this.saved.textContent = 'Сохранено локально';
    } catch (e) {
      if (this.saved) this.saved.textContent = '';
    }
  };

  Controller.prototype.removeDraft = function () {
    try { root.localStorage.removeItem(this.storageKey); } catch (e) {}
  };

  Controller.prototype.restore = function (snap) {
    this.state.walls = new Set(this.cleanEdges(snap && snap.walls));
    this.state.notes = new Set(this.cleanEdges(snap && snap.notes));
    this.state.shading = this.cleanShading(snap && snap.shading);
    this.state.notesVisible = !snap || snap.notesVisible !== false;
  };

  Controller.prototype.commitFrom = function (before, announcement) {
    var after = snapshot(this.state);
    if (snapshotsEqual(before, after)) return false;
    this.past.push(before);
    if (this.past.length > HISTORY_LIMIT) this.past.shift();
    this.future = [];
    this.hideFirstHint();
    this.persist();
    this.refresh();
    if (announcement) this.announce(announcement);
    return true;
  };

  Controller.prototype.scheduleRefresh = function () {
    var self = this;
    if (!root.requestAnimationFrame) {
      this.refresh();
      return;
    }
    if (this.refreshFrame !== null) return;
    this.refreshFrame = root.requestAnimationFrame(function () {
      self.refreshFrame = null;
      self.refresh();
    });
  };

  Controller.prototype.undo = function () {
    if (!this.past.length) return;
    this.future.push(snapshot(this.state));
    this.restore(this.past.pop());
    this.persist();
    this.refresh();
    this.announce('Последнее действие отменено');
  };

  Controller.prototype.redo = function () {
    if (!this.future.length) return;
    this.past.push(snapshot(this.state));
    this.restore(this.future.pop());
    this.persist();
    this.refresh();
    this.announce('Действие повторено');
  };

  Controller.prototype.toggleWall = function (edge) {
    if (!this.canSetWalls) return false;
    if (!validEdge(edge, this.rows, this.cols)) return false;
    if (this.state.walls.has(edge)) this.state.walls.delete(edge);
    else {
      this.state.walls.add(edge);
      this.state.notes.delete(edge);
    }
    this.recentEdge = edge;
    return true;
  };

  Controller.prototype.setWall = function (edge, add) {
    if (!this.canSetWalls) return false;
    if (!validEdge(edge, this.rows, this.cols)) return false;
    var changed = false;
    if (add && !this.state.walls.has(edge)) {
      this.state.walls.add(edge);
      changed = true;
    } else if (!add && this.state.walls.delete(edge)) changed = true;
    if (add && this.state.notes.delete(edge)) changed = true;
    if (changed) this.recentEdge = edge;
    return changed;
  };

  Controller.prototype.toggleNote = function (edge) {
    if (!this.canSetPath) return false;
    if (!validEdge(edge, this.rows, this.cols)) return false;
    if (this.state.notes.has(edge)) this.state.notes.delete(edge);
    else {
      this.state.notes.add(edge);
      this.state.walls.delete(edge);
    }
    this.recentEdge = edge;
    return true;
  };

  Controller.prototype.toggleShading = function (cell, value) {
    if (!this.canSetShading || !cell || (value !== 'B' && value !== 'G')) return false;
    var key = cell.row + ':' + cell.col;
    if (this.state.shading[key] === value) delete this.state.shading[key];
    else this.state.shading[key] = value;
    this.selected = { row: cell.row, col: cell.col };
    return true;
  };

  Controller.prototype.buildBoard = function () {
    var width = this.cols * CELL;
    var height = this.rows * CELL;
    this.svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    this.svg.setAttribute('width', width);
    this.svg.setAttribute('height', height);
    clear(this.svg);

    this.shadingGroup = svgEl('g', { className: 'new-grid-puzzle__shading', 'aria-hidden': 'true' });
    this.svg.appendChild(this.shadingGroup);

    var base = svgEl('g', { className: 'new-grid-puzzle__base' });
    var r, c;
    for (r = 0; r <= this.rows; r += 1) {
      base.appendChild(svgEl('line', { x1: 0, y1: r * CELL, x2: width, y2: r * CELL }));
    }
    for (c = 0; c <= this.cols; c += 1) {
      base.appendChild(svgEl('line', { x1: c * CELL, y1: 0, x2: c * CELL, y2: height }));
    }
    base.appendChild(svgEl('rect', { x: 1, y: 1, width: width - 2, height: height - 2, className: 'new-grid-puzzle__frame' }));
    this.svg.appendChild(base);

    this.notesGroup = svgEl('g', { className: 'new-grid-puzzle__notes' });
    this.svg.appendChild(this.notesGroup);

    var marks = svgEl('g', { className: 'new-grid-puzzle__marks', 'aria-hidden': 'true' });
    (this.config.marks || []).forEach(function (mark) {
      var x = (mark.col + 0.5) * CELL;
      var y = (mark.row + 0.5) * CELL;
      if (mark.value === 'O') {
        marks.appendChild(svgEl('circle', { cx: x, cy: y, r: 10, className: 'new-grid-puzzle__mark-backing' }));
        marks.appendChild(svgEl('circle', { cx: x, cy: y, r: 10, className: 'new-grid-puzzle__mark-o' }));
      } else if (mark.value === 'X') {
        marks.appendChild(svgEl('line', { x1: x - 9, y1: y - 9, x2: x + 9, y2: y + 9, className: 'new-grid-puzzle__mark-backing' }));
        marks.appendChild(svgEl('line', { x1: x + 9, y1: y - 9, x2: x - 9, y2: y + 9, className: 'new-grid-puzzle__mark-backing' }));
        marks.appendChild(svgEl('line', { x1: x - 9, y1: y - 9, x2: x + 9, y2: y + 9, className: 'new-grid-puzzle__mark-x' }));
        marks.appendChild(svgEl('line', { x1: x + 9, y1: y - 9, x2: x - 9, y2: y + 9, className: 'new-grid-puzzle__mark-x' }));
      } else if (mark.value === 'star') {
        marks.appendChild(svgEl('polygon', {
          points: starPoints(x, y), className: 'new-grid-puzzle__mark-star',
        }));
      } else if (/^arrow-(up|down|left|right)$/.test(mark.value)) {
        var rotations = { 'arrow-up': 0, 'arrow-right': 90, 'arrow-down': 180, 'arrow-left': 270 };
        marks.appendChild(svgEl('polygon', {
          points: [
            x + ',' + (y - 13), (x + 11) + ',' + y, (x + 4) + ',' + y,
            (x + 4) + ',' + (y + 13), (x - 4) + ',' + (y + 13),
            (x - 4) + ',' + y, (x - 11) + ',' + y,
          ].join(' '),
          transform: 'rotate(' + rotations[mark.value] + ' ' + x + ' ' + y + ')',
          className: 'new-grid-puzzle__mark-arrow',
        }));
      }
    });
    this.svg.appendChild(marks);
    this.selectionGroup = svgEl('g', { className: 'new-grid-puzzle__selection' });
    this.svg.appendChild(this.selectionGroup);
    this.wallsGroup = svgEl('g', { className: 'new-grid-puzzle__walls' });
    this.svg.appendChild(this.wallsGroup);
    this.previewGroup = svgEl('g', { className: 'new-grid-puzzle__preview' });
    this.svg.appendChild(this.previewGroup);

    if (!this.readonly) {
      var hits = svgEl('g', { className: 'new-grid-puzzle__hits' });
      if (this.canSetShading) for (r = 0; r < this.rows; r += 1) {
        for (c = 0; c < this.cols; c += 1) {
          hits.appendChild(svgEl('rect', {
            x: c * CELL, y: r * CELL, width: CELL, height: CELL,
            'data-grid-cell': r + ':' + c, className: 'new-grid-puzzle__cell-hit',
            id: this.instanceId + '-cell-' + r + '-' + c,
            role: 'gridcell', 'aria-rowindex': r + 1, 'aria-colindex': c + 1,
            'aria-label': 'Строка ' + (r + 1) + ', столбец ' + (c + 1) + ', белая',
          }));
        }
      }
      if (this.canSetWalls) for (r = 1; r < this.rows; r += 1) {
        for (c = 0; c < this.cols; c += 1) {
          var he = 'h:' + r + ':' + c;
          hits.appendChild(svgEl('line', {
            x1: c * CELL, y1: r * CELL, x2: (c + 1) * CELL, y2: r * CELL,
            'data-grid-edge': he, className: 'new-grid-puzzle__edge-hit', tabindex: '-1',
          }));
        }
      }
      if (this.canSetWalls) for (r = 0; r < this.rows; r += 1) {
        for (c = 1; c < this.cols; c += 1) {
          var ve = 'v:' + r + ':' + c;
          hits.appendChild(svgEl('line', {
            x1: c * CELL, y1: r * CELL, x2: c * CELL, y2: (r + 1) * CELL,
            'data-grid-edge': ve, className: 'new-grid-puzzle__edge-hit', tabindex: '-1',
          }));
        }
      }
      if (this.canSetPath) for (r = 0; r < this.rows; r += 1) {
        for (c = 0; c < this.cols; c += 1) {
          hits.appendChild(svgEl('circle', {
            cx: (c + 0.5) * CELL, cy: (r + 0.5) * CELL, r: 10,
            'data-grid-center': r + ':' + c, className: 'new-grid-puzzle__center-hit',
          }));
        }
      }
      this.svg.appendChild(hits);
    }
  };

  Controller.prototype.edgeLine = function (edge, className) {
    var p = parseEdge(edge);
    if (!p) return null;
    var attrs = { className: className, 'data-edge-id': edge };
    if (p.type === 'h') {
      attrs.x1 = p.col * CELL;
      attrs.y1 = p.row * CELL;
      attrs.x2 = (p.col + 1) * CELL;
      attrs.y2 = p.row * CELL;
    } else {
      attrs.x1 = p.col * CELL;
      attrs.y1 = p.row * CELL;
      attrs.x2 = p.col * CELL;
      attrs.y2 = (p.row + 1) * CELL;
    }
    return svgEl('line', attrs);
  };

  Controller.prototype.noteLine = function (edge, className) {
    var p = parseEdge(edge);
    if (!p) return null;
    var attrs = { className: className, 'data-edge-id': edge };
    if (p.type === 'h') {
      attrs.x1 = (p.col + 0.5) * CELL;
      attrs.y1 = (p.row - 0.5) * CELL;
      attrs.x2 = (p.col + 0.5) * CELL;
      attrs.y2 = (p.row + 0.5) * CELL;
    } else {
      attrs.x1 = (p.col - 0.5) * CELL;
      attrs.y1 = (p.row + 0.5) * CELL;
      attrs.x2 = (p.col + 0.5) * CELL;
      attrs.y2 = (p.row + 0.5) * CELL;
    }
    return svgEl('line', attrs);
  };

  Controller.prototype.refresh = function () {
    var self = this;
    if (this.refreshFrame !== null && root.cancelAnimationFrame) {
      root.cancelAnimationFrame(this.refreshFrame);
      this.refreshFrame = null;
    }
    clear(this.shadingGroup);
    Object.keys(this.state.shading).sort().forEach(function (key) {
      var bits = key.split(':');
      var value = self.state.shading[key];
      self.shadingGroup.appendChild(svgEl('rect', {
        x: Number(bits[1]) * CELL,
        y: Number(bits[0]) * CELL,
        width: CELL,
        height: CELL,
        className: value === 'B' ? 'new-grid-puzzle__shade-black' : 'new-grid-puzzle__shade-green',
      }));
    });
    clear(this.notesGroup);
    if (this.state.notesVisible) {
      sortedValues(this.state.notes).forEach(function (edge) {
        self.notesGroup.appendChild(self.noteLine(edge, 'new-grid-puzzle__note'));
      });
    }
    clear(this.wallsGroup);
    sortedValues(this.state.walls).forEach(function (edge) {
      var cls = 'new-grid-puzzle__wall' + (edge === self.recentEdge ? ' is-recent' : '');
      self.wallsGroup.appendChild(self.edgeLine(edge, 'new-grid-puzzle__wall-outline'));
      self.wallsGroup.appendChild(self.edgeLine(edge, cls));
    });
    if (this.recentEdge && root.setTimeout) {
      var animatedEdge = this.recentEdge;
      if (this.recentTimer) root.clearTimeout(this.recentTimer);
      this.recentTimer = root.setTimeout(function () {
        if (self.recentEdge === animatedEdge) self.recentEdge = null;
      }, 220);
    }
    clear(this.selectionGroup);
    if (!this.readonly && this.selected) {
      this.selectionGroup.appendChild(svgEl('rect', {
        x: this.selected.col * CELL + 3,
        y: this.selected.row * CELL + 3,
        width: CELL - 6,
        height: CELL - 6,
        rx: 4,
        className: 'new-grid-puzzle__selected-cell',
      }));
    }
    clear(this.previewGroup);
    if (this.gesture && this.gesture.type === 'note' && this.gesture.previewEdge) {
      this.previewGroup.appendChild(this.noteLine(this.gesture.previewEdge, 'new-grid-puzzle__note-preview'));
    }
    if (this.wallInput) this.wallInput.value = JSON.stringify(sortedValues(this.state.walls));
    if (this.wallCount) this.wallCount.textContent = this.wallCountText(this.state.walls.size);
    var rows = shadingRows(this.state.shading, this.rows, this.cols);
    if (this.shadingInput) this.shadingInput.value = JSON.stringify(rows);
    if (this.shadingCount) {
      var filled = Object.keys(this.state.shading).length;
      this.shadingCount.textContent = filled + ' / ' + (this.rows * this.cols) + ' клеток заполнено';
    }
    this.container.querySelectorAll('[data-grid-cell]').forEach(function (cell) {
      var key = cell.getAttribute('data-grid-cell');
      var bits = key.split(':');
      var value = self.state.shading[key];
      var color = value === 'B' ? 'чёрная' : value === 'G' ? 'светло-зелёная' : 'белая';
      var active = self.selected && key === self.selected.row + ':' + self.selected.col;
      cell.setAttribute(
        'aria-label',
        'Строка ' + (Number(bits[0]) + 1) + ', столбец ' + (Number(bits[1]) + 1) + ', ' + color
      );
      cell.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    if (this.selected && this.canSetShading) {
      this.svg.setAttribute(
        'aria-activedescendant',
        this.instanceId + '-cell-' + this.selected.row + '-' + this.selected.col
      );
    }
    var undo = this.container.querySelector('[data-grid-undo]');
    var redo = this.container.querySelector('[data-grid-redo]');
    if (undo) undo.disabled = !this.past.length;
    if (redo) redo.disabled = !this.future.length;
    var notesToggle = this.container.querySelector('[data-grid-notes-toggle]');
    if (notesToggle) {
      notesToggle.setAttribute('aria-pressed', this.state.notesVisible ? 'true' : 'false');
      notesToggle.textContent = this.state.notesVisible ? 'Скрыть путь' : 'Показать путь';
    }
    this.container.querySelectorAll('[data-grid-shade-mode]').forEach(function (button) {
      var active = !self.panMode && button.getAttribute('data-grid-shade-mode') === self.shadeMode;
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.classList.toggle('is-active', active);
    });
    this.container.classList.toggle('is-pan-mode', this.panMode);
    var panButton = this.container.querySelector('[data-grid-pan-mode]');
    if (panButton) {
      panButton.setAttribute('aria-pressed', this.panMode ? 'true' : 'false');
      panButton.classList.toggle('is-active', this.panMode);
      panButton.textContent = this.panMode ? 'Редактировать' : 'Перемещать';
    }
  };

  Controller.prototype.wallCountText = function (count) {
    var mod10 = count % 10;
    var mod100 = count % 100;
    var word = (mod10 === 1 && mod100 !== 11) ? 'стена' :
      (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) ? 'стены' : 'стен';
    return count + ' ' + word;
  };

  Controller.prototype.pointFromEvent = function (event) {
    var rect = this.svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (this.cols * CELL / rect.width),
      y: (event.clientY - rect.top) * (this.rows * CELL / rect.height),
    };
  };

  Controller.prototype.cellFromPoint = function (point) {
    var col = Math.floor(point.x / CELL);
    var row = Math.floor(point.y / CELL);
    if (row < 0 || row >= this.rows || col < 0 || col >= this.cols) return null;
    return { row: row, col: col };
  };

  Controller.prototype.edgeFromPoint = function (point) {
    var row = Math.floor(point.y / CELL);
    var col = Math.floor(point.x / CELL);
    var vx = Math.round(point.x / CELL);
    var hy = Math.round(point.y / CELL);
    var vDist = Math.abs(point.x - vx * CELL);
    var hDist = Math.abs(point.y - hy * CELL);
    var candidate = null;
    if (vDist <= hDist && vDist <= 12 && row >= 0 && row < this.rows && vx > 0 && vx < this.cols) {
      candidate = 'v:' + row + ':' + vx;
    } else if (hDist <= 12 && col >= 0 && col < this.cols && hy > 0 && hy < this.rows) {
      candidate = 'h:' + hy + ':' + col;
    }
    return candidate;
  };

  Controller.prototype.applyDragEdge = function (edge) {
    if (!this.gesture || this.gesture.type !== 'edge' || !edge || this.gesture.visited.has(edge)) return;
    this.gesture.visited.add(edge);
    if (this.setWall(edge, this.gesture.add)) {
      this.gesture.changed = true;
      this.scheduleRefresh();
    }
  };

  Controller.prototype.onPointerDown = function (event) {
    if (this.panMode) return;
    if (event.button !== undefined && event.button !== 0) return;
    var edge = event.target.getAttribute && event.target.getAttribute('data-grid-edge');
    var center = event.target.getAttribute && event.target.getAttribute('data-grid-center');
    var cellRaw = event.target.getAttribute && event.target.getAttribute('data-grid-cell');
    if (!edge && !center && !cellRaw) return;
    event.preventDefault();
    try { this.svg.focus({ preventScroll: true }); } catch (focusError) { this.svg.focus(); }
    try { this.svg.setPointerCapture(event.pointerId); } catch (e) {}
    if (cellRaw && this.canSetShading) {
      var cellBits = cellRaw.split(':');
      this.gesture = {
        type: 'shade', pointerId: event.pointerId, before: snapshot(this.state),
        cell: { row: Number(cellBits[0]), col: Number(cellBits[1]) },
        shadeValue: this.shadeMode,
      };
      this.selected = this.gesture.cell;
      this.refresh();
      return;
    }
    if (edge && this.canSetWalls) {
      this.gesture = {
        type: 'edge', pointerId: event.pointerId, before: snapshot(this.state),
        add: !this.state.walls.has(edge), visited: new Set(), changed: false,
      };
      this.applyDragEdge(edge);
    } else if (center && this.canSetPath) {
      var bits = center.split(':');
      this.gesture = {
        type: 'note', pointerId: event.pointerId, before: snapshot(this.state),
        start: { row: Number(bits[0]), col: Number(bits[1]) }, previewEdge: null,
        shadeValue: this.shadeMode,
      };
      this.selected = { row: Number(bits[0]), col: Number(bits[1]) };
      this.refresh();
    }
  };

  Controller.prototype.onPointerMove = function (event) {
    if (!this.gesture || event.pointerId !== this.gesture.pointerId) return;
    event.preventDefault();
    var point = this.pointFromEvent(event);
    if (this.gesture.type === 'shade') return;
    if (this.gesture.type === 'edge') {
      this.applyDragEdge(this.edgeFromPoint(point));
      return;
    }
    var cell = this.cellFromPoint(point);
    var preview = edgeBetweenCells(this.gesture.start, cell);
    if (preview !== this.gesture.previewEdge) {
      this.gesture.previewEdge = preview;
      this.scheduleRefresh();
    }
  };

  Controller.prototype.finishGesture = function (event, cancel) {
    if (!this.gesture || (event && event.pointerId !== this.gesture.pointerId)) return;
    var gesture = this.gesture;
    if (cancel) {
      this.restore(gesture.before);
      this.gesture = null;
      this.refresh();
      this.announce('Жест отменён');
      return;
    }
    var announcement = gesture.type === 'edge' ? 'Стены изменены' : 'Заметка изменена';
    if (gesture.type === 'shade' && this.canSetShading) {
      this.toggleShading(gesture.cell, gesture.shadeValue);
      announcement = gesture.shadeValue === 'G' ? 'Клетка закрашена зелёным' : 'Клетка закрашена чёрным';
    } else if (gesture.type === 'note' && this.canSetPath && gesture.previewEdge) {
      this.toggleNote(gesture.previewEdge);
    } else if (gesture.type === 'note' && this.canSetShading) {
      this.toggleShading(gesture.start, gesture.shadeValue || 'B');
      announcement = gesture.shadeValue === 'G' ? 'Клетка закрашена зелёным' : 'Клетка закрашена чёрным';
    }
    this.gesture = null;
    this.commitFrom(gesture.before, announcement);
    this.refresh();
  };

  Controller.prototype.keyboardAction = function (event) {
    var shadeKey = String(event.key || '').toLowerCase();
    if (
      (shadeKey === 'b' || shadeKey === 'g') && this.canSetShading
      && !event.ctrlKey && !event.metaKey && !event.altKey
    ) {
      var beforeShade = snapshot(this.state);
      var shadeValue = shadeKey.toUpperCase();
      this.panMode = false;
      this.shadeMode = shadeValue;
      this.toggleShading(this.selected, shadeValue);
      this.commitFrom(beforeShade, shadeValue === 'B' ? 'Клетка закрашена чёрным' : 'Клетка закрашена зелёным');
      return true;
    }
    var keys = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    var delta = keys[event.key];
    if (!delta) return false;
    var next = { row: this.selected.row + delta[0], col: this.selected.col + delta[1] };
    if (next.row < 0 || next.row >= this.rows || next.col < 0 || next.col >= this.cols) return true;
    if (event.shiftKey || event.altKey) {
      var edge = edgeBetweenCells(this.selected, next);
      var before = snapshot(this.state);
      if (event.altKey && this.canSetPath) {
        this.toggleNote(edge);
        this.commitFrom(before, 'Путь изменён');
      } else if (event.shiftKey && this.canSetWalls) {
        this.toggleWall(edge);
        this.commitFrom(before, 'Стена изменена');
      }
    } else {
      this.selected = next;
      this.refresh();
    }
    return true;
  };

  Controller.prototype.openHelp = function () {
    var modal = this.container.querySelector('[data-grid-help-modal]');
    if (!modal) return;
    modal.hidden = false;
    modal.classList.add('is-open');
    root.document.body.style.overflow = 'hidden';
    this.helpReturnFocus = root.document.activeElement;
    var closeButton = modal.querySelector('[data-grid-help-close]');
    if (closeButton) closeButton.focus();
  };

  Controller.prototype.closeHelp = function () {
    var modal = this.container.querySelector('[data-grid-help-modal]');
    if (!modal) return;
    modal.hidden = true;
    modal.classList.remove('is-open');
    root.document.body.style.overflow = '';
    if (this.helpReturnFocus && this.helpReturnFocus.focus) this.helpReturnFocus.focus();
    this.helpReturnFocus = null;
  };

  Controller.prototype.hideFirstHint = function () {
    var hint = this.container.querySelector('[data-grid-first-hint]');
    if (hint) hint.hidden = true;
  };

  Controller.prototype.announce = function (text) {
    if (this.saved) this.saved.textContent = text || '';
  };

  Controller.prototype.resetPart = function (part) {
    var before = snapshot(this.state);
    if (part === 'notes') {
      this.state.notes.clear();
    } else if (part === 'walls') {
      if (this.state.walls.size && !root.confirm('Сбросить все стены?')) return;
      this.state.walls.clear();
    } else if (part === 'shading') {
      if (Object.keys(this.state.shading).length && !root.confirm('Сбросить закраску?')) return;
      this.state.shading = {};
    } else {
      if ((this.state.walls.size || this.state.notes.size || Object.keys(this.state.shading).length) && !root.confirm('Сбросить всё поле?')) return;
      this.state.walls.clear();
      this.state.notes.clear();
      this.state.shading = {};
    }
    this.commitFrom(before, 'Поле очищено');
  };

  Controller.prototype.submit = function (event) {
    var self = this;
    event.preventDefault();
    if (this.form.dataset.submitting === '1') return;
    if (this.checkerId === 'grid-shading-checker' && Object.keys(this.state.shading).length !== this.rows * this.cols) {
      if (this.message) this.message.textContent = 'Сначала закрасьте каждую клетку чёрным или светло-зелёным.';
      return;
    }
    this.form.dataset.submitting = '1';
    var button = this.form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    if (this.message) this.message.textContent = 'Проверяем…';
    if (this.wallInput) this.wallInput.value = JSON.stringify(sortedValues(this.state.walls));
    if (this.shadingInput) this.shadingInput.value = JSON.stringify(shadingRows(this.state.shading, this.rows, this.cols));
    var csrf = this.form.querySelector('input[name="csrfmiddlewaretoken"]');
    root.fetch(this.form.action, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrf ? csrf.value : '',
      },
      body: new root.FormData(this.form),
      credentials: 'same-origin',
    }).then(function (response) { return response.json(); }).then(function (data) {
      self.form.dataset.submitting = '0';
      if (button) button.disabled = false;
      if (data && root.interovesAnalytics && root.interovesAnalytics.flushPendingGoals) {
        root.interovesAnalytics.flushPendingGoals(data.analytics_events || []);
      }
      if (data && data.status === 'ok' && data.grid_puzzle_correct) {
        self.removeDraft();
        if (self.message) self.message.textContent = 'Верно!';
        if (data.update_task_html_new && root.applyNewUiTaskHtml) root.applyNewUiTaskHtml(data.update_task_html_new);
        else root.location.reload();
        return;
      }
      if (data && data.status === 'ok') {
        if (data.update_task_html_new && root.applyNewUiTaskHtml) {
          root.applyNewUiTaskHtml(data.update_task_html_new);
          var freshMessage = root.document.querySelector(
            '#new-task-' + data.task_id + ' [data-grid-message]'
          );
          if (freshMessage) freshMessage.textContent = 'Пока неверно. Поле сохранено локально.';
          if (root.openAttemptsPopoverForTask) {
            root.openAttemptsPopoverForTask(data.task_id, data.attempt_id);
          }
        } else if (self.message) {
          self.message.textContent = 'Пока неверно. Поле сохранено локально.';
        }
      } else if (data && data.status === 'duplicate') {
        if (self.message) self.message.textContent = 'Такое состояние поля уже отправлялось.';
        if (root.openAttemptsPopoverForTask) {
          root.openAttemptsPopoverForTask(data.task_id || self.config.task_id, data.attempt_id);
        }
      } else if (data && data.status === 'attempt_limit_exceeded') {
        if (self.message) self.message.textContent = 'Попытки закончились.';
      } else if (data && data.status === 'no_profile') {
        if (self.message) self.message.textContent = 'Нужно заполнить профиль.';
      } else if (data && data.status === 'no_team') {
        if (self.message) self.message.textContent = 'Для командного режима нужна команда.';
      } else if (data && data.status === 'no_anon') {
        if (self.message) self.message.textContent = 'Не удалось сохранить анонимный ключ.';
      } else if (data && data.status === 'no_access') {
        if (self.message) self.message.textContent = 'Нет доступа к отправке ответа.';
      } else if (data && data.status === 'invalid_form') {
        if (self.message) self.message.textContent = 'Поле заполнено некорректно.';
      } else {
        if (self.message) self.message.textContent = 'Не удалось проверить ответ.';
      }
    }).catch(function () {
      self.form.dataset.submitting = '0';
      if (button) button.disabled = false;
      if (self.message) self.message.textContent = 'Ошибка сети. Решение сохранено локально.';
    });
  };

  Controller.prototype.bind = function () {
    var self = this;
    this.svg.addEventListener('pointerdown', function (event) { self.onPointerDown(event); });
    this.svg.addEventListener('pointermove', function (event) { self.onPointerMove(event); });
    this.svg.addEventListener('pointerup', function (event) { self.finishGesture(event, false); });
    this.svg.addEventListener('pointercancel', function (event) { self.finishGesture(event, true); });
    this.svg.addEventListener('contextmenu', function (event) {
      if (!self.canSetShading || self.panMode) return;
      var cell = self.cellFromPoint(self.pointFromEvent(event));
      if (!cell) return;
      event.preventDefault();
      var before = snapshot(self.state);
      self.toggleShading(cell, 'G');
      self.commitFrom(before, 'Клетка закрашена зелёным');
    });
    this.svg.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        if (self.gesture) self.finishGesture(null, true);
        else self.svg.blur();
        event.preventDefault();
      } else if (event.key === '?') {
        self.openHelp();
        event.preventDefault();
      } else if (self.keyboardAction(event)) event.preventDefault();
    });
    var actions = {
      '[data-grid-undo]': function () { self.undo(); },
      '[data-grid-redo]': function () { self.redo(); },
      '[data-grid-clear-notes]': function () { self.resetPart('notes'); },
      '[data-grid-reset-walls]': function () { self.resetPart('walls'); },
      '[data-grid-reset-shading]': function () { self.resetPart('shading'); },
      '[data-grid-reset-all]': function () { self.resetPart('all'); },
      '[data-grid-pan-mode]': function () {
        self.panMode = !self.panMode;
        self.refresh();
      },
      '[data-grid-help]': function () { self.openHelp(); },
      '[data-grid-fullscreen]': function () {
        if (self.viewport && self.viewport.requestFullscreen) self.viewport.requestFullscreen().catch(function () {});
      },
      '[data-grid-notes-toggle]': function () {
        var before = snapshot(self.state);
        self.state.notesVisible = !self.state.notesVisible;
        self.commitFrom(before, self.state.notesVisible ? 'Заметки показаны' : 'Заметки скрыты');
      },
    };
    Object.keys(actions).forEach(function (selector) {
      var button = self.container.querySelector(selector);
      if (button) button.addEventListener('click', actions[selector]);
    });
    this.container.querySelectorAll('[data-grid-shade-mode]').forEach(function (button) {
      button.addEventListener('click', function () {
        self.shadeMode = button.getAttribute('data-grid-shade-mode') === 'G' ? 'G' : 'B';
        self.panMode = false;
        self.refresh();
        try { self.svg.focus({ preventScroll: true }); } catch (focusError) { self.svg.focus(); }
      });
    });
    this.container.querySelectorAll('[data-grid-help-close]').forEach(function (el) {
      el.addEventListener('click', function () { self.closeHelp(); });
    });
    if (this.form) this.form.addEventListener('submit', function (event) { self.submit(event); });
    this.container.addEventListener('keydown', function (event) {
      var target = event.target;
      if (target && (target.matches('input,textarea,select,[contenteditable="true"]'))) return;
      var helpModal = self.container.querySelector('[data-grid-help-modal].is-open');
      if (event.key === 'Escape' && helpModal) {
        self.closeHelp(); event.preventDefault(); return;
      }
      var ctrl = event.ctrlKey || event.metaKey;
      if (ctrl && !event.shiftKey && event.key.toLowerCase() === 'z') {
        self.undo(); event.preventDefault();
      } else if ((ctrl && event.shiftKey && event.key.toLowerCase() === 'z') || (ctrl && event.key.toLowerCase() === 'y')) {
        self.redo(); event.preventDefault();
      }
    });
  };

  function initOne(container) {
    if (!container || container.dataset.gridInitialized === '1') return null;
    var id = container.getAttribute('data-config-id');
    var script = container.querySelector('script[type="application/json"]') || (id && root.document.getElementById(id));
    if (!script) return null;
    try {
      var config = JSON.parse(script.textContent || '{}');
      container.dataset.gridInitialized = '1';
      var controller = new Controller(container, config);
      container.gridPuzzleController = controller;
      return controller;
    } catch (e) {
      container.dataset.gridInitialized = 'error';
      return null;
    }
  }

  function initAll(scope) {
    if (!root || !root.document) return [];
    var base = scope || root.document;
    var nodes = [];
    if (base.matches && base.matches('[data-grid-puzzle]')) nodes.push(base);
    if (base.querySelectorAll) nodes = nodes.concat(Array.from(base.querySelectorAll('[data-grid-puzzle]')));
    return nodes.map(initOne).filter(Boolean);
  }

  if (root && root.document) {
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', function () { initAll(); });
    else initAll();
  }

  return {
    CELL: CELL,
    Controller: Controller,
    edgeBetweenCells: edgeBetweenCells,
    parseEdge: parseEdge,
    snapshot: snapshot,
    shadingFromRows: shadingFromRows,
    shadingRows: shadingRows,
    snapshotsEqual: snapshotsEqual,
    validEdge: validEdge,
    initAll: initAll,
  };
});
