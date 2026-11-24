/**
 * Small, DOM-free helpers for reconciling Alphabetty guess responses.
 * Local check: node static/js/alphabetty_guess_response.test.js
 */
(function (global) {
  'use strict';

  function includesWord(words, word) {
    return Array.isArray(words) && words.indexOf(word) >= 0;
  }

  function shouldRecoverDuplicate(currentGuesses, responseGuesses, submittedWord) {
    return (
      !includesWord(currentGuesses, submittedWord) &&
      includesWord(responseGuesses, submittedWord)
    );
  }

  function isHtmlBody(text) {
    return /^\s*</.test(String(text || ''));
  }

  function transportErrorMessage() {
    return 'Не удалось проверить слово. Попробуйте ещё раз.';
  }

  // A request the network never answers must not hold the page: the guess
  // form ignores presses while one is in flight, so a stalled POST left
  // «Проверить» dead until a reload (bug #359, 2026-11-23, Chrome on macOS:
  // no guess reached the server for a minute, the reload fixed it).
  var REQUEST_TIMEOUT_MS = 15000;

  function fetchWithTimeout(fetchFn, url, init, timeoutMs) {
    var ms = timeoutMs || REQUEST_TIMEOUT_MS;
    var AbortCtl = global.AbortController;
    var controller = typeof AbortCtl === 'function' ? new AbortCtl() : null;
    var opts = {};
    Object.keys(init || {}).forEach(function (k) { opts[k] = init[k]; });
    if (controller) opts.signal = controller.signal;
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = global.setTimeout(function () {
        if (done) return;
        done = true;
        if (controller) {
          try { controller.abort(); } catch (e) {}
        }
        var err = new Error(transportErrorMessage());
        err.timedOut = true;
        reject(err);
      }, ms);
      Promise.resolve().then(function () { return fetchFn(url, opts); }).then(function (response) {
        if (done) return;
        done = true;
        global.clearTimeout(timer);
        resolve(response);
      }, function (error) {
        if (done) return;
        done = true;
        global.clearTimeout(timer);
        reject(error);
      });
    });
  }

  var api = {
    shouldRecoverDuplicate: shouldRecoverDuplicate,
    isHtmlBody: isHtmlBody,
    transportErrorMessage: transportErrorMessage,
    fetchWithTimeout: fetchWithTimeout,
    REQUEST_TIMEOUT_MS: REQUEST_TIMEOUT_MS,
  };

  global.AlphabettyGuessResponse = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
