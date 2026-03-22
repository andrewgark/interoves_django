(function (global) {
  'use strict';

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

  function connectUserHub() {
    var socket = new WebSocket(proto() + '://' + host() + '/ws/track/');
    socket.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        handleTrackEvent(msg);
      } catch (e) {}
    };
  }

  function connectGame(gameId) {
    if (!gameId) return;
    var socket = new WebSocket(
      proto() + '://' + host() + '/games/' + encodeURIComponent(gameId) + '/track'
    );
    socket.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === 'track.event') {
          handleTrackEvent(msg);
          return;
        }
        applyTaskUpdates(msg);
      } catch (e) {}
    };
  }

  global.InterovesTrack = {
    connectUserHub: connectUserHub,
    connectGame: connectGame,
  };
})(window);
