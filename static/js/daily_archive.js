(function () {
  document.querySelectorAll('.daily-archive').forEach(function (archive) {
    var rememberCookie = archive.getAttribute('data-remember-cookie');
    if (rememberCookie) {
      // Keep a non-current month the player chose for a while; forget it otherwise.
      var value = archive.getAttribute('data-remember-value') || '';
      var maxAge = value ? (parseInt(archive.getAttribute('data-remember-max-age'), 10) || 0) : 0;
      document.cookie = rememberCookie + '=' + encodeURIComponent(value) +
        '; path=/; max-age=' + maxAge + '; SameSite=Lax';
    }
    var picker = archive.querySelector('.daily-archive__month-picker');
    var months = archive.querySelector('.daily-archive__months');
    if (picker && months) {
      var closePicker = function () {
        picker.setAttribute('aria-expanded', 'false');
        months.hidden = true;
      };
      picker.addEventListener('click', function () {
        var open = picker.getAttribute('aria-expanded') === 'true';
        picker.setAttribute('aria-expanded', String(!open));
        months.hidden = open;
      });
      picker.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
          closePicker();
          picker.focus();
        }
      });
      months.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
          event.preventDefault();
          closePicker();
          picker.focus();
        }
      });
      document.addEventListener('click', function (event) {
        if (!archive.contains(event.target)) closePicker();
      });
    }
    var dayLinks = Array.prototype.slice.call(archive.querySelectorAll('[data-daily-archive-day]'));
    dayLinks.forEach(function (link, index) {
      link.addEventListener('keydown', function (event) {
        var next = null;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = dayLinks[index + 1];
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = dayLinks[index - 1];
        if (event.key === 'Home') next = dayLinks[0];
        if (event.key === 'End') next = dayLinks[dayLinks.length - 1];
        if (next) { event.preventDefault(); next.focus(); }
      });
    });
    archive.querySelectorAll('a[href*="#"]').forEach(function (link) {
      link.addEventListener('click', function () {
        var target = document.getElementById(link.hash.slice(1));
        if (target) { target.classList.add('daily-archive__card--focus'); setTimeout(function () { target.classList.remove('daily-archive__card--focus'); }, 1400); }
      });
    });
  });
})();
