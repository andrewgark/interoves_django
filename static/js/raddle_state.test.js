'use strict';

var assert = require('assert');
require('./raddle_state.js');
var S = global.RaddleState;

// ── Server response matrix ────────────────────────────────────────────────
// Expectations are the client side of RADDLE_RESPONSE_SCENARIOS in
// games/raddle_response_contract.py; test_raddle_response_contract keeps the
// ids and flags of both files in sync.
var SCENARIO_EXPECTATIONS = {
  correct: {
    response: { status: 'ok', raddle_correct: true },
    replace_html: true, advance_focus: true, mark_wrong: false, keep_input: false,
  },
  wrong: {
    response: { status: 'ok', raddle_correct: false, raddle_needs_sync: false },
    replace_html: false, advance_focus: false, mark_wrong: true, keep_input: true,
  },
  needs_sync: {
    response: { status: 'ok', raddle_correct: false, raddle_needs_sync: true },
    replace_html: true, advance_focus: true, mark_wrong: false, keep_input: false,
  },
  stale_ui: {
    response: {
      status: 'ok', raddle_correct: false, raddle_needs_sync: true, raddle_stale_ui: true,
    },
    replace_html: true, advance_focus: false, mark_wrong: false, keep_input: false,
  },
  duplicate_unsolved: {
    response: { status: 'duplicate', raddle_duplicate_solved: false },
    replace_html: false, advance_focus: false, mark_wrong: true, keep_input: true,
  },
  duplicate_solved: {
    response: { status: 'duplicate', raddle_duplicate_solved: true },
    replace_html: true, advance_focus: true, mark_wrong: false, keep_input: false,
  },
};

function testResponseMatrixCoversEveryScenario() {
  Object.keys(SCENARIO_EXPECTATIONS).forEach(function (id) {
    var spec = SCENARIO_EXPECTATIONS[id];
    var verdict = S.classifyAttemptResponse(spec.response);
    assert.strictEqual(verdict.id, id, 'scenario ' + id + ' classified as ' + verdict.id);
    assert.strictEqual(verdict.replaceHtml, spec.replace_html, id + '.replace_html');
    assert.strictEqual(verdict.advanceFocus, spec.advance_focus, id + '.advance_focus');
    assert.strictEqual(verdict.markWrong, spec.mark_wrong, id + '.mark_wrong');
    assert.strictEqual(verdict.keepInput, spec.keep_input, id + '.keep_input');
  });
  assert.deepStrictEqual(
    S.RESPONSE_MATRIX_IDS.slice().sort(),
    Object.keys(SCENARIO_EXPECTATIONS).sort()
  );
}

function testAdvanceImpliesReplaceHtml() {
  S.RESPONSE_MATRIX.forEach(function (row) {
    if (row.effects.advance_focus) {
      assert.strictEqual(row.effects.replace_html, true, row.id);
    }
    if (row.effects.mark_wrong) {
      assert.strictEqual(row.effects.keep_input, true, row.id);
    }
  });
}

function testUpdateTaskHtmlIsNotAProgressSignal() {
  // A wrong answer that happens to carry HTML must not redraw or advance.
  var verdict = S.classifyAttemptResponse({
    status: 'ok',
    raddle_correct: false,
    update_task_html_new: { '7': '<div></div>' },
    solved_indices: [0, 1, 2],
  });
  assert.strictEqual(verdict.id, 'wrong');
  assert.strictEqual(verdict.replaceHtml, false);
  assert.strictEqual(verdict.advanceFocus, false);
}

function testStatusOkAloneIsNotProgress() {
  var verdict = S.classifyAttemptResponse({ status: 'ok' });
  assert.strictEqual(verdict.id, 'wrong');
  assert.strictEqual(verdict.advanceFocus, false);
}

function testStaleUiWinsOverNeedsSync() {
  var verdict = S.classifyAttemptResponse({
    status: 'ok', raddle_needs_sync: true, raddle_stale_ui: true,
  });
  assert.strictEqual(verdict.id, 'stale_ui');
  assert.strictEqual(verdict.replaceHtml, true);
  assert.strictEqual(verdict.advanceFocus, false);
}

