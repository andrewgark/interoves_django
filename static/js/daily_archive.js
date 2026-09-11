(function () {
  document.querySelectorAll('.daily-archive').forEach(function (archive) {
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
