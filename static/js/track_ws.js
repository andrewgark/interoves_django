(function (global) {
  'use strict';

  var SYNC_MS = 25000;
  var RECONNECT_MIN_MS = 1000;
  var RECONNECT_MAX_MS = 30000;
  // Spread recovery traffic across clients after a shared outage.
  var RECONNECT_JITTER_MS = 5000;
  var DAILY_TRANSITION_JITTER_MS = 20000;
  var GAME_TRANSITION_JITTER_MS = 3000;

  function proto() {
    return window.location.protocol === 'https:' ? 'wss' : 'ws';
  }

  function host() {
    return window.location.host;
  }

  var lastReloadKey = '';
  var lastReloadAt = 0;
  var RELOAD_COOLDOWN_MS = 30000;
  var pendingReloadTimer = null;
  var pendingReloadKey = '';
  function reloadTrackEventOnce(key) {
    var now = Date.now();
    if (key === lastReloadKey && now - lastReloadAt < 2000) return;
    try {
      var storageKey = 'interoves_track_reload:' + key;
      var previous = Number(global.sessionStorage.getItem(storageKey)) || 0;
      if (previous && now - previous < RELOAD_COOLDOWN_MS) return;
      global.sessionStorage.setItem(storageKey, String(now));
    } catch (e) {}
    lastReloadKey = key;
    lastReloadAt = now;
    window.location.reload();
  }

  function randomDelayMs(maxJitterMs, randomValue) {
    var max = Math.max(0, Number(maxJitterMs) || 0);
    var random = typeof randomValue === 'number' ? randomValue : Math.random();
    random = Math.max(0, Math.min(0.999999999, random));
    return Math.floor(random * (max + 1));
  }

  function scheduleReload(key, maxJitterMs) {
    if (key === lastReloadKey || (pendingReloadTimer && pendingReloadKey === key)) return;
    if (pendingReloadTimer) clearTimeout(pendingReloadTimer);
    pendingReloadKey = key;
    pendingReloadTimer = setTimeout(function () {
      pendingReloadTimer = null;
      pendingReloadKey = '';
      reloadTrackEventOnce(key);
    }, randomDelayMs(maxJitterMs));
  }

  function handleTrackEvent(msg) {
    if (!msg || msg.type !== 'track.event') return;
    if (
      msg.event === 'game.play_available' ||
      msg.event === 'game.started' ||
      msg.event === 'game.ended'
    ) {
      var gid = (msg.payload && msg.payload.game_id) || '';
      scheduleReload(msg.event + ':' + gid, GAME_TRANSITION_JITTER_MS);
    }
  }

  function applyTaskUpdates(msg) {
    if (msg.update_task_html_new && typeof global.applyNewUiTaskHtml === 'function') {
      global.applyNewUiTaskHtml(msg.update_task_html_new);
    } else if (msg.update_task_html && typeof global.updateTasks === 'function') {
      global.updateTasks(msg.update_task_html);
    }
    if (msg.raddle_ui && typeof global.applyRaddleUiState === 'function') {
      global.applyRaddleUiState(msg.raddle_ui, {fromRemote: true});
    }
    if (msg.update_task_group_title_html && typeof global.updateTaskGroupTitle === 'function') {
      global.updateTaskGroupTitle(msg.update_task_group_title_html);
    }
    if (msg.update_game_title_html && typeof global.updateGameTitle === 'function') {
      global.updateGameTitle(msg.update_game_title_html);
    }
  }

  function dispatchPageSyncEvent(name, detail) {
    var doc = global.document;
    if (!doc || typeof doc.dispatchEvent !== 'function' || typeof global.CustomEvent !== 'function') return false;
    var event = new global.CustomEvent(name, {detail: detail});
    doc.dispatchEvent(event);
    return true;
  }

  function taskChangeMatchesPageMode(msg) {
    var body = global.document && global.document.body;
    var mode = body && body.getAttribute
      ? body.getAttribute('data-track-play-mode')
      : '';
    if (!mode) mode = global.InterovesPlayMode || '';
    if (msg && msg.by === 'team' && mode === 'personal') return false;
    if (msg && msg.by === 'personal' && mode === 'team') return false;
    return true;
  }

  function acceptFreshSequence(msg, seen) {
    if (!msg || typeof msg.seq !== 'number' || !Number.isFinite(msg.seq)) return true;
    var namespace = String(msg.seq_namespace || 'legacy');
    var previous = seen[namespace];
    if (typeof previous === 'number' && msg.seq <= previous) return false;
    seen[namespace] = msg.seq;
    return true;
  }

  function nextGamePhaseAt(root, nowMs) {
    if (!root || typeof root.getAttribute !== 'function') return null;
    var now = typeof nowMs === 'number' ? nowMs : Date.now();
    var candidates = [
      'data-game-start-at',
      'data-game-end-at',
      'data-live-next-transition-at',
    ].map(function (name) {
      var raw = root.getAttribute(name);
      var value = raw ? Date.parse(raw) : NaN;
      return Number.isFinite(value) && value > now ? value : null;
    }).filter(function (value) { return value !== null; });
    if (!candidates.length) return null;
    return Math.min.apply(Math, candidates);
  }

  function transitionJitterMs(root, transitionAt) {
    var gameStartAt = Date.parse(root.getAttribute('data-game-start-at') || '');
    var gameEndAt = Date.parse(root.getAttribute('data-game-end-at') || '');
    if (gameStartAt === transitionAt || gameEndAt === transitionAt) {
      return GAME_TRANSITION_JITTER_MS;
    }
    var dailyAt = Date.parse(root.getAttribute('data-live-next-transition-at') || '');
    return dailyAt === transitionAt ? DAILY_TRANSITION_JITTER_MS : GAME_TRANSITION_JITTER_MS;
  }

  function scheduleGamePhaseReload(root) {
    var MAX_DELAY_MS = 2147483000;
    function schedule() {
      var now = Date.now();
      var transitionAt = nextGamePhaseAt(root, now);
      if (transitionAt === null) return;
      var remaining = transitionAt - now;
      setTimeout(function () {
        if (remaining > MAX_DELAY_MS) {
          schedule();
          return;
        }
        scheduleReload('game-phase:' + transitionAt, transitionJitterMs(root, transitionAt));
      }, Math.min(remaining, MAX_DELAY_MS));
    }
    schedule();
  }

  function collectTaskIds(root) {
    if (!root || typeof root.querySelectorAll !== 'function') return [];
    var seen = {};
    var ids = [];
    root.querySelectorAll('[id^="new-task-"]').forEach(function (el) {
      var match = String(el.id || '').match(/^new-task-(\d+)$/);
      if (!match || seen[match[1]]) return;
      seen[match[1]] = true;
      ids.push(match[1]);
    });
    return ids;
  }

  function reconcileTaskGroup(root) {
    var doc = root && root.body ? root : null;
    var body = doc ? doc.body : root;
    var endpoint = body && body.getAttribute
      ? body.getAttribute('data-track-live-state-url')
      : '';
    var taskIds = collectTaskIds(doc || root);
    if (!endpoint || !taskIds.length || typeof global.fetch !== 'function') {
      return Promise.reject(new Error('live state unavailable'));
    }
    var joiner = endpoint.indexOf('?') === -1 ? '?' : '&';
    var query = 'task_ids=' + encodeURIComponent(taskIds.join(','));
    var playMode = body && body.getAttribute
      ? body.getAttribute('data-track-play-mode')
      : '';
    if (playMode === 'team' || playMode === 'personal') {
      query += '&play_mode=' + encodeURIComponent(playMode);
    }
    var shareHash = body && body.getAttribute
      ? body.getAttribute('data-offer-share-hash')
      : '';
    if (shareHash) query += '&offer_share=' + encodeURIComponent(shareHash);
    return global.fetch(endpoint + joiner + query, {
      credentials: 'same-origin',
      headers: {'X-Requested-With': 'XMLHttpRequest'},
      cache: 'no-store',
    }).then(function (response) {
      if (!response.ok) throw new Error('live state HTTP ' + response.status);
      return response.json();
    }).then(function (data) {
      if (!data || data.status !== 'ok' || data.reload_required) {
        throw new Error('live state requires reload');
      }
      if (typeof global.applyNewUiTaskHtml !== 'function') {
        throw new Error('task projection unavailable');
      }
      global.applyNewUiTaskHtml(data.update_task_html_new || {});
      if (data.raddle_ui && typeof global.applyRaddleUiState === 'function') {
        global.applyRaddleUiState(data.raddle_ui, {fromRemote: true});
      }
      if (doc && typeof doc.dispatchEvent === 'function' && typeof global.CustomEvent === 'function') {
        doc.dispatchEvent(new global.CustomEvent('interoves:reconciled', {detail: data}));
      }
      return data;
    });
  }

  /**
   * Persistent WebSocket with exponential reconnect backoff and revision sync.
   * onMessage(msg) receives parsed JSON except ping/pong.
   */
  function openTrackSocket(url, onMessage, onResync, onSynced, isRelevantMessage) {
    var delay = RECONNECT_MIN_MS;
    var socket = null;
    var pingTimer = null;
    var reconnectTimer = null;
    var stopped = false;
    var seenSequences = {};
    var resyncing = false;
    var queuedMessages = [];
    var doc = global.document;

    function rememberSyncBaseline(versions) {
      if (!versions || typeof versions !== 'object') return;
      Object.keys(versions).forEach(function (namespace) {
        if (typeof seenSequences[namespace] === 'number') return;
        var value = Number(versions[namespace]);
        if (Number.isFinite(value) && value >= 0) seenSequences[namespace] = value;
      });
    }

    function advanceSeenVersions(versions) {
      if (!versions || typeof versions !== 'object') return;
      Object.keys(versions).forEach(function (namespace) {
        var value = Number(versions[namespace]);
        if (!Number.isFinite(value) || value < 0) return;
        if (typeof seenSequences[namespace] !== 'number' || value > seenSequences[namespace]) {
          seenSequences[namespace] = value;
        }
      });
    }

    function replaceSeenVersions(versions) {
      if (!versions || typeof versions !== 'object') return;
      Object.keys(versions).forEach(function (namespace) {
        var value = Number(versions[namespace]);
        if (Number.isFinite(value) && value >= 0) seenSequences[namespace] = value;
      });
    }

    function processMessage(msg) {
      // Filter scope before sequence tracking: another open game must not
      // advance this socket's revision or trigger a false gap/reconciliation.
      if (typeof isRelevantMessage === 'function' && !isRelevantMessage(msg)) return;
      if (resyncing) {
        queuedMessages.push(msg);
        return;
      }
      if (msg && typeof msg.seq === 'number' && Number.isFinite(msg.seq)) {
        var namespace = String(msg.seq_namespace || 'legacy');
        var previous = seenSequences[namespace];
        if (typeof previous === 'number' && msg.seq > previous + 1) {
          queuedMessages.push(msg);
          if (typeof onResync !== 'function') {
            scheduleReload('sequence-gap:' + url + ':' + namespace, RECONNECT_JITTER_MS);
            return;
          }
          var versions = {};
          versions[namespace] = msg.seq;
          var gap = {
            type: 'track.resync_required',
            versions: versions,
            missed: versions,
          };
          runReconcile(
            function () { return onResync(gap); }, true,
            'sequence-gap:' + url + ':' + namespace,
            versions,
          );
          return;
        }
      }
      if (!acceptFreshSequence(msg, seenSequences)) return;
      if (typeof onMessage === 'function') onMessage(msg);
    }

    function flushQueuedMessages() {
      var messages = queuedMessages;
      queuedMessages = [];
      messages.sort(function (left, right) {
        return Number(left.seq || 0) - Number(right.seq || 0);
      });
      messages.forEach(function (msg) {
        processMessage(msg);
      });
    }

    function runReconcile(work, fallbackToReload, fallbackKey, versions, resetNamespaces) {
      resyncing = true;
      try {
        if (typeof work === 'function') work = work();
      } catch (error) {
        work = Promise.reject(error);
      }
      Promise.resolve(work).then(function (result) {
        advanceSeenVersions((result && result.versions) || versions);
        if (resetNamespaces && resetNamespaces.length) {
          var resetVersions = (result && result.versions) || versions || {};
          var exactReset = {};
          resetNamespaces.forEach(function (namespace) {
            if (Object.prototype.hasOwnProperty.call(resetVersions, namespace)) {
              exactReset[namespace] = resetVersions[namespace];
            }
          });
          replaceSeenVersions(exactReset);
        }
        resyncing = false;
        flushQueuedMessages();
      }).catch(function () {
        resyncing = false;
        if (fallbackToReload) {
          scheduleReload(fallbackKey, RECONNECT_JITTER_MS);
        } else {
          flushQueuedMessages();
        }
      });
    }

    function clearPing() {
      if (pingTimer) {
        clearInterval(pingTimer);
        pingTimer = null;
      }
    }

    function requestSync() {
      if (!socket || socket.readyState !== 1) return;
      try {
        socket.send(JSON.stringify({ type: 'track.sync', seen: seenSequences }));
      } catch (err) {}
    }

    function onVisibilityChange() {
      if (!doc || doc.visibilityState !== 'visible') return;
      requestSync();
    }

    function scheduleReconnect() {
      if (stopped || reconnectTimer) return;
      var jitter = Math.floor(Math.random() * Math.min(RECONNECT_JITTER_MS, delay / 4));
      reconnectTimer = setTimeout(function () {
        reconnectTimer = null;
        connect();
      }, delay + jitter);
      delay = Math.min(RECONNECT_MAX_MS, delay * 2);
    }

    function connect() {
      if (stopped) return;
      clearPing();
      try {
        socket = new WebSocket(url);
      } catch (e) {
        scheduleReconnect();
        return;
      }

      socket.onopen = function () {
        delay = RECONNECT_MIN_MS;
        requestSync();
        clearPing();
        pingTimer = setInterval(requestSync, SYNC_MS);
      };

      socket.onmessage = function (ev) {
        try {
          var msg = JSON.parse(ev.data);
          if (!msg || msg.type === 'pong') return;
          if (msg.type === 'track.resync_required') {
            if (resyncing) return;
            if (typeof onResync !== 'function') {
              scheduleReload('resync:' + url, RECONNECT_JITTER_MS);
              return;
            }
            runReconcile(
              function () { return onResync(msg); },
              true, 'resync:' + url, msg.versions,
              msg.reset && Object.keys(msg.reset)
            );
            return;
          }
          if (msg.type === 'track.synced') {
            rememberSyncBaseline(msg.versions);
            if (typeof onSynced === 'function' && !resyncing) {
              runReconcile(function () { return onSynced(msg); }, false, '', msg.versions);
            }
            return;
          }
          processMessage(msg);
        } catch (e) {}
      };

      socket.onerror = function () {
        // onclose follows; reconnect there
      };

      socket.onclose = function () {
        clearPing();
        socket = null;
        scheduleReconnect();
      };
    }

    if (doc && typeof doc.addEventListener === 'function') {
      doc.addEventListener('visibilitychange', onVisibilityChange);
    }
    connect();

    return {
      close: function () {
        stopped = true;
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        clearPing();
        if (doc && typeof doc.removeEventListener === 'function') {
          doc.removeEventListener('visibilitychange', onVisibilityChange);
        }
        if (socket) {
          try {
            socket.close();
          } catch (e) {}
          socket = null;
        }
      },
    };
  }

  function connectUserHub() {
    return openTrackSocket(proto() + '://' + host() + '/ws/track/', function (msg) {
      handleTrackEvent(msg);
    });
  }

  function connectGame(gameId, trackUrl, playMode) {
    if (!gameId) return null;
    var url = trackUrl || ('/games/' + encodeURIComponent(gameId) + '/track');
    if (!/^wss?:\/\//i.test(url)) {
      url = proto() + '://' + host() + (url.charAt(0) === '/' ? url : '/' + url);
    }
    if (playMode === 'team' || playMode === 'personal') {
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'play_mode=' + encodeURIComponent(playMode);
    }
    var initialReconciled = false;
    var initialReconcileInFlight = false;
    return openTrackSocket(url, function (msg) {
      if (msg.type === 'track.event') {
        handleTrackEvent(msg);
        return;
      }
      if (!taskChangeMatchesPageMode(msg)) return;
      dispatchPageSyncEvent('interoves:track-task-changed', {
        gameId: String(gameId),
        taskId: msg.task == null ? '' : String(msg.task),
        reason: msg.reason || '',
      });
      applyTaskUpdates(msg);
    }, function (msg) {
      var detail = {
        gameId: String(gameId),
        versions: (msg && msg.versions) || {},
        promise: null,
      };
      dispatchPageSyncEvent('interoves:track-resync', detail);
      if (detail.promise && typeof detail.promise.then === 'function') {
        return detail.promise.then(function () {
          return {versions: detail.versions};
        });
      }
      return reconcileTaskGroup(global.document);
    }, function (msg) {
      var body = global.document && global.document.body;
      if (initialReconciled || initialReconcileInFlight) return;
      initialReconcileInFlight = true;
      var detail = {
        gameId: String(gameId),
        versions: (msg && msg.versions) || {},
        initial: true,
        promise: null,
      };
      dispatchPageSyncEvent('interoves:track-synced', detail);
      if (detail.promise && typeof detail.promise.then === 'function') {
        return detail.promise.then(function (data) {
          initialReconciled = true;
          initialReconcileInFlight = false;
          return data || {versions: detail.versions};
        }).catch(function (error) {
          initialReconcileInFlight = false;
          throw error;
        });
      }
      if (!body || !body.getAttribute('data-track-live-state-url')) {
        initialReconciled = true;
        initialReconcileInFlight = false;
        return;
      }
      return reconcileTaskGroup(global.document).then(function (data) {
        initialReconciled = true;
        initialReconcileInFlight = false;
        return data;
      }).catch(function () {
        // Keep the rendered page usable; retry on the next periodic sync.
        initialReconcileInFlight = false;
        throw new Error('initial live-state reconciliation failed');
      });
    }, function (msg) {
      if (!msg) return false;
      var eventGameId = msg.game_id;
      if (eventGameId == null && msg.type === 'track.event' && msg.payload) {
        eventGameId = msg.payload.game_id;
      }
      if (msg.type === 'task.changed' && msg.by === 'personal' && eventGameId == null) {
        return false;
      }
      return eventGameId == null || String(eventGameId) === String(gameId);
    });
  }

  global.InterovesTrack = {
    connectUserHub: connectUserHub,
    connectGame: connectGame,
    openTrackSocket: openTrackSocket,
    acceptFreshSequence: acceptFreshSequence,
    nextGamePhaseAt: nextGamePhaseAt,
    randomDelayMs: randomDelayMs,
    scheduleReload: scheduleReload,
    transitionJitterMs: transitionJitterMs,
    scheduleGamePhaseReload: scheduleGamePhaseReload,
    collectTaskIds: collectTaskIds,
    reconcileTaskGroup: reconcileTaskGroup,
  };
})(window);