function testPartialProgressStatusIsNotCorrect() {
  // attempt_status Partial describes the whole task, not this word.
  var verdict = S.classifyAttemptResponse({
    status: 'ok', attempt_status: 'Partial', raddle_correct: false,
  });
  assert.strictEqual(verdict.id, 'wrong');
  assert.strictEqual(verdict.markWrong, true);
}

function testWrongKeepsTheSubmitLock() {
  // The value stays in the field, so it must not be auto-resent.
  assert.strictEqual(S.classifyAttemptResponse({ status: 'ok' }).releaseSubmitLock, false);
  assert.strictEqual(
    S.classifyAttemptResponse({ status: 'ok', raddle_correct: true }).releaseSubmitLock,
    true
  );
}

function testBadAndUnknownResponses() {
  var empty = S.classifyAttemptResponse(null);
  assert.strictEqual(empty.id, 'bad_response');
  assert.strictEqual(empty.replaceHtml, false);
  assert.strictEqual(empty.keepInput, true);

  var unknown = S.classifyAttemptResponse({ status: 'attempt_limit_exceeded' });
  assert.strictEqual(unknown.id, 'server_error');
  assert.strictEqual(unknown.messageKey, 'status');
  assert.strictEqual(unknown.replaceHtml, false);
}

function testWordIndexIsZeroBasedFromTheRequest() {
  assert.strictEqual(S.resolveWordIndex({ raddle_word_index: 0 }, 3), 0);
  assert.strictEqual(S.resolveWordIndex({ raddle_word_index: '4' }, 3), 4);
  assert.strictEqual(S.resolveWordIndex({}, '2'), 2);
  assert.strictEqual(S.resolveWordIndex({}, null), null);
}

// ── Retry / timeout ───────────────────────────────────────────────────────
function testAbortIsNeverRetried() {
  var verdict = S.classifyRequestFailure({ name: 'AbortError' }, 1, 3);
  assert.strictEqual(verdict.retry, false);
  assert.strictEqual(verdict.reason, 'abort');
}

function testOfflineRetriesUpToThreeAttempts() {
  assert.strictEqual(S.classifyRequestFailure(new TypeError('failed'), 1, 3).retry, true);
  assert.strictEqual(S.classifyRequestFailure(new TypeError('failed'), 2, 3).retry, true);
  var last = S.classifyRequestFailure(new TypeError('failed'), 3, 3);
  assert.strictEqual(last.retry, false);
  assert.strictEqual(last.reason, 'exhausted');
  assert.strictEqual(last.messageKey, 'network');
}

function testHtml403IsNotRetriedAndNotMaskedAsNetwork() {
  var err = new Error('non-json-response');
  err.httpStatus = 403;
  err.retryable = false;
  var verdict = S.classifyRequestFailure(err, 1, 3);
  assert.strictEqual(verdict.retry, false);
  assert.strictEqual(verdict.reason, 'forbidden');
  assert.strictEqual(verdict.messageKey, 'stale_session');
}

// ── In-flight de-duplication ──────────────────────────────────────────────
function testInFlightDeduplication() {
  var reg = S.createRequestRegistry();
  var key = reg.key('7', 3);
  assert.strictEqual(key, '7:3');
  assert.strictEqual(reg.begin(key), true);
  assert.strictEqual(reg.begin(key), false, 'parallel request for the same word');
  assert.strictEqual(reg.isInFlight(key), true);
  reg.end(key);
  assert.strictEqual(reg.begin(key), true);
}

function testInFlightIsPerTaskAndWord() {
  var reg = S.createRequestRegistry();
  assert.strictEqual(reg.begin(reg.key('7', 3)), true);
  assert.strictEqual(reg.begin(reg.key('7', 4)), true);
  assert.strictEqual(reg.begin(reg.key('8', 3)), true);
  assert.strictEqual(reg.key('7', ''), '');
  assert.strictEqual(reg.key('', 3), '');
  assert.strictEqual(reg.begin(''), false);
}

