(function () {
  'use strict';

  function csrfToken(form) {
    var input = form.querySelector('input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function showError(form, message) {
    var err = document.getElementById('word-salad-edit-error');
    if (!err) {
      alert(message);
      return;
    }
    err.textContent = message;
    err.hidden = false;
  }

  function clearError() {
    var err = document.getElementById('word-salad-edit-error');
    if (!err) {
      return;
    }
    err.textContent = '';
    err.hidden = true;
  }

  function responseTextSnippet(text) {
    var clean = (text || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    if (!clean) {
      return '';
    }
    return clean.slice(0, 240);
  }

  function postForm(url, form) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': csrfToken(form),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: new FormData(form)
    }).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : {};
        } catch (e) {
          data = null;
        }
        if (!resp.ok) {
          throw new Error(
            (data && data.error) ||
            responseTextSnippet(text) ||
            ('HTTP ' + resp.status)
          );
        }
        if (!data || !data.ok) {
          throw new Error(
            (data && data.error) ||
            responseTextSnippet(text) ||
            'Сервер вернул неожиданный ответ'
          );
        }
        return data;
      });
    });
  }

  function initCreate() {
    var form = document.getElementById('word-salad-create-form');
    if (!form) {
      return;
    }
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      postForm(form.action, form).then(function (data) {
        window.location.search = '?edit=' + encodeURIComponent(data.detail.link_id);
      }).catch(function (err) {
        alert(err.message);
      });
    });
  }

  function initEdit() {
    var form = document.getElementById('word-salad-edit-form');
    if (!form) {
      return;
    }
    var deleteBtn = document.getElementById('word-salad-delete-btn');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      clearError();
      postForm(form.action, form).then(function (data) {
        window.location.search = '?edit=' + encodeURIComponent(data.detail.link_id);
      }).catch(function (err) {
        showError(form, err.message);
      });
    });
    if (deleteBtn) {
      deleteBtn.addEventListener('click', function () {
        if (!window.confirm('Удалить этот Word Salad?')) {
          return;
        }
        clearError();
        postForm(form.dataset.deleteUrl, form).then(function () {
          window.location.search = '';
        }).catch(function (err) {
          showError(form, err.message);
        });
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    initCreate();
    initEdit();
  });
}());
