(function () {
  'use strict';

  var form = document.getElementById('new-pay-ticket-form');
  var qty = document.getElementById('new-pay-qty');
  var qtyMinus = document.querySelector('[data-pay-qty-minus]');
  var qtyPlus = document.querySelector('[data-pay-qty-plus]');
  var hiddenQty = form && form.querySelector('[data-pay-tickets]');
  var total = document.getElementById('new-pay-total');
  var totalBreakdown = document.getElementById('new-pay-total-breakdown');
  var consent = document.getElementById('new-pay-consent');
  var submit = document.getElementById('new-pay-submit');
  var login = document.getElementById('new-pay-login');
  var teamSetup = document.getElementById('new-pay-setup-team');
  var terms = document.getElementById('new-pay-terms-link');
  var sellerText = document.getElementById('new-pay-seller-text');
  var sellerLink = document.getElementById('new-pay-seller-link');
  var security = document.getElementById('new-pay-security');
  var conversionNote = document.getElementById('new-pay-conversion-note');
  var message = document.getElementById('new-pay-message');
  var statusEl = document.getElementById('new-pay-status');
  var widgetHost = document.getElementById('new-pay-widget-host');
  var yooMount = document.getElementById('new-yookassa-widget');
  var cryptoMount = document.getElementById('new-nowpayments-widget');
  var poller = null;
  var busy = false;
  var STORAGE_KEY = 'interoves_ticket_poll';
  if (!form || !qty) return;

  function flushAnalyticsEvents(events) {
    if (!window.interovesAnalytics || !window.interovesAnalytics.flushPendingGoals) return;
    return window.interovesAnalytics.flushPendingGoals(events || []) || [];
  }

  function ackAnalyticsGoal(statusUrl, goalKey) {
    if (!statusUrl || !goalKey) return Promise.resolve(false);
    return fetch(statusUrl, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrfToken(),
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
      },
      body: new URLSearchParams({ analytics_ack: goalKey }).toString(),
      credentials: 'same-origin'
    }).then(function (response) {
      return response.ok;
    }).catch(function () {
      return false;
    });
  }

  var routeCopy = {
    russian_card: {
      seller: 'Продавец: Андрей Гаркавый, плательщик НПД, РФ',
      sellerUrl: '/sellers/#russia',
      termsUrl: '/terms/russia/',
      security: 'Оплата российской картой проходит в защищенном виджете ЮKassa. Inter Oves не получает и не хранит полные реквизиты банковской карты.'
    },
    international_card: {
      seller: 'Продавец: Andrei Garkavyi IE, Republic of Armenia',
      sellerUrl: '/sellers/#armenia',
      termsUrl: '/terms/armenia/',
      security: 'Оплата будет проходить на защищенной странице банка. Inter Oves не будет получать или хранить полные реквизиты банковской карты.'
    },
    crypto: {
      seller: 'Продавец: Андрей Гаркавый, плательщик НПД, РФ',
      sellerUrl: '/sellers/#russia',
      termsUrl: '/terms/crypto/',
      security: 'Криптоплатеж обрабатывает NOWPayments. Сумма к отправке и адрес кошелька отображаются в защищенном виджете провайдера.'
    }
  };

  function selectedInput() {
    return form.querySelector('input[name="payment_method"]:checked');
  }

  function clampQuantity(value) {
    var n = Number(value || 1);
    if (!isFinite(n)) n = 1;
    return Math.max(1, Math.min(20, Math.round(n)));
  }

  function formatAmount(value, currency) {
    var formatted;
    try {
      formatted = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(value);
    } catch (e) {
      formatted = String(value);
    }
    return formatted + (currency === 'RUB' ? ' ₽' : ' AMD');
  }

  function ticketLabel(count) {
    var mod10 = count % 10;
    var mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return count + ' билет';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return count + ' билета';
    return count + ' билетов';
  }

  function setMessage(text) {
    if (message) message.textContent = text || '';
  }

  function hideWidgets() {
    if (widgetHost) widgetHost.hidden = true;
    if (yooMount) yooMount.innerHTML = '';
    if (cryptoMount) cryptoMount.innerHTML = '';
  }

  function setBusy(value) {
    busy = !!value;
    render();
  }

  function render() {
    var input = selectedInput();
    if (!input) return;
    var route = input.value;
    var copy = routeCopy[route];
    var count = clampQuantity(qty.value);
    qty.value = String(count);
    if (hiddenQty) hiddenQty.value = String(count);
    var amount = count * Number(input.getAttribute('data-unit-price') || 0);
    var currency = input.getAttribute('data-currency') || 'RUB';
    var amountText = formatAmount(amount, currency);
    var unitPriceText = formatAmount(Number(input.getAttribute('data-unit-price') || 0), currency);

    qty.setAttribute('aria-valuetext', ticketLabel(count));
    if (qtyMinus) qtyMinus.disabled = count <= 1;
    if (qtyPlus) qtyPlus.disabled = count >= 20;

    form.querySelectorAll('[data-pay-method-card]').forEach(function (card) {
      var radio = card.querySelector('input[name="payment_method"]');
      card.classList.toggle('is-selected', !!radio && radio.checked);
    });
    if (total) total.textContent = amountText;
    if (totalBreakdown) totalBreakdown.textContent = ticketLabel(count) + ' × ' + unitPriceText;
    if (terms) terms.href = copy.termsUrl;
    if (sellerText) sellerText.textContent = copy.seller;
    if (sellerLink) sellerLink.href = copy.sellerUrl;
    if (security) security.textContent = copy.security;
    if (conversionNote) conversionNote.hidden = route !== 'international_card';

    if (submit) {
      submit.textContent = route === 'international_card' ? 'Международная оплата готовится' : 'Оплатить ' + amountText;
      submit.disabled = busy || route === 'international_card' || !consent.checked;
    }
    if (login) {
      login.textContent = route === 'international_card' ? 'Международная оплата готовится' : 'Войти и продолжить';
      login.disabled = route === 'international_card';
    }
    if (teamSetup) {
      teamSetup.textContent = route === 'international_card' ? 'Международная оплата готовится' : 'Создать команду для оплаты';
      teamSetup.classList.toggle('is-disabled', route === 'international_card');
      teamSetup.setAttribute('aria-disabled', route === 'international_card' ? 'true' : 'false');
    }
    if (route === 'international_card') {
      setMessage('Подключение приема международных карт готовится. Цена, продавец и условия показаны для ознакомления.');
      hideWidgets();
    } else if (!busy) {
      setMessage('');
    }
  }

  function csrfToken() {
    var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function parseJsonResponse(response) {
    return response.text().then(function (text) {
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) {}
      return { response: response, data: data };
    });
  }

  function renderPending() {
    if (!statusEl) return;
    statusEl.className = 'new-payment-status new-payment-status--pending';
    statusEl.textContent = 'Платеж обрабатывается…';
    statusEl.hidden = false;
  }

  function renderAccepted(data) {
    if (!statusEl) return;
    statusEl.className = 'new-payment-status new-payment-status--ok';
    statusEl.textContent = 'Оплата подтверждена. Билеты зачислены команде.';
    var ticketsNow = document.getElementById('new-pay-team-tickets');
    if (ticketsNow && data && typeof data.team_tickets !== 'undefined') {
      ticketsNow.textContent = String(data.team_tickets);
    }
    statusEl.hidden = false;
  }

  function renderRejected() {
    if (!statusEl) return;
    statusEl.className = 'new-payment-status new-payment-status--err';
    statusEl.textContent = 'Платеж отменен или отклонен. Если средства списались, напишите в поддержку.';
    statusEl.hidden = false;
  }

  function startPoll(url) {
    if (!window.InterovesPaymentPoll || !url) return;
    if (poller) poller.stop();
    poller = window.InterovesPaymentPoll.start({
      statusUrl: url,
      storageKey: STORAGE_KEY,
      onPending: renderPending,
      onConfirmed: function (data) {
        var sentKeys = flushAnalyticsEvents(data && data.analytics_events);
        var purchaseKey = 'ticket_purchase:' + (data && data.ticket_request_id ? data.ticket_request_id : '');
        if (sentKeys && sentKeys.indexOf(purchaseKey) >= 0) {
          ackAnalyticsGoal(url, purchaseKey);
        }
        renderAccepted(data);
        setMessage('');
      },
      onRejected: renderRejected,
      onTimeout: function () { setMessage('Подтверждение занимает дольше обычного. Билеты зачислятся после callback платежного провайдера.'); },
      isConfirmed: function (data) { return data && data.status === 'Accepted'; },
      isRejected: function (data) { return data && data.status === 'Rejected'; }
    });
  }

  form.querySelectorAll('input[name="payment_method"]').forEach(function (input) {
    input.addEventListener('change', render);
  });
  qty.addEventListener('input', render);
  qty.addEventListener('change', render);
  if (qtyMinus) {
    qtyMinus.addEventListener('click', function () {
      qty.value = String(clampQuantity(clampQuantity(qty.value) - 1));
      render();
      qty.focus();
    });
  }
  if (qtyPlus) {
    qtyPlus.addEventListener('click', function () {
      qty.value = String(clampQuantity(clampQuantity(qty.value) + 1));
      render();
      qty.focus();
    });
  }
  if (consent) consent.addEventListener('change', render);
  if (teamSetup) {
    teamSetup.addEventListener('click', function (event) {
      if (teamSetup.getAttribute('aria-disabled') === 'true') event.preventDefault();
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var input = selectedInput();
    if (!input || busy || input.value === 'international_card') return;
    if (!consent || !consent.checked) {
      setMessage('Подтвердите согласие с условиями покупки.');
      if (consent) consent.focus();
      return;
    }

    setBusy(true);
    hideWidgets();
    setMessage('Создаем платеж…');
    var endpoint = input.value === 'crypto' ? form.getAttribute('data-crypto-action') : form.action;
    fetch(endpoint, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrfToken()
      },
      body: new FormData(form),
      credentials: 'same-origin'
    }).then(parseJsonResponse).then(function (result) {
      var data = result.data;
      setBusy(false);
      if (!data || data.status !== 'ok') {
        setMessage((data && data.message) || 'Не удалось создать платеж. Попробуйте еще раз.');
        return;
      }
      flushAnalyticsEvents(data.analytics_events);
      if (data.status_url) startPoll(data.status_url);
      if (input.value === 'crypto') {
        if (!cryptoMount || !(data.embed_url || data.invoice_id)) {
          setMessage('Провайдер не вернул страницу оплаты.');
          return;
        }
        var iframe = document.createElement('iframe');
        iframe.src = data.embed_url || ('https://nowpayments.io/embeds/payment-widget?iid=' + encodeURIComponent(data.invoice_id));
        iframe.width = '410';
        iframe.height = '696';
        iframe.title = 'Оплата криптовалютой через NOWPayments';
        iframe.setAttribute('frameborder', '0');
        iframe.style.maxWidth = '100%';
        cryptoMount.appendChild(iframe);
        if (widgetHost) widgetHost.hidden = false;
        setMessage('Завершите оплату в виджете провайдера. Подтверждение сети может занять некоторое время.');
        return;
      }
      if (!data.confirmation_token || !window.YooMoneyCheckoutWidget || !yooMount) {
        setMessage('Не удалось загрузить виджет ЮKassa.');
        return;
      }
      if (widgetHost) widgetHost.hidden = false;
      try {
        var checkout = new window.YooMoneyCheckoutWidget({
          confirmation_token: data.confirmation_token,
          return_url: data.return_url || window.location.href,
          error_callback: function () { setMessage('ЮKassa сообщила об ошибке. Попробуйте еще раз.'); }
        });
        checkout.render('new-yookassa-widget');
        setMessage('Завершите оплату в защищенном виджете ЮKassa.');
      } catch (error) {
        setMessage('Не удалось открыть виджет ЮKassa. Обновите страницу и попробуйте снова.');
      }
    }).catch(function () {
      setBusy(false);
      setMessage('Ошибка сети. Проверьте соединение и попробуйте еще раз.');
    });
  });

  if (window.InterovesPaymentPoll) {
    var saved = window.InterovesPaymentPoll.readStorage(STORAGE_KEY);
    if (saved && saved.statusUrl) startPoll(saved.statusUrl);
  }
  render();
})();
