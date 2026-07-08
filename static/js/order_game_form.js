(function () {
  var PLACEHOLDERS = {
    telegram: '@username',
    email: 'email@company.com',
    other: '+7 …, ссылка или ник',
  };

  function updateContactForm(contactFieldset) {
    var methodInput = contactFieldset.querySelector('input[name="contact_method"]:checked');
    var valueInput = contactFieldset.querySelector('#id_contact_value');
    if (!methodInput || !valueInput) {
      return;
    }
    var method = methodInput.value;
    valueInput.placeholder = PLACEHOLDERS[method] || '';
    if (method === 'email') {
      valueInput.type = 'email';
      valueInput.autocomplete = 'email';
    } else {
      valueInput.type = 'text';
      valueInput.autocomplete = method === 'telegram' ? 'username' : 'off';
    }
  }

  function initContactForm(contactFieldset) {
    updateContactForm(contactFieldset);
    contactFieldset.addEventListener('change', function (event) {
      if (event.target && event.target.name === 'contact_method') {
        updateContactForm(contactFieldset);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(
      document.querySelectorAll('.corp-form__contact'),
      initContactForm
    );
  });
})();
