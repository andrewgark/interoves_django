(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.CensorlyGuessFeedback = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function wordFrom(data, submittedWord) {
    return (data && data.guess_word) || submittedWord || '';
  }

  function feedback(data, submittedWord) {
    var word = wordFrom(data, submittedWord);
    if (data && data.status === 'duplicate') {
      return 'Слово «' + word + '» уже вводили';
    }
    if (data && (data.status === 'already_open' || data.status === 'initially_open')) {
      return 'Слово «' + word + '» уже открыто';
    }
    return (data && data.error) || 'Не удалось отправить';
  }

  function shouldClearInput(status) {
    return status === 'duplicate' || status === 'initially_open' || status === 'already_open';
  }

  return {
    feedback: feedback,
    shouldClearInput: shouldClearInput,
  };
}));