function testPasteAndInputDeduplication() {
  // paste fires accept+complete for one gesture: the second call is blocked
  // by the submitted value, not by timing.
  var reg = S.createRequestRegistry();
  var key = reg.key('7', 3);
  reg.begin(key);
  reg.markSubmitted(key, 'КАНАВА');
  assert.strictEqual(reg.isDuplicateSubmit(key, 'КАНАВА'), true);
  assert.strictEqual(reg.isDuplicateSubmit(key, 'КАНАВЫ'), false);
}

function testSubmitLockSurvivesRerender() {
  // applyNewUiTaskHtml replaces the DOM; the registry is not attached to it.
  var reg = S.createRequestRegistry();
  var key = reg.key('7', 3);
  reg.begin(key);
  reg.markSubmitted(key, 'СЛОВО');
  // ... re-render happens here ...
  assert.strictEqual(reg.isInFlight(key), true);
  assert.strictEqual(reg.isDuplicateSubmit(key, 'СЛОВО'), true);
}

function testSubmitLockResetPaths() {
  var reg = S.createRequestRegistry();
  var key = reg.key('7', 3);
  reg.markSubmitted(key, 'СЛОВО');
  reg.clearSubmitted(key);
  assert.strictEqual(reg.isDuplicateSubmit(key, 'СЛОВО'), false);
  assert.strictEqual(reg.submittedValue(key), null);
}

// ── Input session ─────────────────────────────────────────────────────────
function fakeClock() {
  var t = 1000;
  return {
    now: function () { return t; },
    advance: function (ms) { t += ms; },
  };
}

function makeSession(clock) {
  return S.createInputSession({ now: clock.now });
}

function testSessionNeedsAnExplicitUserAction() {
  var clock = fakeClock();
  var session = makeSession(clock);
  assert.strictEqual(session.isActive('7'), false);
  assert.strictEqual(session.ownsPin(), false);
  session.begin('7', 3, 'real');
  assert.strictEqual(session.isActive('7'), true);
  assert.strictEqual(session.isActive('8'), false);
  assert.deepStrictEqual(session.anchor, { taskId: '7', wordIndex: '3' });
}

function testAndroidBackClosesKeyboardButKeepsAnchor() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  session.noteInput();
  assert.strictEqual(session.keyboardState, 'open');
  session.noteKeyboardClosed();
  assert.strictEqual(session.keyboardState, 'closed');
  assert.strictEqual(session.isActive('7'), false, 'no session after Back');
  assert.strictEqual(session.ownsPin(), false);
  assert.deepStrictEqual(session.anchor, { taskId: '7', wordIndex: '3' },
    'caret/anchor stays on the same pair');
  assert.strictEqual(session.keyboardClosedRecently(700), true);
  clock.advance(800);
  assert.strictEqual(session.keyboardClosedRecently(700), false);
}

function testAsyncResponseAfterKeyboardDismissalMayNotFocus() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  session.noteKeyboardClosed();
  assert.strictEqual(
    S.mayApplyEffect('server.advance', 'moveFocus', { sessionActive: session.isActive('7') }),
    false
  );
  assert.strictEqual(
    S.shouldCascadeSubmit({
      isTournament: false, hasAuto: true, filled: true, sessionActive: session.isActive('7'),
    }),
    false,
    'no cascade against the user closing the keyboard'
  );
}

function testExplicitTapOnAnotherRowSwitchesAnchor() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  session.noteKeyboardClosed();
  session.begin('7', 5, 'real');
  assert.strictEqual(session.isActive('7'), true);
  assert.deepStrictEqual(session.anchor, { taskId: '7', wordIndex: '5' });
  assert.strictEqual(session.dismissSessionActive(), false);
}

