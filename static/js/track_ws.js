(function (global) {
  'use strict';

  var PING_MS = 25000;
  var RECONNECT_MIN_MS = 1000;
  var RECONNECT_MAX_MS = 30000;

  function proto() {
    return window.location.protocol === 'https:' ? 'wss' : 'ws';
  }

  function host() {
    return window.location.host;
  }

  var lastReloadKey = '';
  var lastReloadAt = 0;
  function reloadTrackEventOnce(key) {
    var now = Date.now();
    if (key === lastReloadKey && now - lastReloadAt < 2000) return;
    lastReloadKey = key;
    lastReloadAt = now;
    window.location.reload();
  }

  function handleTrackEvent(msg) {
    if (!msg || msg.type !== 'track.event') return;
    if (
      msg.event === 'game.play_available' ||
      msg.event === 'game.started' ||
      msg.event === 'game.ended'
    ) {
      var gid = (msg.payload && msg.payload.game_id) || '';
      reloadTrackEventOnce(msg.event + ':' + gid);
    }
  }

  function applyTaskUpdates(msg) {
    if (msg.update_task_html_new && typeof global.applyNewUiTaskHtml === 'function') {
      global.applyNewUiTaskHtml(msg.update_task_html_new);
    } else if (msg.update_task_html && typeof global.updateTasks === 'function') {
      global.updateTasks(msg.update_task_html);
    }
    if (msg.update_task_group_title_html && typeof global.updateTaskGroupTitle === 'function') {
      global.updateTaskGroupTitle(msg.update_task_group_title_html);
    }
    if (msg.update_game_title_html && typeof global.updateGameTitle === 'function') {
      global.updateGameTitle(msg.update_game_title_html);
    }
  }

  function acceptFreshSequence(msg, seen) {
    if (!msg || typeof msg.seq !== 'number') return true;
    var namespace = String(msg.seq_namespace || 'legacy');
    var previous = seen[namespace];
    if (typeof previous === 'number' && msg.seq <= previous) return false;
    seen[namespace] = msg.seq;
    return true;
  }

  /**
   * Persistent WebSocket with exponential reconnect backoff and application ping.
   * onMessage(msg) receives parsed JSON except ping/pong.
   */
  function openTrackSocket(url, onMessage) {
    var delay = RECONNECT_MIN_MS;
    var socket = null;
    var pingTimer = null;
    var reconnectTimer = null;
    var stopped = false;
    var seenSequences = {};

    function clearPing() {
      if (pingTimer) {
        clearInterval(pingTimer);
        pingTimer = null;
      }
    }

    function scheduleReconnect() {
      if (stopped || reconnectTimer) return;
      reconnectTimer = setTimeout(function () {
        reconnectTimer = null;
        connect();
      }, delay);
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
        clearPing();
        pingTimer = setInterval(function () {
          if (socket && socket.readyState === 1) {
            try {
              socket.send(JSON.stringify({ type: 'ping' }));
            } catch (err) {}
          }
        }, PING_MS);
      };

      socket.onmessage = function (ev) {
        try {
          var msg = JSON.parse(ev.data);
          if (!msg || msg.type === 'pong') return;
          if (!acceptFreshSequence(msg, seenSequences)) return;
          if (typeof onMessage === 'function') onMessage(msg);
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

    connect();

    return {
      close: function () {
        stopped = true;
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        clearPing();
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

  function connectGame(gameId) {
    if (!gameId) return null;
    var url = proto() + '://' + host() + '/games/' + encodeURIComponent(gameId) + '/track';
    return openTrackSocket(url, function (msg) {
      if (msg.type === 'track.event') {
        handleTrackEvent(msg);
        return;
      }
      applyTaskUpdates(msg);
    });
  }

  global.InterovesTrack = {
    connectUserHub: connectUserHub,
    connectGame: connectGame,
    openTrackSocket: openTrackSocket,
    acceptFreshSequence: acceptFreshSequence,
  };
})(window);
