(function () {
  'use strict';

  // Game pages can be restored from the back/forward cache with DOM that no
  // longer matches server state. Reload once on restoration; normal page boot
  // then fetches and renders the authoritative state.
  var reloading = false;
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted || reloading) return;
    if (!document.body || document.body.getAttribute('data-game-state-page') !== '1') return;
    reloading = true;
    window.location.reload();
  });
}());
