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

  var api = {
    shouldRecoverDuplicate: shouldRecoverDuplicate,
  };

  global.AlphabettyGuessResponse = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