function testTapOnEmptySpaceKeepsThePair() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  session.end();
  assert.strictEqual(session.isActive('7'), false);
  assert.deepStrictEqual(session.anchor, { taskId: '7', wordIndex: '3' });
}

function testLadderScrollKeepsThePinSession() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  session.setOwner('pin');
  assert.strictEqual(session.ownsPin(), true);
  assert.strictEqual(session.noteUserScroll(), false, 'pin session survives scrolling');
  assert.strictEqual(session.ownsPin(), true);
}

function testTypingInThePinArmsThePanHold() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'pin');
  assert.strictEqual(session.pinKeepsPan(), true, 'the keyboard may pan the pair back');
}

function testScrollReleasesThePanHoldButKeepsTheSession() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'pin');
  session.noteUserScroll();
  assert.strictEqual(session.ownsPin(), true, 'the user is still typing in the pin');
  assert.strictEqual(
    session.pinKeepsPan(), false,
    'after a gesture the geometry decides, so the pair is never shown twice'
  );
}

function testProgrammaticPinFocusDoesNotRearmThePanHold() {
  // Каждый проход видимости зовёт setOwner('pin'); если бы он возвращал hold,
  // инерционный скролл (touchend уже был) снова прилипал бы к пину.
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'pin');
  session.noteUserScroll();
  session.setOwner('pin');
  assert.strictEqual(session.pinKeepsPan(), false);
}

function testPanHoldIsDroppedWhenTheRealFieldTakesOver() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'pin');
  session.setOwner('real');
  assert.strictEqual(session.pinKeepsPan(), false);
  session.begin('7', 3, 'real');
  assert.strictEqual(session.pinKeepsPan(), false);
}

function testSessionRemembersTheDirectionOfTravel() {
  var clock = fakeClock();
  var session = makeSession(clock);
  assert.strictEqual(session.direction, 'down', 'лесенка по умолчанию решается сверху');
  session.noteDirection('up');
  assert.strictEqual(session.direction, 'up');
  session.noteDirection('sideways');
  assert.strictEqual(session.direction, 'up', 'мусор направление не сбрасывает');
  // Направление живёт дольше сессии: перерисовка после закрытой клавиатуры
  // всё равно должна знать, в какую сторону идёт пользователь.
  session.noteKeyboardClosed();
  assert.strictEqual(session.direction, 'up');
}

function testKeyboardCloseDropsThePanHold() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'pin');
  session.noteKeyboardClosed();
  assert.strictEqual(session.pinKeepsPan(), false);
}

function testPageScrollEndsARealFieldSession() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  assert.strictEqual(session.noteUserScroll(), true);
  assert.strictEqual(session.isActive('7'), false);
}

function testAnchorMovesOnAdvanceWithoutReopeningASession() {
  var clock = fakeClock();
  var session = makeSession(clock);
  session.begin('7', 3, 'real');
  session.noteKeyboardClosed();
  session.moveAnchor('7', 4);
  assert.deepStrictEqual(session.anchor, { taskId: '7', wordIndex: '4' });
  assert.strictEqual(session.isActive('7'), false, 'advance alone is not a session');
}

function testPointerDownWindow() {
  var clock = fakeClock();
  var session = makeSession(clock);
  var target = {};
  session.notePointerDown(target);
  assert.strictEqual(session.recentPointerDown(700).target, target);
  clock.advance(800);
  assert.strictEqual(session.recentPointerDown(700), null);
}

// ── Effect gate ───────────────────────────────────────────────────────────
function testPassiveViewportHandlersMayNotFocusOrScroll() {
  ['viewport.resize', 'viewport.scroll', 'page.scroll', 'keyboard.closed'].forEach(function (src) {
    assert.strictEqual(S.mayApplyEffect(src, 'moveFocus', { sessionActive: true }), false, src);
    assert.strictEqual(S.mayApplyEffect(src, 'restoreFocus', { sessionActive: true }), false, src);
    assert.strictEqual(S.mayApplyEffect(src, 'scroll', { sessionActive: true }), false, src);
    assert.strictEqual(S.mayApplyEffect(src, 'rebuildPin', {}), false, src);
  });
}

