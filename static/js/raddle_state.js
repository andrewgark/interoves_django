/**
 * Raddle (лесенка) client state machine.
 *
 * Central owner of everything the DOM layer used to infer from
 * document.activeElement / scattered data-attributes:
 *
 *   - the server response matrix (games/raddle_response_contract.py);
 *   - request de-duplication that survives applyNewUiTaskHtml();
 *   - the mobile input session (owner real|pin, keyboard, anchor, dismiss);
 *   - which effects (focus / scroll / pin rebuild) each event source may apply.
 *
 * Deliberately DOM-free so node can test the decisions.
 * Local check: node static/js/raddle_state.test.js
 */
(function (global) {
  'use strict';

  function isTrue(value) {
    return value === true || value === 1 || value === '1';
  }

  function finite(value, fallback) {
    var n = Number(value);
    return isFinite(n) ? n : fallback;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Server response matrix
  //
  // Mirror of RADDLE_RESPONSE_SCENARIOS in games/raddle_response_contract.py
  // (games.tests.test_raddle_response_contract keeps the two in sync).
  //
  // The presence of update_task_html_new, `status: ok` on its own, and
  // solved_indices are NOT inputs here: only explicit progress flags are.
  // ─────────────────────────────────────────────────────────────────────────
  var RESPONSE_MATRIX = [
    {
      id: 'correct',
      match: function (d) {
        return d.status === 'ok' && isTrue(d.raddle_correct);
      },
      effects: {
        replace_html: true,
        advance_focus: true,
        mark_wrong: false,
        keep_input: false,
      },
      messageKey: null,
      clearMessage: true,
      releaseSubmitLock: true,
    },
    {
      id: 'stale_ui',
      match: function (d) {
        return d.status === 'ok' && isTrue(d.raddle_needs_sync) && isTrue(d.raddle_stale_ui);
      },
      effects: {
        replace_html: true,
        advance_focus: false,
        mark_wrong: false,
        keep_input: false,
      },
      messageKey: null,
      clearMessage: true,
      releaseSubmitLock: true,
    },
    {
      id: 'needs_sync',
      match: function (d) {
        return d.status === 'ok' && isTrue(d.raddle_needs_sync);
      },
      effects: {
        replace_html: true,
        advance_focus: true,
        mark_wrong: false,
        keep_input: false,
      },
      messageKey: null,
      clearMessage: true,
      releaseSubmitLock: true,
    },
    {
      id: 'duplicate_solved',
      match: function (d) {
        return d.status === 'duplicate' && isTrue(d.raddle_duplicate_solved);
      },
      effects: {
        replace_html: true,
        advance_focus: true,
        mark_wrong: false,
        keep_input: false,
      },
      messageKey: null,
      clearMessage: true,
      releaseSubmitLock: true,
    },
    {
      id: 'duplicate_unsolved',
      match: function (d) {
        return d.status === 'duplicate';
      },
      effects: {
        replace_html: false,
        advance_focus: false,
        mark_wrong: true,
        keep_input: true,
      },
      messageKey: 'duplicate',
      clearMessage: false,
      releaseSubmitLock: true,
    },
    {
      id: 'wrong',
      match: function (d) {
        return d.status === 'ok';
      },
      effects: {
        replace_html: false,
        advance_focus: false,
        mark_wrong: true,
        keep_input: true,
      },
      messageKey: null,
      clearMessage: false,
      // A wrong answer keeps the value in the field, so the same value must not
      // be auto-resent; only an edit or an explicit retry releases the lock.
      releaseSubmitLock: false,
    },
  ];

  var RESPONSE_MATRIX_IDS = RESPONSE_MATRIX.map(function (row) { return row.id; });

  function verdictFrom(row) {
    return {
      id: row.id,
      replaceHtml: row.effects.replace_html,
      advanceFocus: row.effects.advance_focus,
      markWrong: row.effects.mark_wrong,
      keepInput: row.effects.keep_input,
      clearMessage: row.clearMessage,
      messageKey: row.messageKey,
      releaseSubmitLock: row.releaseSubmitLock,
    };
  }

  /**
   * Server response → allowed client effects. Never looks at
   * update_task_html_new: the caller decides how to obtain fresh HTML once
   * replaceHtml is granted.
   */
  function classifyAttemptResponse(data) {
    if (!data || typeof data !== 'object' || !data.status) {
      return {
        id: 'bad_response',
        replaceHtml: false,
        advanceFocus: false,
        markWrong: false,
        keepInput: true,
        clearMessage: false,
        messageKey: 'bad_response',
        releaseSubmitLock: true,
      };
    }
    for (var i = 0; i < RESPONSE_MATRIX.length; i++) {
      if (RESPONSE_MATRIX[i].match(data)) return verdictFrom(RESPONSE_MATRIX[i]);
    }
    return {
      id: 'server_error',
      replaceHtml: false,
      advanceFocus: false,
      markWrong: false,
      keepInput: true,
      clearMessage: false,
      messageKey: 'status',
      releaseSubmitLock: true,
    };
  }

  /**
   * raddle_word_index is always the 0-based index of the word from the request.
   */
  function resolveWordIndex(data, fallback) {
    if (data && data.raddle_word_index !== undefined && data.raddle_word_index !== null) {
      var n = Number(data.raddle_word_index);
      if (isFinite(n)) return n;
    }
    if (fallback === null || fallback === undefined || fallback === '') return null;
    var f = Number(fallback);
    return isFinite(f) ? f : null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Request failure policy
  // ─────────────────────────────────────────────────────────────────────────
  function isAbortError(err) {
    return !!(err && (err.name === 'AbortError' || err.code === 20));
  }

  /**
   * Abort/timeout is never retried: the server may already have recorded the
   * move and a retry fights the teammate's select_for_update. An HTML 403 is
   * not a network problem and must not be reported as one.
   */
  function classifyRequestFailure(err, attempt, maxAttempts) {
    var at = finite(attempt, 1);
    var max = finite(maxAttempts, 1);
    if (isAbortError(err)) {
      return { retry: false, reason: 'abort', messageKey: 'network' };
    }
    if (err && err.httpStatus === 403) {
      return { retry: false, reason: 'forbidden', messageKey: 'stale_session' };
    }
    if (err && err.retryable === false) {
      return { retry: false, reason: 'not_retryable', messageKey: 'network' };
    }
    if (at < max) {
      return { retry: true, reason: 'offline', messageKey: null };
    }
    return { retry: false, reason: 'exhausted', messageKey: 'network' };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Request registry
  //
  // Lives in JS memory, not on DOM nodes, so both the in-flight lock and the
  // "same value already sent" lock survive applyNewUiTaskHtml().
  // ─────────────────────────────────────────────────────────────────────────
  function createRequestRegistry() {
    var inFlight = Object.create(null);
    var submitted = Object.create(null);

    function key(taskId, wordIndex) {
      if (taskId === null || taskId === undefined || taskId === '') return '';
      if (wordIndex === null || wordIndex === undefined || wordIndex === '') return '';
      return String(taskId) + ':' + String(wordIndex);
    }

    return {
      key: key,
      isInFlight: function (k) {
        return !!(k && inFlight[k]);
      },
      /** Reserve the slot. false when a request for this word is already out. */
      begin: function (k) {
        if (!k) return false;
        if (inFlight[k]) return false;
        inFlight[k] = true;
        return true;
      },
      end: function (k) {
        if (k) delete inFlight[k];
      },
      /** Called only once a fetch has actually been issued. */
      markSubmitted: function (k, value) {
        if (!k) return;
        submitted[k] = String(value == null ? '' : value);
      },
      submittedValue: function (k) {
        return k && Object.prototype.hasOwnProperty.call(submitted, k) ? submitted[k] : null;
      },
      isDuplicateSubmit: function (k, value) {
        if (!k) return false;
        if (!Object.prototype.hasOwnProperty.call(submitted, k)) return false;
        return submitted[k] === String(value == null ? '' : value);
      },
      clearSubmitted: function (k) {
        if (k) delete submitted[k];
      },
      reset: function () {
        inFlight = Object.create(null);
        submitted = Object.create(null);
      },
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Mobile input session
  //
  // Native focus is not a session: Android keeps an input focused after Back
  // dismisses the soft keyboard. A session only exists after an explicit user
  // action and ends on keyboard close, page gesture, or a tap on empty space.
  // ─────────────────────────────────────────────────────────────────────────
  var POINTER_WINDOW_MS = 700;
  var KEYBOARD_CLOSE_WINDOW_MS = 700;
  var DISMISS_WINDOW_MS = 1500;

  function fallbackIntent() {
    return {
      context: '',
      activate: function (context) { this.context = String(context == null ? '' : context); },
      clear: function () { this.context = ''; },
      isActive: function (context) {
        if (!this.context) return false;
        if (context === undefined || context === null) return true;
        return this.context === String(context);
      },
      observeViewport: function () { return false; },
    };
  }

  function createInputSession(opts) {
    opts = opts || {};
    var intent = opts.intent || fallbackIntent();
    var now = typeof opts.now === 'function' ? opts.now : Date.now;
    var owner = null;
    var keyboard = 'unknown';
    var dismissSession = false;
    var keyboardClosedAt = 0;
    var anchor = null;
    var lastPointerDown = null;
    var panHold = false;
    var direction = 'down';

    function isActive(taskId) {
      return taskId === undefined || taskId === null
        ? !!intent.isActive()
        : !!intent.isActive(String(taskId));
    }

    return {
      get owner() { return owner; },
      get keyboardState() { return keyboard; },
      get anchor() { return anchor; },
      /** Which way along the ladder the user is working: 'up' | 'down'. */
      get direction() { return direction; },
      noteDirection: function (next) {
        if (next === 'up' || next === 'down') direction = next;
      },

      /** Explicit user action: tap on a row/input, typing, Tab, Enter. */
      begin: function (taskId, wordIndex, nextOwner) {
        if (taskId === null || taskId === undefined || taskId === '') return;
        intent.activate(String(taskId));
        owner = nextOwner === 'pin' ? 'pin' : 'real';
        // Only an explicit action arms the pan hold. Re-arming it from the
        // programmatic focus of a visibility pass would let momentum scrolling
        // resurrect a hold the user's gesture had just released.
        panHold = owner === 'pin';
        dismissSession = false;
        if (wordIndex !== null && wordIndex !== undefined && wordIndex !== '') {
          anchor = { taskId: String(taskId), wordIndex: String(wordIndex) };
        }
      },
      /** The input moved between the real row and the sticky-pin proxy. */
      setOwner: function (nextOwner) {
        if (nextOwner !== 'pin' && nextOwner !== 'real') return;
        owner = nextOwner;
        if (owner === 'real') panHold = false;
      },
      /** Anchor follows a confirmed advance without re-opening a session. */
      moveAnchor: function (taskId, wordIndex) {
        if (taskId === null || taskId === undefined || taskId === '') return;
        if (wordIndex === null || wordIndex === undefined || wordIndex === '') return;
        anchor = { taskId: String(taskId), wordIndex: String(wordIndex) };
      },
      /** beforeinput proves a keyboard (soft or hardware) is delivering keys. */
      noteInput: function () {
        keyboard = 'open';
      },
      noteKeyboardClosed: function () {
        keyboard = 'closed';
        dismissSession = true;
        keyboardClosedAt = now();
        owner = null;
        panHold = false;
        intent.clear();
      },
      /**
       * A deliberate page gesture ends the session so a late async response
       * cannot pull the user back into an input — unless the pin owns the
       * input, where scrolling the ladder is normal reading. Even then the
       * gesture releases the pan hold: from here on the pin's fate is decided
       * by where the pair actually is, so the user cannot end up looking at
       * the pin and the real pair at the same time.
       */
      noteUserScroll: function () {
        panHold = false;
        if (owner === 'pin' && isActive()) return false;
        intent.clear();
        owner = null;
        return true;
      },
      /** Tap on empty space: keyboard goes away, the current pair does not. */
      end: function () {
        intent.clear();
        owner = null;
        panHold = false;
      },
      notePointerDown: function (target) {
        lastPointerDown = { target: target || null, at: now() };
      },
      recentPointerDown: function (windowMs) {
        if (!lastPointerDown) return null;
        var span = finite(windowMs, POINTER_WINDOW_MS);
        return (now() - lastPointerDown.at > span) ? null : lastPointerDown;
      },
      keyboardClosedRecently: function (windowMs) {
        if (!keyboardClosedAt) return false;
        return (now() - keyboardClosedAt) < finite(windowMs, KEYBOARD_CLOSE_WINDOW_MS);
      },
      /**
       * True while a just-dismissed keyboard can still hand focus to another
       * word (Back fires before visualViewport reports the close). Time-bounded
       * so it cannot block a legitimate focus move forever.
       */
      dismissSessionActive: function (windowMs) {
        if (!dismissSession) return false;
        return (now() - keyboardClosedAt) < finite(windowMs, DISMISS_WINDOW_MS);
      },
      isActive: isActive,
      /** The one question the pin controller may ask — never activeElement. */
      ownsPin: function () {
        return owner === 'pin' && isActive();
      },
      /**
       * True while the pin may stay even though the pair is back on screen.
       * That happens when the soft keyboard pans the visual viewport under the
       * user mid-typing: hiding the pin there would hand focus back and jump
       * the page. Released by the first deliberate scroll gesture.
       */
      pinKeepsPan: function () {
        return panHold && owner === 'pin' && isActive();
      },
      snapshot: function () {
        return {
          owner: owner,
          keyboardState: keyboard,
          dismissSession: dismissSession,
          panHold: panHold,
          direction: direction,
          anchor: anchor ? { taskId: anchor.taskId, wordIndex: anchor.wordIndex } : null,
          active: isActive(),
        };
      },
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Effect gate
  //
  // Passive viewport/scroll handlers must never focus or scroll: only an
  // explicit user action or a confirmed server advance may.
  // ─────────────────────────────────────────────────────────────────────────
  var SOURCE_EFFECTS = {
    'init':            { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: true },
    'user.pointer':    { moveFocus: true,  restoreFocus: true,  scroll: true,  rebuildPin: true },
    'user.key':        { moveFocus: true,  restoreFocus: true,  scroll: true,  rebuildPin: true },
    'user.tab':        { moveFocus: true,  restoreFocus: true,  scroll: false, rebuildPin: true },
    'user.input':      { moveFocus: false, restoreFocus: true,  scroll: false, rebuildPin: false },
    'server.advance':  { moveFocus: true,  restoreFocus: true,  scroll: false, rebuildPin: true },
    'server.feedback': { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: false },
    'live.update':     { moveFocus: false, restoreFocus: true,  scroll: false, rebuildPin: true },
    'viewport.resize': { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: false },
    'viewport.scroll': { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: false },
    'page.scroll':     { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: false },
    'keyboard.closed': { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: false },
    'pause':           { moveFocus: false, restoreFocus: false, scroll: false, rebuildPin: false },
  };

  function mayApplyEffect(source, effect, ctx) {
    var row = SOURCE_EFFECTS[source];
    if (!row) return false;
    if (!row[effect]) return false;
    if (effect === 'moveFocus' || effect === 'restoreFocus') {
      return !!(ctx && ctx.sessionActive);
    }
    return true;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Focus target
  // ─────────────────────────────────────────────────────────────────────────
  /**
   * Which playable step the caret goes to when the requested one is not
   * playable — a teammate solved it, or it is a middle draft row.
   *
   * Prefers the direction the user was travelling, then proximity. "The first
   * playable row in the ladder" is never an answer: solving upwards from the
   * bottom, that throws the caret to the very top of the ladder.
   *
   * @param indices   playable word indices, any order
   * @param fromIndex the requested (unplayable) step, or null
   * @param direction 'up' when moving towards index 0, otherwise 'down'
   */
  function pickNearestPlayable(indices, fromIndex, direction) {
    var pool = [];
    (indices || []).forEach(function (value) {
      var n = Number(value);
      if (isFinite(n)) pool.push(n);
    });
    if (!pool.length) return null;
    var from = Number(fromIndex);
    if (fromIndex === null || fromIndex === undefined || fromIndex === '' || !isFinite(from)) {
      // No origin to be near: keep the ladder's own order.
      return pool.reduce(function (a, b) { return b < a ? b : a; });
    }
    var up = direction === 'up';
    var best = null;
    pool.forEach(function (idx) {
      var toward = up ? idx <= from : idx >= from;
      var dist = Math.abs(idx - from);
      if (!best) {
        best = { idx: idx, toward: toward, dist: dist };
        return;
      }
      var better = toward !== best.toward ? toward : dist < best.dist;
      if (better) best = { idx: idx, toward: toward, dist: dist };
    });
    return best ? best.idx : null;
  }

  /**
   * Where the caret belongs after the pin appeared or disappeared.
   *   pin   — the proxy is the real input for the user
   *   real  — the pair came back on screen during an active session
   *   blur  — the field is off screen or the session is over
   *   keep  — nothing may touch native focus
   */
  function decideInputTarget(opts) {
    opts = opts || {};
    if (opts.pinVisible) {
      return opts.sessionActive ? 'pin' : 'keep';
    }
    if (!opts.focusInPin) return 'keep';
    if (opts.sessionActive && opts.realVisible) return 'real';
    return 'blur';
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Drafts / live update
  // ─────────────────────────────────────────────────────────────────────────
  /**
   * Local editing wins over remote state: a teammate's snapshot must not
   * overwrite the field the user is typing in, nor a value we have queued.
   */
  function shouldApplyRemoteDraft(opts) {
    opts = opts || {};
    if (opts.rowSolved) return false;
    var remote = String(opts.remoteLetters == null ? '' : opts.remoteLetters);
    var current = String(opts.currentLetters == null ? '' : opts.currentLetters);
    if (remote === current) return false;
    if (!opts.fromRemote) return true;
    if (opts.locallyEditing) return false;
    if (opts.hasPendingLocal) return false;
    return true;
  }

  /**
   * Cascade after an advance: normal mode only, and only while the user's own
   * input session is still open (they may have closed the keyboard meanwhile).
   */
  function shouldCascadeSubmit(opts) {
    opts = opts || {};
    if (opts.isTournament) return false;
    if (!opts.hasAuto) return false;
    if (!opts.sessionActive) return false;
    return !!opts.filled;
  }

  global.RaddleState = {
    RESPONSE_MATRIX: RESPONSE_MATRIX,
    RESPONSE_MATRIX_IDS: RESPONSE_MATRIX_IDS,
    SOURCE_EFFECTS: SOURCE_EFFECTS,
    classifyAttemptResponse: classifyAttemptResponse,
    resolveWordIndex: resolveWordIndex,
    isAbortError: isAbortError,
    classifyRequestFailure: classifyRequestFailure,
    createRequestRegistry: createRequestRegistry,
    createInputSession: createInputSession,
    mayApplyEffect: mayApplyEffect,
    decideInputTarget: decideInputTarget,
    pickNearestPlayable: pickNearestPlayable,
    shouldApplyRemoteDraft: shouldApplyRemoteDraft,
    shouldCascadeSubmit: shouldCascadeSubmit,
  };
})(typeof window !== 'undefined' ? window : globalThis);
