/**
 * Parse fetch() bodies without treating HTML 403/502 as a generic TypeError.
 * AbortError is not auto-retried: the server request may still complete.
 *
 * Local check: node static/js/fetch_json.test.js
 */
(function (global) {
  'use strict';

  function isAbortError(err) {
    return !!(err && (err.name === 'AbortError' || err.code === 20));
  }

  function parseJsonResponse(response) {
    return Promise.resolve(response.text()).then(function (text) {
      var data = null;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (e) {
          var parseErr = new Error('non-json-response');
          parseErr.retryable = !response.status || response.status >= 500;
          parseErr.httpStatus = response.status;
          throw parseErr;
        }
      }
      if (!data || typeof data !== 'object') {
        var emptyErr = new Error('empty-response');
        emptyErr.retryable = !response.status || response.status >= 500;
        emptyErr.httpStatus = response.status;
        throw emptyErr;
      }
      return data;
    });
  }

  function shouldRetry(err, attempt, maxAttempts) {
    if (attempt >= maxAttempts) return false;
    if (isAbortError(err)) return false;
    if (err && err.retryable === false) return false;
    return true;
  }

  function liveElement(el, doc) {
    doc = doc || global.document;
    if (!el) return null;
    if (el.id && doc && typeof doc.getElementById === 'function') {
      var fresh = doc.getElementById(el.id);
      if (fresh) return fresh;
    }
    return el;
  }

  function wordRowIsSolved(doc, taskId, wordIndex) {
    if (!doc || typeof doc.querySelector !== 'function') return false;
    if (taskId === null || taskId === undefined || wordIndex === null || wordIndex === undefined) {
      return false;
    }
    var row = doc.querySelector(
      '.new-raddle-task[data-task-id="' + taskId + '"] ' +
      '.new-raddle-row[data-word-index="' + wordIndex + '"]'
    );
    return !!(row && row.classList && row.classList.contains('new-raddle-row--solved'));
  }

  global.InterovesFetchJson = {
    isAbortError: isAbortError,
    parseJsonResponse: parseJsonResponse,
    shouldRetry: shouldRetry,
    liveElement: liveElement,
    wordRowIsSolved: wordRowIsSolved,
  };
})(typeof window !== 'undefined' ? window : global);