function testFocusRequiresAnActiveSession() {
  assert.strictEqual(S.mayApplyEffect('user.pointer', 'moveFocus', { sessionActive: true }), true);
  assert.strictEqual(S.mayApplyEffect('user.pointer', 'moveFocus', { sessionActive: false }), false);
  assert.strictEqual(S.mayApplyEffect('server.advance', 'moveFocus', { sessionActive: true }), true);
  assert.strictEqual(S.mayApplyEffect('server.feedback', 'moveFocus', { sessionActive: true }), false);
}

function testLiveUpdateRestoresButNeverMovesFocus() {
  assert.strictEqual(S.mayApplyEffect('live.update', 'moveFocus', { sessionActive: true }), false);
  assert.strictEqual(S.mayApplyEffect('live.update', 'restoreFocus', { sessionActive: true }), true);
  assert.strictEqual(S.mayApplyEffect('live.update', 'restoreFocus', { sessionActive: false }), false);
  assert.strictEqual(S.mayApplyEffect('live.update', 'scroll', {}), false);
  assert.strictEqual(S.mayApplyEffect('live.update', 'rebuildPin', {}), true);
}

function testWrongAnswerFeedbackTouchesNothing() {
  assert.strictEqual(S.mayApplyEffect('server.feedback', 'rebuildPin', {}), false);
  assert.strictEqual(S.mayApplyEffect('server.feedback', 'scroll', {}), false);
}

function testUnknownSourceIsDenied() {
  assert.strictEqual(S.mayApplyEffect('mystery', 'moveFocus', { sessionActive: true }), false);
}

// ── Focus transfer real ↔ pin ─────────────────────────────────────────────
function testFocusMovesIntoThePinWhileItIsVisible() {
  assert.strictEqual(S.decideInputTarget({
    pinVisible: true, sessionActive: true, focusInPin: false,
  }), 'pin');
}

function testVisiblePinDoesNotStealFocusWithoutASession() {
  assert.strictEqual(S.decideInputTarget({
    pinVisible: true, sessionActive: false, focusInPin: false,
  }), 'keep');
}

function testFocusReturnsToTheRealFieldWhenThePairIsVisibleAgain() {
  assert.strictEqual(S.decideInputTarget({
    pinVisible: false, sessionActive: true, focusInPin: true, realVisible: true,
  }), 'real');
}

function testFocusIsDroppedWhenTheRealFieldIsOffScreen() {
  assert.strictEqual(S.decideInputTarget({
    pinVisible: false, sessionActive: true, focusInPin: true, realVisible: false,
  }), 'blur');
}

function testFocusIsDroppedWhenTheUserFinishedTyping() {
  assert.strictEqual(S.decideInputTarget({
    pinVisible: false, sessionActive: false, focusInPin: true, realVisible: true,
  }), 'blur');
}

function testHiddenPinWithoutPinFocusLeavesTheDomAlone() {
  assert.strictEqual(S.decideInputTarget({
    pinVisible: false, sessionActive: true, focusInPin: false, realVisible: true,
  }), 'keep');
}

// ── Where the top of the screen actually begins ───────────────────────────
function testNavOffsetIsTheHeaderBottomWhenNothingIsPanned() {
  assert.strictEqual(S.visibleNavOffset(56, 0), 56);
}

function testKeyboardPanShrinksTheReservedHeaderStrip() {
  // Клавиатура «панит» страницу на 20px: от шапки видно только 36px.
  assert.strictEqual(S.visibleNavOffset(56, 20), 36);
}

function testHeaderPannedOffScreenReservesNothing() {
  // Шапка уехала выше видимой области — пин должен встать в самый верх экрана,
  // а не висеть под пустой полосой там, где меню уже нет.
  assert.strictEqual(S.visibleNavOffset(56, 300), 0);
}

