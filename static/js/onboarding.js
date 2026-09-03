(function (global) {
  'use strict';

  var STORAGE_KEY = 'interoves_onboarding_v2';
  var MAX_AGE_MS = 24 * 60 * 60 * 1000;
  // Let the existing success UI (solved board / toast) land before the prompt.
  var SOCIAL_PROMPT_DELAY_MS = 1600;

  function nowMs() {
    return Date.now ? Date.now() : new Date().getTime();
  }

  function normalizeGame(game) {
    game = String(game || '').toLowerCase();
    return game === 'alphabet' ? 'alphabetty' : game;
  }

  function readContext() {
    try {
      var value = JSON.parse(global.localStorage.getItem(STORAGE_KEY) || 'null');
      if (!value || !value.createdAt || nowMs() - Number(value.createdAt) > MAX_AGE_MS) {
        global.localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      return value;
    } catch (e) {
      return null;
    }
  }

  function writeContext(value) {
    try { global.localStorage.setItem(STORAGE_KEY, JSON.stringify(value)); }
    catch (e) {}
  }

  function eventKey(prefix, context) {
    return prefix + ':' + String(context && context.id || 'unknown');
  }

  function trackOnce(key, goal, params) {
    var analytics = global.interovesAnalytics;
    if (!analytics || !analytics.trackYandexGoalOnce) return false;
    return analytics.trackYandexGoalOnce(key, goal, params || {});
  }

  function markSelection(game, recommended) {
    var context = {
      id: String(nowMs()) + '-' + String(Math.random()).slice(2),
      source: 'start',
      selectedGame: normalizeGame(game),
      recommended: !!recommended,
      stage: 'selected',
      createdAt: nowMs(),
      updatedAt: nowMs()
    };
    writeContext(context);
    return context;
  }

  function initStartPage() {
    trackOnce(
      'onboarding-view:' + String(nowMs()) + ':' + String(Math.random()).slice(2),
      'onboarding_view',
      { source: 'start' }
    );
    global.document.querySelectorAll('[data-onboarding-game]').forEach(function (link) {
      link.addEventListener('click', function () {
        var game = normalizeGame(link.getAttribute('data-onboarding-game'));
        var recommended = link.getAttribute('data-onboarding-recommended') === '1';
        var context = markSelection(game, recommended);
        trackOnce(
          eventKey('onboarding-select', context),
          'onboarding_game_select',
          { game: game, recommended: recommended }
        );
      });
    });
  }

  function socialPromptEl(block) {
    return block && block.querySelector
      ? block.querySelector('[data-onboarding-social-prompt]')
      : null;
  }

  function canShowSocialPrompt(context) {
    return !!(
      context
      && context.stage === 'completed'
      && !context.socialFollowPromptShown
      && !context.socialFollowPromptDismissed
    );
  }

  function revealSocialPrompt(block) {
    var prompt = socialPromptEl(block);
    var context = readContext();
    if (!prompt || !canShowSocialPrompt(context)) return;
    prompt.hidden = false;
    context.socialFollowPromptShown = true;
    context.updatedAt = nowMs();
    writeContext(context);
    trackOnce(
      eventKey('social-follow-prompt-view', context),
      'social_follow_prompt_view',
      { game: normalizeGame(context.firstGame) }
    );
  }

  function hideSocialPrompt(block) {
    var prompt = socialPromptEl(block);
    if (prompt) prompt.hidden = true;
  }

  function dismissSocialPrompt(block) {
    var context = readContext();
    hideSocialPrompt(block);
    if (!context || context.socialFollowPromptDismissed) return;
    context.socialFollowPromptDismissed = true;
    context.socialFollowPromptShown = true;
    context.updatedAt = nowMs();
    writeContext(context);
    trackOnce(
      eventKey('social-follow-prompt-dismiss', context),
      'social_follow_prompt_dismiss',
      { game: normalizeGame(context.firstGame) }
    );
  }

  function bindSocialPrompt(block) {
    var prompt = socialPromptEl(block);
    if (!prompt || prompt.getAttribute('data-onboarding-social-bound') === '1') return;
    prompt.setAttribute('data-onboarding-social-bound', '1');
    var closeBtn = prompt.querySelector('[data-onboarding-social-dismiss]');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        dismissSocialPrompt(block);
      });
    }
    (prompt.querySelectorAll('[data-onboarding-social-platform]') || []).forEach(function (link) {
      link.addEventListener('click', function () {
        var active = readContext();
        var platform = String(link.getAttribute('data-onboarding-social-platform') || '');
        if (active && platform) {
          trackOnce(
            eventKey('social-follow-click-' + platform, active),
            'social_follow_click',
            { platform: platform }
          );
        }
        hideSocialPrompt(block);
      });
    });
  }

  function showFollowup(block, options) {
    if (!block) return;
    block.hidden = false;
    bindSocialPrompt(block);
    if (options && options.delaySocialPrompt) {
      global.setTimeout(function () {
        revealSocialPrompt(block);
      }, SOCIAL_PROMPT_DELAY_MS);
      return;
    }
    revealSocialPrompt(block);
  }

  function goalsFromEvent(event) {
    var detail = event && event.detail;
    return detail && Array.isArray(detail.goals) ? detail.goals : [];
  }

  function isDifferentGameStart(context, currentGame, params) {
    var firstGame = normalizeGame(context.firstGame);
    var eventGameId = String(params.game_id || '');
    if (firstGame !== currentGame) return true;
    // A different archived game of the same format is also a valid second game.
    // Without both IDs we cannot distinguish it from a retried delivery of the
    // first game_start payload, so keep the conservative answer.
    return !!(
      context.firstGameId
      && eventGameId
      && eventGameId !== String(context.firstGameId)
    );
  }

  function initGamePage(block) {
    if (!block) return;
    bindSocialPrompt(block);
    var currentGame = normalizeGame(block.getAttribute('data-onboarding-current-game'));
    var context = readContext();
    if (context && context.stage === 'completed' && normalizeGame(context.firstGame) === currentGame) {
      showFollowup(block);
    }

    block.querySelectorAll('[data-onboarding-next-game]').forEach(function (link) {
      link.addEventListener('click', function () {
        var active = readContext();
        if (!active || active.stage !== 'completed') return;
        active.stage = 'awaiting_second_start';
        active.targetGame = normalizeGame(link.getAttribute('data-onboarding-next-game'));
        active.updatedAt = nowMs();
        writeContext(active);
      });
    });

    global.addEventListener('interoves:analytics-goals', function (event) {
      var active = readContext();
      if (!active) return;
      goalsFromEvent(event).forEach(function (payload) {
        if (!payload || !payload.goal) return;
        var params = payload.params || {};
        var eventGame = normalizeGame(params.game || currentGame);

        if (
          payload.goal === 'game_start'
          && active.stage === 'selected'
          && normalizeGame(active.selectedGame) === currentGame
          && eventGame === currentGame
        ) {
          active.stage = 'first_started';
          active.firstGame = currentGame;
          active.firstGameId = String(params.game_id || '');
          active.updatedAt = nowMs();
          writeContext(active);
        } else if (
          payload.goal === 'game_complete'
          && active.stage === 'first_started'
          && normalizeGame(active.firstGame) === currentGame
          && eventGame === currentGame
          && (!active.firstGameId || !params.game_id || String(params.game_id) === active.firstGameId)
        ) {
          active.stage = 'completed';
          active.completedAt = nowMs();
          active.updatedAt = nowMs();
          writeContext(active);
          trackOnce(
            eventKey('onboarding-first-complete', active),
            'onboarding_first_game_complete',
            { game: currentGame, recommended: !!active.recommended }
          );
          showFollowup(block, { delaySocialPrompt: true });
        } else if (
          payload.goal === 'game_start'
          && (active.stage === 'completed' || active.stage === 'awaiting_second_start')
          && eventGame === currentGame
          && isDifferentGameStart(active, currentGame, params)
        ) {
          trackOnce(
            eventKey('onboarding-second-start', active),
            'onboarding_second_game_start',
            { first_game: normalizeGame(active.firstGame), game: currentGame }
          );
          active.stage = 'second_started';
          active.secondGame = currentGame;
          active.updatedAt = nowMs();
          writeContext(active);
          block.hidden = true;
        }
      });
    });
  }

  global.interovesOnboarding = {
    initStartPage: initStartPage,
    initGamePage: initGamePage,
    _readContext: readContext,
    _normalizeGame: normalizeGame
  };
})(typeof window !== 'undefined' ? window : globalThis);
