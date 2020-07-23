
function main() {

(function () {
   'use strict';
   
  	$('a.page-scroll').click(function() {
        if (location.pathname.replace(/^\//,'') == this.pathname.replace(/^\//,'') && location.hostname == this.hostname) {
          var target = $(this.hash);
          target = target.length ? target : $('[name=' + this.hash.slice(1) +']');
          if (target.length) {
            $('html,body').animate({
              scrollTop: target.offset().top - 50
            }, 900);
            return false;
          }
        }
      });


    $('body').scrollspy({ 
        target: '.navbar-default',
        offset: 80
    });

	// Hide nav on click
  $(".navbar-nav li a").click(function (event) {
    // check if window is small enough so dropdown is created
    var toggle = $(".navbar-toggle").is(":visible");
    if (toggle) {
      $(".navbar-collapse").collapse('hide');
    }
  });
	
	
    // Nivo Lightbox 
    $('.portfolio-item a').nivoLightbox({
            effect: 'slideDown',  
            keyboardNav: true,                            
        });
		
}());

function processNewAttempt(form, data) {
  if (data['status'] == 'duplicate') {
    alert('Посылка является копией одной из предудущих посылок.');
    return;
  }
  if (data['status'] == 'attempt_limit_exceeded') {
    alert('У вашей команды закончились посылки');
    return;
  }
  if (data['status'] != 'ok') {
        console.log("Unknown status of attempt response: " + data['status']);
        return;
  }
  
  var task_html_id = 'task-' + data['task_id'];
  $('#' + task_html_id).replaceWith(data['html']);
  $('#' + task_html_id + ' .attempt-form').on('submit', submitAttemptForm);
}

function submitAttemptForm(event) {
  event.preventDefault();
  var raw_form_data = $(this).serializeArray();
  var form_data = {}
  $.map(raw_form_data, function(n, i){
      form_data[n['name']] = n['value'];
  });
  param_form_data = $.param(form_data)
  var task_id = $(this).children(".attempt-task-id")[0].value;
  var csrf = $(this).children("input[name=csrfmiddlewaretoken]")[0].value;

  $.ajaxSetup({
      headers: { "X-CSRFToken": csrf }
  });
  $.ajax({
      type: 'POST',
      url: '/send_attempt/' + task_id + '/',
      dataType: 'json',
      data: param_form_data,
      contentType : 'application/x-www-form-urlencoded',
      success: function(data) {
          processNewAttempt($(this), data)
      }
  });
}

function fadeInAnswer(overlay, window) {
  overlay.fadeIn(297, function(){
    window
    .css('display', 'block')
    .animate({opacity: 1}, 198);
  });
}

function processAnswer(parent, data) {
  var is_first_time_answer_asked = parent.children('#answerOverlay').length == 0;
  if (is_first_time_answer_asked) {
    parent.append($(data['html']));
  }
  
  var overlay = $(parent.children('#answerOverlay')[0]);
  var window = $(parent.children('#answerWindow')[0]);
  
  overlay.fadeIn(297, function(){
    window
    .css('display', 'block')
    .animate({opacity: 1}, 198);
  });

  if (!is_first_time_answer_asked) {
    return;
  }

  var close = parent.children('#answerOverlay').add(window.children('#answerWindow__close'));

  close.click( function(){
    window.animate({opacity: 0}, 198, function(){
      $(this).css('display', 'none');
      overlay.fadeOut(297);
    });
  });

}

function showAnswer(event) {
  event.preventDefault();
  var parent = $($(this).parent());
  var form = parent;
  var task_id = form.children(".attempt-task-id")[0].value;
  var csrf = form.children("input[name=csrfmiddlewaretoken]")[0].value;

  $.ajaxSetup({
      headers: { "X-CSRFToken": csrf }
  });
  $.ajax({
      type: 'GET',
      url: '/get_answer/' + task_id + '/',
      dataType: 'json',
      success: function(data) {
          processAnswer(parent, data)
      }
  });
}

$(document).ready(function() {
  $('.attempt-form').on('submit', submitAttemptForm);
  $('.show-answer').on('click', showAnswer);
});

}

main();