function testNavOffsetWithoutGeometryIsUnknown() {
  assert.strictEqual(S.visibleNavOffset(null, 0), null, 'нет замера — пусть решает CSS-константа');
  assert.strictEqual(S.visibleNavOffset(56, null), 56);
}

// ── Advance onto an unplayable step ───────────────────────────────────────
function testGoingUpFromTheBottomNeverLandsOnTheTopOfTheLadder() {
  // Идём снизу вверх; напарник уже взял 5 и 4, играбельны только 1 и 6.
  assert.strictEqual(S.pickNearestPlayable([1, 6], 5, 'up'), 1);
  // Тот же набор при движении вниз — ближайшая снизу.
  assert.strictEqual(S.pickNearestPlayable([1, 6], 5, 'down'), 6);
}

function testNearestPlayablePrefersTheDirectionOfTravel() {
  assert.strictEqual(S.pickNearestPlayable([2, 3, 7, 8], 5, 'up'), 3);
  assert.strictEqual(S.pickNearestPlayable([2, 3, 7, 8], 5, 'down'), 7);
}

function testNearestPlayableTakesTheRequestedStepWhenItIsPlayable() {
  assert.strictEqual(S.pickNearestPlayable([2, 5, 8], 5, 'up'), 5);
  assert.strictEqual(S.pickNearestPlayable([2, 5, 8], 5, 'down'), 5);
}

function testNearestPlayableFallsBackAcrossTheDirectionWhenNothingIsThatWay() {
  // Вверх играбельных нет — берём то, что есть, вместо «ничего».
  assert.strictEqual(S.pickNearestPlayable([7, 9], 5, 'up'), 7);
  assert.strictEqual(S.pickNearestPlayable([1, 3], 5, 'down'), 3);
}

function testNearestPlayableWithoutAnOriginKeepsTheLadderOrder() {
  assert.strictEqual(S.pickNearestPlayable([4, 2, 9], null, 'down'), 2);
  assert.strictEqual(S.pickNearestPlayable([], 5, 'down'), null);
}

function testNearestPlayableIgnoresJunkIndices() {
  assert.strictEqual(S.pickNearestPlayable(['3', null, 'x', '8'], 5, 'up'), 3);
}

// ── Live update vs local drafts ───────────────────────────────────────────
function testRemoteDraftDoesNotOverwriteTheFieldBeingTypedIn() {
  assert.strictEqual(S.shouldApplyRemoteDraft({
    fromRemote: true, locallyEditing: true, currentLetters: 'КА', remoteLetters: 'КАНАВА',
  }), false);
}

function testRemoteDraftYieldsToAQueuedLocalValue() {
  assert.strictEqual(S.shouldApplyRemoteDraft({
    fromRemote: true, hasPendingLocal: true, currentLetters: 'КА', remoteLetters: 'КАНАВА',
  }), false);
}

function testRemoteDraftAppliesToAnIdleField() {
  assert.strictEqual(S.shouldApplyRemoteDraft({
    fromRemote: true, currentLetters: '', remoteLetters: 'КАНАВА',
  }), true);
}

function testRemoteDraftIsANoopWhenEqualOrSolved() {
  assert.strictEqual(S.shouldApplyRemoteDraft({
    fromRemote: true, currentLetters: 'КАНАВА', remoteLetters: 'КАНАВА',
  }), false);
  assert.strictEqual(S.shouldApplyRemoteDraft({
    fromRemote: true, rowSolved: true, currentLetters: '', remoteLetters: 'КАНАВА',
  }), false);
}

function testOwnEchoAppliesEvenWhileEditing() {
  assert.strictEqual(S.shouldApplyRemoteDraft({
    fromRemote: false, locallyEditing: true, currentLetters: '', remoteLetters: 'КАНАВА',
  }), true);
}

