'use strict';

var assert = require('assert');
var G = require('./grid_puzzle.js');

function makeController() {
  var c = Object.create(G.Controller.prototype);
  c.rows = 3;
  c.cols = 4;
  c.readonly = false;
  c.canSetWalls = true;
  c.canSetPath = true;
  c.canSetShading = true;
  c.panMode = false;
  c.shadeMode = 'B';
  c.state = { walls: new Set(), notes: new Set(), shading: {}, notesVisible: true };
  c.past = [];
  c.future = [];
  c.persist = function () {};
  c.refresh = function () {};
  c.hideFirstHint = function () {};
  c.announce = function () {};
  return c;
}

(function testEdgeBetweenCells() {
  assert.strictEqual(G.edgeBetweenCells({ row: 0, col: 0 }, { row: 1, col: 0 }), 'h:1:0');
  assert.strictEqual(G.edgeBetweenCells({ row: 2, col: 2 }, { row: 2, col: 1 }), 'v:2:2');
  assert.strictEqual(G.edgeBetweenCells({ row: 0, col: 0 }, { row: 1, col: 1 }), null);
})();

(function testEdgeValidation() {
  assert.strictEqual(G.validEdge('h:1:0', 3, 4), true);
  assert.strictEqual(G.validEdge('v:2:3', 3, 4), true);
  assert.strictEqual(G.validEdge('h:0:0', 3, 4), false);
  assert.strictEqual(G.validEdge('v:0:4', 3, 4), false);
})();

(function testWallAndNoteAreMutuallyExclusive() {
  var c = makeController();
  c.toggleWall('h:1:0');
  assert.strictEqual(c.state.walls.has('h:1:0'), true);
  c.toggleNote('h:1:0');
  assert.strictEqual(c.state.walls.has('h:1:0'), false);
  assert.strictEqual(c.state.notes.has('h:1:0'), true);
  c.toggleWall('h:1:0');
  assert.strictEqual(c.state.notes.has('h:1:0'), false);
})();

(function testDragCanCommitAsOneUndoAction() {
  var c = makeController();
  var before = G.snapshot(c.state);
  c.setWall('h:1:0', true);
  c.setWall('h:1:1', true);
  c.setWall('v:0:1', true);
  c.commitFrom(before, '');
  assert.strictEqual(c.past.length, 1);
  assert.deepStrictEqual(G.snapshot(c.state).walls, ['h:1:0', 'h:1:1', 'v:0:1']);
  c.undo();
  assert.deepStrictEqual(G.snapshot(c.state).walls, []);
  c.redo();
  assert.deepStrictEqual(G.snapshot(c.state).walls, ['h:1:0', 'h:1:1', 'v:0:1']);
})();

(function testSnapshotIsCanonical() {
  var state = {
    walls: new Set(['v:0:2', 'h:1:0']),
    notes: new Set(['v:1:1']),
    shading: { '1:2': 'G', '0:0': 'B' },
    notesVisible: false,
  };
  assert.deepStrictEqual(G.snapshot(state), {
    walls: ['h:1:0', 'v:0:2'],
    notes: ['v:1:1'],
    shading: [['0:0', 'B'], ['1:2', 'G']],
    notesVisible: false,
  });
})();

(function testShadingHelpersAndUndo() {
  assert.deepStrictEqual(G.shadingFromRows(['BGW', 'WGB'], 2, 3), {
    '0:0': 'B', '0:1': 'G', '1:1': 'G', '1:2': 'B',
  });
  assert.deepStrictEqual(G.shadingRows({ '0:0': 'B', '1:2': 'G' }, 2, 3), ['BWW', 'WWG']);
  var c = makeController();
  var before = G.snapshot(c.state);
  assert.strictEqual(c.toggleShading({ row: 1, col: 2 }, 'G'), true);
  c.commitFrom(before, '');
  assert.deepStrictEqual(G.shadingRows(c.state.shading, 3, 4), ['WWWW', 'WWGW', 'WWWW']);
  c.undo();
  assert.deepStrictEqual(G.shadingRows(c.state.shading, 3, 4), ['WWWW', 'WWWW', 'WWWW']);
})();

(function testShadingPointerCommitsOnReleaseAndHonorsMode() {
  var c = makeController();
  c.shadeMode = 'G';
  c.svg = { focus: function () {}, setPointerCapture: function () {} };
  var event = {
    button: 0,
    pointerId: 7,
    preventDefault: function () {},
    target: {
      getAttribute: function (name) { return name === 'data-grid-cell' ? '1:2' : null; },
    },
  };
  c.onPointerDown(event);
  assert.deepStrictEqual(G.snapshot(c.state).shading, []);
  c.finishGesture({ pointerId: 7 }, false);
  assert.deepStrictEqual(G.snapshot(c.state).shading, [['1:2', 'G']]);
})();

(function testPanModeDoesNotStartEditingGesture() {
  var c = makeController();
  c.panMode = true;
  c.onPointerDown({
    button: 0,
    target: { getAttribute: function () { throw new Error('target must not be inspected'); } },
  });
  assert.strictEqual(c.gesture, undefined);
  assert.deepStrictEqual(G.snapshot(c.state).shading, []);
})();

(function testShadingKeyboardIgnoresModifiers() {
  var c = makeController();
  c.selected = { row: 0, col: 0 };
  assert.strictEqual(c.keyboardAction({ key: 'b', ctrlKey: true }), false);
  assert.deepStrictEqual(G.snapshot(c.state).shading, []);
  c.panMode = true;
  assert.strictEqual(c.keyboardAction({ key: 'g' }), true);
  assert.strictEqual(c.panMode, false);
  assert.deepStrictEqual(G.snapshot(c.state).shading, [['0:0', 'G']]);
})();

(function testDisabledCapabilitiesBlockMutations() {
  var c = makeController();
  c.canSetWalls = false;
  assert.strictEqual(c.toggleWall('h:1:0'), false);
  assert.deepStrictEqual(G.snapshot(c.state).walls, []);
  c.canSetPath = false;
  assert.strictEqual(c.toggleNote('v:0:1'), false);
  assert.deepStrictEqual(G.snapshot(c.state).notes, []);
  c.canSetShading = false;
  assert.strictEqual(c.toggleShading({ row: 0, col: 0 }, 'B'), false);
  assert.deepStrictEqual(G.snapshot(c.state).shading, []);
})();

console.log('grid_puzzle.test.js: ok');
