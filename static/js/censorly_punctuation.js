(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.CensorlyPunctuation = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var OPENING = '([{«‹“‘„‚〈《「『【〔〖〘〚¿¡';
  var CLOSING = ')]}>»›”’〉》」』】〕〗〙.,!?;:…';
  var AMBIGUOUS_QUOTES = '"\'';

  function attachments(text, hasPreviousWord, hasNextWord) {
    var chars = Array.from(String(text || ''));
    if (!chars.length) return {before: false, after: false};

    if (chars.length === 1 && AMBIGUOUS_QUOTES.indexOf(chars[0]) !== -1) {
      if (hasPreviousWord) return {before: true, after: false};
      return {before: false, after: !!hasNextWord};
    }

    var first = chars[0];
    var last = chars[chars.length - 1];
    var opening = OPENING.indexOf(first) !== -1 || OPENING.indexOf(last) !== -1;
    var closing = CLOSING.indexOf(first) !== -1 || CLOSING.indexOf(last) !== -1;

    return {
      before: closing || (!opening && !!hasPreviousWord),
      after: opening && !!hasNextWord,
    };
  }

  return {attachments: attachments};
}));