// ── Cascade ───────────────────────────────────────────────────────────────
function testCascadeOnlyInNormalMode() {
  var base = { hasAuto: true, filled: true, sessionActive: true };
  assert.strictEqual(S.shouldCascadeSubmit(Object.assign({}, base, { isTournament: false })), true);
  assert.strictEqual(S.shouldCascadeSubmit(Object.assign({}, base, { isTournament: true })), false);
  assert.strictEqual(S.shouldCascadeSubmit({ hasAuto: false, filled: true, sessionActive: true }), false);
  assert.strictEqual(S.shouldCascadeSubmit({ hasAuto: true, filled: false, sessionActive: true }), false);
}

testResponseMatrixCoversEveryScenario();
testAdvanceImpliesReplaceHtml();
testUpdateTaskHtmlIsNotAProgressSignal();
testStatusOkAloneIsNotProgress();
testStaleUiWinsOverNeedsSync();
testPartialProgressStatusIsNotCorrect();
testWrongKeepsTheSubmitLock();
testBadAndUnknownResponses();
testWordIndexIsZeroBasedFromTheRequest();
testAbortIsNeverRetried();
testOfflineRetriesUpToThreeAttempts();
testHtml403IsNotRetriedAndNotMaskedAsNetwork();
testInFlightDeduplication();
testInFlightIsPerTaskAndWord();
testPasteAndInputDeduplication();
testSubmitLockSurvivesRerender();
testSubmitLockResetPaths();
testSessionNeedsAnExplicitUserAction();
testAndroidBackClosesKeyboardButKeepsAnchor();
testAsyncResponseAfterKeyboardDismissalMayNotFocus();
testExplicitTapOnAnotherRowSwitchesAnchor();
testTapOnEmptySpaceKeepsThePair();
testLadderScrollKeepsThePinSession();
testTypingInThePinArmsThePanHold();
testScrollReleasesThePanHoldButKeepsTheSession();
testProgrammaticPinFocusDoesNotRearmThePanHold();
testPanHoldIsDroppedWhenTheRealFieldTakesOver();
testSessionRemembersTheDirectionOfTravel();
testKeyboardCloseDropsThePanHold();
testPageScrollEndsARealFieldSession();
testAnchorMovesOnAdvanceWithoutReopeningASession();
testPointerDownWindow();
testPassiveViewportHandlersMayNotFocusOrScroll();
testFocusRequiresAnActiveSession();
testLiveUpdateRestoresButNeverMovesFocus();
testWrongAnswerFeedbackTouchesNothing();
testUnknownSourceIsDenied();
testFocusMovesIntoThePinWhileItIsVisible();
testVisiblePinDoesNotStealFocusWithoutASession();
testFocusReturnsToTheRealFieldWhenThePairIsVisibleAgain();
testFocusIsDroppedWhenTheRealFieldIsOffScreen();
testFocusIsDroppedWhenTheUserFinishedTyping();
testHiddenPinWithoutPinFocusLeavesTheDomAlone();
testNavOffsetIsTheHeaderBottomWhenNothingIsPanned();
testKeyboardPanShrinksTheReservedHeaderStrip();
testHeaderPannedOffScreenReservesNothing();
testNavOffsetWithoutGeometryIsUnknown();
testGoingUpFromTheBottomNeverLandsOnTheTopOfTheLadder();
testNearestPlayablePrefersTheDirectionOfTravel();
testNearestPlayableTakesTheRequestedStepWhenItIsPlayable();
testNearestPlayableFallsBackAcrossTheDirectionWhenNothingIsThatWay();
testNearestPlayableWithoutAnOriginKeepsTheLadderOrder();
testNearestPlayableIgnoresJunkIndices();
testRemoteDraftDoesNotOverwriteTheFieldBeingTypedIn();
testRemoteDraftYieldsToAQueuedLocalValue();
testRemoteDraftAppliesToAnIdleField();
testRemoteDraftIsANoopWhenEqualOrSolved();
testOwnEchoAppliesEvenWhileEditing();
testCascadeOnlyInNormalMode();
console.log('raddle_state.test.js: ok');
