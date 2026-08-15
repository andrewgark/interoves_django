/**
 * Small state machine for mobile inputs whose DOM/position changes while typing.
 * It deliberately does not use document.activeElement: Android keeps inputs
 * focused after Back has dismissed the soft keyboard.
 *
 * Local check: node static/js/mobile_input_intent.test.js
 */
(function (global) {
  'use strict';

  function finiteNumber(value) {
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function InputIntent(options) {
    options = options || {};
    this.durationMs = Math.max(0, finiteNumber(options.durationMs) || 0);
    this.keyboardCloseDelta = Math.max(
      1,
      finiteNumber(options.keyboardCloseDelta) || 120
    );
    this.viewportWidthResetDelta = Math.max(
      1,
      finiteNumber(options.viewportWidthResetDelta) || 40
    );
    this.now = typeof options.now === 'function' ? options.now : Date.now;
    this.context = '';
    this.expiresAt = 0;
    this.viewportWidth = null;
    this.viewportHeight = null;
    this.minViewportHeight = null;
  }

  InputIntent.prototype.activate = function (context) {
    this.context = String(context == null ? '' : context);
    this.expiresAt = this.durationMs ? this.now() + this.durationMs : 0;
    this.minViewportHeight = this.viewportHeight;
  };

  InputIntent.prototype.clear = function () {
    this.context = '';
    this.expiresAt = 0;
  };

  InputIntent.prototype.isActive = function (context) {
    if (!this.context) return false;
    if (this.expiresAt && this.now() > this.expiresAt) {
      this.clear();
      return false;
    }
    if (context === undefined || context === null) return true;
    return this.context === String(context);
  };

  InputIntent.prototype.observeViewport = function (width, height) {
    var nextWidth = finiteNumber(width);
    var nextHeight = finiteNumber(height);
    if (nextWidth === null || nextHeight === null) return false;

    var widthChanged = this.viewportWidth !== null &&
      Math.abs(nextWidth - this.viewportWidth) >= this.viewportWidthResetDelta;
    var active = this.isActive();
    if (active && this.minViewportHeight !== null) {
      this.minViewportHeight = Math.min(this.minViewportHeight, nextHeight);
    } else {
      this.minViewportHeight = nextHeight;
    }
    var keyboardClosed = active && !widthChanged &&
      nextHeight - this.minViewportHeight >= this.keyboardCloseDelta;

    this.viewportWidth = nextWidth;
    this.viewportHeight = nextHeight;
    if (widthChanged || keyboardClosed) {
      this.clear();
      this.minViewportHeight = nextHeight;
    }
    return keyboardClosed;
  };

  global.MobileInputIntent = {
    create: function (options) { return new InputIntent(options); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
