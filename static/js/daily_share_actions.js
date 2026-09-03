'use strict';

(function (root) {
  var COPY_TEXT_LABEL = 'Скопировать результат';
  var COPY_TEXT_DONE = 'Результат скопирован';
  var COPY_IMAGE_LABEL = 'Скопировать картинку';
  var COPY_IMAGE_DONE = 'Картинка скопирована';
  var SHARE_LABEL = 'Поделиться';
  var IMAGE_COPY_UNSUPPORTED = 'Этот браузер не умеет копировать картинки';
  var IMAGE_COPY_FAILED = 'Не удалось скопировать картинку';
  var SHARE_FAILED = 'Не удалось поделиться';
  var SHARE_UNAVAILABLE = 'Не удалось поделиться — результат скопирован текстом';

  function shareCardApi() {
    return root.DailyShareCard || globalThis.DailyShareCard;
  }

  function analytics() {
    return root.interovesAnalytics || (root.window && root.window.interovesAnalytics) || null;
  }

  function track(goal, params) {
    var helper = analytics();
    if (!helper) return;
    try {
      if (typeof helper.trackYandexGoalOnce === 'function') {
        helper.trackYandexGoalOnce(goal + ':' + Date.now() + ':' + Math.random(), goal, params || {});
        return;
      }
      if (typeof helper.trackYandexGoal === 'function') {
        helper.trackYandexGoal(goal, params || {});
      }
    } catch (err) {}
  }

  function analyticsParams(block) {
    var payload = readPayload(block) || {};
    var params = {};
    if (payload.game_kind || payload.kind) params.game_kind = payload.game_kind || payload.kind;
    if (payload.number) params.daily_number = String(payload.number);
    if (payload.locale) params.locale = payload.locale;
    return params;
  }

  function readPayload(block) {
    if (!block) return null;
    var raw = block.getAttribute('data-share-card');
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (err) {
      return null;
    }
  }

  function shareTextFromBlock(block) {
    var el = block && block.querySelector('[data-raddle-share-text]');
    if (!el) return '';
    var lines = [];
    var nodes = el.querySelectorAll('div');
    var i;
    for (i = 0; i < nodes.length; i += 1) {
      var t = (nodes[i].textContent || '').replace(/\s+$/g, '').replace(/^\s+/, '');
      if (t) lines.push(t);
    }
    if (lines.length) return lines.join('\n');
    return ((el.innerText || el.textContent || '') + '').trim();
  }

  function setStatus(block, message) {
    var live = block && block.querySelector('[data-share-status]');
    if (live) live.textContent = message || '';
  }

  function flashButton(btn, doneLabel, restoreLabel) {
    if (!btn) return;
    var original = restoreLabel || btn.getAttribute('aria-label') || '';
    btn.classList.add('is-copied');
    if (doneLabel) {
      btn.setAttribute('aria-label', doneLabel);
      btn.setAttribute('title', doneLabel);
    }
    setTimeout(function () {
      btn.classList.remove('is-copied');
      if (original) {
        btn.setAttribute('aria-label', original);
        btn.setAttribute('title', original);
      }
    }, 1600);
  }

  function fallbackCopyText(text) {
    if (typeof document === 'undefined') return false;
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'absolute';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return !!ok;
    } catch (err) {
      return false;
    }
  }

  function copyTextToClipboard(text) {
    var nav = root.navigator || (root.window && root.window.navigator);
    if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
      return nav.clipboard.writeText(text).catch(function () {
        if (!fallbackCopyText(text)) throw new Error('copy-text-failed');
      });
    }
    return new Promise(function (resolve, reject) {
      if (fallbackCopyText(text)) resolve();
      else reject(new Error('copy-text-failed'));
    });
  }

  function canWriteClipboardImage(nav) {
    return !!(
      nav &&
      nav.clipboard &&
      typeof nav.clipboard.write === 'function' &&
      typeof root.ClipboardItem === 'function'
    );
  }

  function writePngToClipboard(blob) {
    var nav = root.navigator || (root.window && root.window.navigator);
    if (!canWriteClipboardImage(nav)) {
      return Promise.reject(Object.assign(new Error('clipboard-image-unsupported'), { code: 'unsupported' }));
    }
    var item;
    try {
      item = new root.ClipboardItem({ 'image/png': blob });
    } catch (err) {
      try {
        item = new root.ClipboardItem({ 'image/png': Promise.resolve(blob) });
      } catch (err2) {
        return Promise.reject(Object.assign(err2, { code: 'unsupported' }));
      }
    }
    return nav.clipboard.write([item]);
  }

  function pngFileFromBlob(blob, filename) {
    var FileCtor = root.File || (root.window && root.window.File);
    var name = filename || 'interoves-result.png';
    if (typeof FileCtor === 'function') {
      return new FileCtor([blob], name, { type: 'image/png' });
    }
    blob.name = name;
    blob.type = blob.type || 'image/png';
    return blob;
  }

  function canShareFiles(nav, files) {
    if (!nav || typeof nav.canShare !== 'function') return false;
    try {
      return !!nav.canShare({ files: files });
    } catch (err) {
      return false;
    }
  }

  function isAbortError(err) {
    if (!err) return false;
    var name = err.name || '';
    return name === 'AbortError' || name === 'AbortError'.toLowerCase();
  }

  function getPngBlob(block) {
    var payload = readPayload(block);
    var api = shareCardApi();
    if (!payload || !api || typeof api.renderShareCardPng !== 'function') {
      return Promise.reject(new Error('renderer-unavailable'));
    }
    return api.renderShareCardPng(payload);
  }

  function copyShareText(block, btn) {
    var text = shareTextFromBlock(block);
    if (!text) return Promise.resolve();
    track('result_text_copy', analyticsParams(block));
    return copyTextToClipboard(text).then(function () {
      setStatus(block, COPY_TEXT_DONE);
      flashButton(btn, COPY_TEXT_DONE, COPY_TEXT_LABEL);
    }).catch(function () {
      setStatus(block, 'Не удалось скопировать результат');
    });
  }

  function copyShareImage(block, btn) {
    track('result_image_copy', analyticsParams(block));
    var nav = root.navigator || (root.window && root.window.navigator);
    if (!canWriteClipboardImage(nav)) {
      setStatus(block, IMAGE_COPY_UNSUPPORTED);
      return Promise.resolve();
    }
    return getPngBlob(block).then(function (blob) {
      return writePngToClipboard(blob);
    }).then(function () {
      setStatus(block, COPY_IMAGE_DONE);
      flashButton(btn, COPY_IMAGE_DONE, COPY_IMAGE_LABEL);
    }).catch(function (err) {
      if (err && err.code === 'unsupported') {
        setStatus(block, IMAGE_COPY_UNSUPPORTED);
        return;
      }
      setStatus(block, IMAGE_COPY_FAILED);
    });
  }

  function shareNative(block, btn) {
    var params = analyticsParams(block);
    track('result_share_click', params);
    var nav = root.navigator || (root.window && root.window.navigator);
    var text = shareTextFromBlock(block);
    var payload = readPayload(block) || {};
    var title = payload.headline || payload.title || SHARE_LABEL;

    function shareTextOnly() {
      if (nav && typeof nav.share === 'function') {
        return Promise.resolve(nav.share({ title: title, text: text })).then(function () {
          track('result_share_success', params);
          setStatus(block, SHARE_LABEL);
        });
      }
      return copyTextToClipboard(text).then(function () {
        setStatus(block, SHARE_UNAVAILABLE);
        flashButton(btn, SHARE_UNAVAILABLE, SHARE_LABEL);
      });
    }

    return getPngBlob(block).then(function (blob) {
      var file = pngFileFromBlob(blob, payload.filename || 'interoves-result.png');
      var files = [file];
      if (nav && typeof nav.share === 'function' && canShareFiles(nav, files)) {
        return Promise.resolve(nav.share({ files: files, title: title, text: text })).then(function () {
          track('result_share_success', params);
          setStatus(block, SHARE_LABEL);
        });
      }
      return shareTextOnly();
    }).catch(function (err) {
      if (isAbortError(err)) {
        track('result_share_cancel', params);
        return;
      }
      if (nav && typeof nav.share === 'function') {
        return shareTextOnly().catch(function (err2) {
          if (isAbortError(err2)) {
            track('result_share_cancel', params);
            return;
          }
          track('result_share_error', params);
          setStatus(block, SHARE_FAILED);
        });
      }
      track('result_share_error', params);
      setStatus(block, SHARE_FAILED);
    });
  }

  function closestBlock(target) {
    if (!target || typeof target.closest !== 'function') return null;
    return target.closest('[data-raddle-result]');
  }

  function onClick(event) {
    var target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    var copyTextBtn = target.closest('[data-share-copy-text], .new-raddle-result__copy');
    var copyImageBtn = target.closest('[data-share-copy-image]');
    var shareBtn = target.closest('[data-share-native]');
    if (!copyTextBtn && !copyImageBtn && !shareBtn) return;
    var block = closestBlock(target);
    if (!block) return;
    event.preventDefault();
    var run;
    if (copyImageBtn) run = copyShareImage(block, copyImageBtn);
    else if (shareBtn) run = shareNative(block, shareBtn);
    else run = copyShareText(block, copyTextBtn);
    Promise.resolve(run).catch(function () {});
  }

  function bind(doc) {
    var target = doc || (typeof document !== 'undefined' ? document : null);
    if (!target || typeof target.addEventListener !== 'function') return;
    if (target.__dailyShareBound) return;
    target.__dailyShareBound = true;
    target.addEventListener('click', onClick);
  }

  var api = {
    COPY_TEXT_LABEL: COPY_TEXT_LABEL,
    COPY_TEXT_DONE: COPY_TEXT_DONE,
    COPY_IMAGE_LABEL: COPY_IMAGE_LABEL,
    COPY_IMAGE_DONE: COPY_IMAGE_DONE,
    bind: bind,
    copyShareText: copyShareText,
    copyShareImage: copyShareImage,
    shareNative: shareNative,
    shareTextFromBlock: shareTextFromBlock,
    readPayload: readPayload,
    isAbortError: isAbortError,
    canShareFiles: canShareFiles,
    canWriteClipboardImage: canWriteClipboardImage,
    onClick: onClick,
  };

  root.DailyShareActions = api;
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { bind(document); });
    } else {
      bind(document);
    }
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
