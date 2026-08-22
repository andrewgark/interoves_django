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
  var teamSelect = document.getElementById('new-pay-team');
  var telegramLinkForm = document.getElementById('new-pay-telegram-link-form');
  var terms = document.getElementById('new-pay-terms-link');
  var sellerText = document.getElementById('new-pay-seller-text');
  var sellerLink = document.getElementById('new-pay-seller-link');
  var security = document.getElementById('new-pay-security');
  var conversionNote = document.getElementById('new-pay-conversion-note');
  var tributeNote = document.getElementById('new-pay-tribute-note');
  var message = document.getElementById('new-pay-message');
  var statusEl = document.getElementById('new-pay-status');
  var widgetHost = document.getElementById('new-pay-widget-host');
  var yooMount = document.getElementById('new-yookassa-widget');
  var cryptoMount = document.getElementById('new-nowpayments-widget');
  var poller = null;
  var busy = false;
  var STORAGE_KEY = 'interoves_ticket_poll';
  var INTERNATIONAL_UNAVAILABLE_TEXT = 'Международные карты пока недоступны';
  if (!form || !qty) return;

  function flushAnalyticsEvents(events) {
    if (!window.interovesAnalytics || !window.interovesAnalytics.flushPendingGoals) return;
    return window.interovesAnalytics.flushPendingGoals(events || []) || [];
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
      security: 'Оплата криптовалютой проходит в защищенном виджете NOWPayments.'
    },
    tribute_card: {
      seller: form.getAttribute('data-tribute-seller') || 'Оплата через Tribute',
      sellerUrl: form.getAttribute('data-tribute-seller-url') || '/sellers/',
      termsUrl: '/terms/tribute/',
      security: 'Оплата проходит на защищенной странице Tribute. Inter Oves не получает и не хранит полные реквизиты банковской карты.'
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
      formatted = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value);
    } catch (e) {
      formatted = String(value);
    }
    if (currency === 'RUB') return formatted + ' ₽';
    if (currency === 'EUR') return formatted + ' €';
    if (currency === 'AMD') return formatted + ' ֏';
    return formatted + ' ' + currency;
  }

  function ticketLabel(count) {
    var mod10 = count % 10;
    var mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return count + ' билет';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return count + ' билета';
    return count + ' билетов';
  }

  var ANDREI_TG = 'https://t.me/andrewgark';

  function andreiTelegramLink(label) {
    var a = document.createElement('a');
    a.href = ANDREI_TG;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = label || 'Андрею в Telegram';
    return a;
  }

  function setMessage(text) {
    if (!message) return;
    message.textContent = '';
    if (!text) return;
    var idx = String(text).indexOf(ANDREI_TG);
    if (idx === -1) {
      message.textContent = text;
      return;
    }
    if (idx) message.appendChild(document.createTextNode(text.slice(0, idx)));
    message.appendChild(andreiTelegramLink('t.me/andrewgark'));
    var rest = text.slice(idx + ANDREI_TG.length);
    if (rest) message.appendChild(document.createTextNode(rest));
  }

  function setMessageWithAndrei(before, after) {
    if (!message) return;
    message.textContent = '';
    if (before) message.appendChild(document.createTextNode(before));
    message.appendChild(andreiTelegramLink());
    if (after) message.appendChild(document.createTextNode(after));
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
    var internationalUnavailable = route === 'international_card';
    var tributeRoute = route === 'tribute_card';
    var tributeEnabled = form.getAttribute('data-tribute-enabled') === '1';
    var telegramLinked = form.getAttribute('data-telegram-linked') === '1';
    var copy = routeCopy[route];
    var count = tributeRoute ? 1 : clampQuantity(qty.value);
    qty.value = String(count);
    if (hiddenQty) hiddenQty.value = String(count);
    var amount = count * Number(input.getAttribute('data-unit-price') || 0);
    var currency = input.getAttribute('data-currency') || 'RUB';
    var amountText = formatAmount(amount, currency);
    var unitPriceText = formatAmount(Number(input.getAttribute('data-unit-price') || 0), currency);

    qty.setAttribute('aria-valuetext', ticketLabel(count));
    qty.disabled = tributeRoute;
    if (qtyMinus) qtyMinus.disabled = tributeRoute || count <= 1;
    if (qtyPlus) qtyPlus.disabled = tributeRoute || count >= 20;

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
    if (conversionNote) conversionNote.hidden = !internationalUnavailable;
    if (tributeNote) tributeNote.hidden = !tributeRoute;

    if (submit) {
      if (internationalUnavailable) submit.textContent = INTERNATIONAL_UNAVAILABLE_TEXT;
      else if (tributeRoute && !tributeEnabled) submit.textContent = 'Этот способ пока недоступен';
      else if (tributeRoute && !telegramLinked) submit.textContent = 'Привязать Telegram для оплаты';
      else if (tributeRoute) submit.textContent = 'Оплатить через Tribute · ' + amountText;
      else submit.textContent = 'Оплатить ' + amountText;
      submit.disabled = busy || internationalUnavailable || (tributeRoute && !tributeEnabled)
        || (!tributeRoute || telegramLinked) && !consent.checked;
      submit.classList.toggle('new-pay-submit--unavailable', internationalUnavailable || (tributeRoute && !tributeEnabled));
    }
    if (login) {
      login.textContent = internationalUnavailable ? INTERNATIONAL_UNAVAILABLE_TEXT
        : (tributeRoute ? 'Войти и привязать Telegram' : 'Войти и продолжить');
      login.disabled = internationalUnavailable || (tributeRoute && !tributeEnabled);
      login.classList.toggle('new-pay-submit--unavailable', internationalUnavailable || (tributeRoute && !tributeEnabled));
    }
    if (teamSetup) {
      teamSetup.textContent = internationalUnavailable ? INTERNATIONAL_UNAVAILABLE_TEXT : 'Создать команду для оплаты';
      teamSetup.classList.toggle('is-disabled', internationalUnavailable);
      teamSetup.classList.toggle('new-pay-submit--unavailable', internationalUnavailable);
      teamSetup.setAttribute('aria-disabled', internationalUnavailable ? 'true' : 'false');
    }
    if (internationalUnavailable) {
      setMessage('Оплата международными картами пока недоступна.');
      hideWidgets();
    } else if (tributeRoute && !tributeEnabled) {
      setMessage('Этот способ оплаты пока недоступен.');
      hideWidgets();
    } else if (tributeRoute && !telegramLinked) {
      setMessage('');
      hideWidgets();
    } else if (tributeRoute) {
      setMessage('');
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
    statusEl.textContent = '';
    statusEl.appendChild(document.createTextNode('Платеж отменен или отклонен. Если средства списались, напишите '));
    statusEl.appendChild(andreiTelegramLink());
    statusEl.appendChild(document.createTextNode('.'));
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
        flushAnalyticsEvents(data && data.analytics_events);
        renderAccepted(data);
        setMessage('');
      },
      onRejected: renderRejected,
      onTimeout: function () { setMessage('Подтверждение идёт дольше обычного. Билет появится, когда платёж дойдёт — можно не ждать на этой странице.'); },
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
  if (teamSelect) {
    teamSelect.addEventListener('change', function () {
      var option = teamSelect.options[teamSelect.selectedIndex];
      if (!option) return;
      form.querySelectorAll('input[name="payment_method"]').forEach(function (radio) {
        if (radio.value === 'russian_card' || radio.value === 'crypto') {
          radio.setAttribute('data-unit-price', option.getAttribute('data-ticket-price') || '0');
        } else if (radio.value === 'international_card') {
          radio.setAttribute('data-unit-price', option.getAttribute('data-ticket-price-amd') || '0');
        } else if (radio.value === 'tribute_card') {
          radio.setAttribute('data-unit-price', option.getAttribute('data-tribute-amount') || '0');
          radio.setAttribute('data-currency', option.getAttribute('data-tribute-currency') || 'EUR');
        }
        var card = radio.closest('[data-pay-method-card]');
        var price = card && card.querySelector('[data-route-price]');
        if (price && Number(radio.getAttribute('data-unit-price') || 0) > 0) {
          price.textContent = formatAmount(
            Number(radio.getAttribute('data-unit-price')),
            radio.getAttribute('data-currency') || 'RUB'
          ) + (radio.value === 'tribute_card' ? ' · 1 покупка = 1 билет' : ' за билет');
        }
      });
      render();
    });
  }
  if (teamSetup) {
    teamSetup.addEventListener('click', function (event) {
      if (teamSetup.getAttribute('aria-disabled') === 'true') event.preventDefault();
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var input = selectedInput();
    if (!input || busy || input.value === 'international_card') return;
    if (input.value === 'tribute_card' && form.getAttribute('data-telegram-linked') !== '1') {
      if (telegramLinkForm) telegramLinkForm.submit();
      return;
    }
    if (!consent || !consent.checked) {
      setMessage('Подтвердите согласие с условиями покупки.');
      if (consent) consent.focus();
      return;
    }

    setBusy(true);
    hideWidgets();
    setMessage('Создаем платеж…');
    var endpoint = input.value === 'crypto'
      ? form.getAttribute('data-crypto-action')
      : (input.value === 'tribute_card' ? form.getAttribute('data-tribute-action') : form.action);
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
      if (input.value === 'tribute_card') {
        if (!data.payment_url) {
          setMessageWithAndrei('Не удалось открыть оплату Tribute. Напишите ', '.');
          return;
        }
        setMessage('На странице Tribute выберите Telegram, не email.');
        window.location.assign(data.payment_url);
        return;
      }
      if (input.value === 'crypto') {
        if (!cryptoMount || !(data.embed_url || data.invoice_id)) {
          setMessage('Не удалось открыть страницу оплаты. Попробуйте ещё раз.');
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
        setMessage('Завершите оплату в виджете. Статус обновится на этой странице.');
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
