
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

function toggleTasksWithPrefix(prefix) {
  $(prefix + '.li-task.li-ok:not(.table-3-n-cell)').toggleClass('display-none');
  $(prefix + '.li-task.li-ok.table-3-n-cell').toggleClass('visibility-hidden');

  var taskGroups = $('.task-group');
  if (prefix.length > 0) {
    taskGroups = $(prefix).parent().parent().parent();
  }
  taskGroups.each(function(index) {
    var taskGroup = $(this);
    var taskList = $(taskGroup.children('.container-tasks')[0]).children('.tasks-list')[0];
    var tasks = $(taskList).children('li').toArray();
    var visibilityHidden = tasks.map(function(task) {
      return $(task).hasClass('visibility-hidden');
    });
    var i;
    for (i = 0; 3 * i < visibilityHidden.length; i++) {
      if (visibilityHidden[3 * i] && visibilityHidden[3 * i + 1] && visibilityHidden[3 * i + 2]) {
        $(tasks[3 * i]).toggleClass('display-none');
        $(tasks[3 * i]).toggleClass('visibility-hidden');
        $(tasks[3 * i + 1]).toggleClass('display-none');
        $(tasks[3 * i + 1]).toggleClass('visibility-hidden');
        $(tasks[3 * i + 2]).toggleClass('display-none');
        $(tasks[3 * i + 2]).toggleClass('visibility-hidden');
      }
    }
  });
  taskGroups.each(function(index) {
    var taskGroup = $(this);
    var taskList = $(taskGroup.children('.container-tasks')[0]).children('.tasks-list')[0];
    if ($(taskList).children('li').toArray().every(function(task) {
      return $(task).hasClass('li-ok');
    })) {
      taskGroup.toggleClass('display-none');
    }
  });
}

function toggleAllTasks() {
  toggleTasksWithPrefix("");
}

function updateTasks(taskToHtml) {
  for (var task_id in taskToHtml) {
    var html = taskToHtml[task_id];
  
    var taskHtmlId = '#task-' + task_id;
    var task = $(taskHtmlId);
  
    var was_ok = task.hasClass('li-ok');
    task.replaceWith(html);
    $(taskHtmlId + ' .attempt-form').on('submit', submitAttemptForm);
    $(taskHtmlId + ' .hint-attempt-form').on('submit', submitHintAttemptForm);
    $(taskHtmlId + ' .show-answer').on('click', showAnswer);
    $(taskHtmlId + ' .wall-tile-not-guessed').on('click', wallTileClick);
    $(taskHtmlId + ' .like-dislike').likeDislike({
      click: clickLikeDislike
    });
  
    task = $(taskHtmlId);
    var is_ok = task.hasClass('li-ok');
    if ($('.icon-ok-tasks').hasClass('fa-eye-slash') && is_ok != was_ok) {
      toggleTasksWithPrefix(taskHtmlId);
    }
  }
}

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
  
  updateTasks(data['update_task_html']);
}

function processNewHintAttempt(form, data) {
  if (data['status'] == 'duplicate') {
    alert('Вы уже запрашивали эту подсказку.');
    return;
  }
  if (data['status'] != 'ok') {
        console.log("Unknown status of attempt response: " + data['status']);
        return;
  }
  
  updateTasks(data['update_task_html']);
}

function submitAttemptForm(event) {
  event.preventDefault();
  var raw_form_data = $(this).serializeArray();
  var form_data = {};
  $.map(raw_form_data, function(n, i){
      form_data[n['name']] = n['value'];
  });
  param_form_data = $.param(form_data);
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
          processNewAttempt($(this), data);
      }
  });
}


function submitHintAttemptForm(event) {
  event.preventDefault();
  var task_id = $(this).children(".hint-task-id")[0].value;
  var hint_number = $(this).children(".hint-number")[0].value;
  
  var csrf = $(this).children("input[name=csrfmiddlewaretoken]")[0].value;

  var form_data = {
    'hint_number': hint_number
  };
  param_form_data = $.param(form_data);


  $.ajaxSetup({
      headers: { "X-CSRFToken": csrf }
  });
  $.ajax({
      type: 'POST',
      url: '/send_hint_attempt/' + task_id + '/',
      dataType: 'json',
      data: param_form_data,
      contentType : 'application/x-www-form-urlencoded',
      success: function(data) {
          processNewHintAttempt($(this), data);
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

function toggleOkTasks(event) {
  $('.icon-ok-tasks').toggleClass('fa-eye-slash');
  $('.icon-ok-tasks').toggleClass('fa-eye');
  toggleAllTasks();
}

function wallTileClick(event) {
  if ($(this).hasClass('wall-tile-stop-guessing')) {
    return;
  }
  $(this).toggleClass('wall-tile-selected');

  var wall = $($(this).parent());
  var task_id = wall.children(".attempt-task-id")[0].value;
  var csrf = wall.children("input[name=csrfmiddlewaretoken]")[0].value;

  var selectedTiles = $('#task-' + task_id + ' .wall-tile-selected').map(function(){
    return $(this).children('p')[0].innerText;
  }).toArray();
  if (selectedTiles.length != 4) {
    return;
  }
  
  var form_data = {
    'words': selectedTiles,
    'stage': 'cat_words'
  }

  $('#task-' + task_id + '.wall-tile-selected').toggleClass('wall-tile-selected');

  $.ajaxSetup({
    headers: { "X-CSRFToken": csrf }
  });
  $.ajax({
    type: 'POST',
    url: '/send_attempt/' + task_id + '/',
    dataType: 'json',
    data: form_data,
    contentType : 'application/x-www-form-urlencoded',
    success: function(data) {
        processNewAttempt($(this), data);
    }
  });
}

function clickLikeDislike(btnType, likes, dislikes, event) {
  var self = this;
  self.readOnly(true);
  var task = $(self.element).closest('.task');
  var task_id = $(task).children('.task-id')[0].value;
  var csrf = $(task).children("input[name=csrfmiddlewaretoken]")[0].value;
  var data = {
    'likes': likes,
    'dislikes': dislikes,
  }
  $.ajaxSetup({
    headers: { "X-CSRFToken": csrf }
});
  $.ajax({
    url: '/like_dislike/' + task_id + '/',
    type: 'POST',
    data: data,
    success: function (data) {
      // show new values
      $(self.element).find('.likes').text(data.likes);
      $(self.element).find('.dislikes').text(data.dislikes);
      // unlock the buttons
      self.readOnly(false);
    }
  });
}


// auto update this:
// $('#like-dislike').likeDislike({
//   // update like / dislike counters
//   click:function (btnType, likes, dislikes, event) {
//       var likesElem = $(this).find('.likes');
//       var dislikedsElem = $(this).find('.dislikes');
//       likesElem.text(parseInt(likesElem.text()) + likes);
//       dislikedsElem.text(parseInt(dislikedsElem.text()) + dislikes);
//   }
// });


$(document).ready(function() {
  $('.attempt-form').on('submit', submitAttemptForm);
  $('.hint-attempt-form').on('submit', submitHintAttemptForm);
  $('.show-answer').on('click', showAnswer);
  $('.toggle-ok-tasks').on('click', toggleOkTasks);
  $('.wall-tile-not-guessed').on('click', wallTileClick);
  $('.like-dislike').likeDislike({
    click: clickLikeDislike
  });
});

}

main();
