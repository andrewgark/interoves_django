var kassaConstructForm = kassaConstructForm || {};
(function(namespace) {
    if (typeof namespace.main !== "undefined") {
        namespace.main.updateHandlers();
        return namespace;
    }

    namespace.main = (function () {
        const COUNT_STEP = 1;

        const _suppMethods =  {
            formatPrice(price) {
                return new Intl.NumberFormat('ru-RU', {style: "currency", currency: "RUB"}).format(price);
            },
            countFilter(input) {
                input.value = parseFloat(input.value).toFixed(2);
                return this;
            },
            calculateProductPrice(input) {
                let productPriceOutput = input.findParents('.ym-product').querySelector('.ym-product-price');
                let price = parseFloat(input.value) * parseFloat(productPriceOutput.dataset.price);
                productPriceOutput.innerText = _suppMethods.formatPrice(price);
                return this;
            },
            calculateFullPrice(form) {
                let resultPriceOutput = form.querySelector('.ym-result-price');
                let productsPrice = form.querySelectorAll('.ym-product-price');

                if (productsPrice.length < 1) {
                    return this;
                }

                let fullPrice = 0;
                productsPrice.forEach(function (productLine) {
                    let price = productLine.textContent.replace(/\s/g, '').replace(',', '.');
                    fullPrice += parseFloat(price);
                })
                form.querySelector('.ym-sum-input').value = fullPrice;
                if (resultPriceOutput !== null) {
                    resultPriceOutput.querySelector('.ym-text-crop').innerText = resultPriceOutput.dataset.text;
                    resultPriceOutput.querySelector('.ym-price-output').innerText = _suppMethods.formatPrice(fullPrice);
                }
                return this;
            },
            validatePaymentForm(form) {
                let errors = [];
                let contactInput = null;

                if (this.isNeedReceipt(form)) {
                    contactInput = this.getAvailableCustomerContact(form);
                }

                const contactPattern = /^([\+]{0,1}[0-9]{9})|((([^<>()[\]\\.,;:\s@"]+(\.[^<>()[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([^<>()[\]\.,;:\s@\"]+\.)+[^<>()[\]\.,;:\s@\"]{2,})))$/;
                if (contactInput !== null && !contactPattern.test(String(contactInput.value).toLowerCase())) {
                    let inputType = contactInput.getAttribute('name');

                    let errorText = "Укажите телефон или email для чека";
                    if (inputType === "cps_phone") {
                        errorText = "Укажите телефон для чека";
                    } else if (inputType === "cps_email") {
                        errorText = "Укажите email для чека";
                    }

                    errors.push({
                        field: contactInput,
                        text: errorText,
                    });
                }

                let sumInput = form.querySelector('.ym-sum-input');
                if (sumInput.value <= 0) {
                    errors.push({
                        field: sumInput,
                        text: 'Укажите сумму',
                    });
                }

                return errors;
            },
            getAvailableCustomerContact(form) {
                const contacts = [
                    form.querySelector('input[name="email"]'),
                    form.querySelector('input[name="cps_phone"]'),
                    form.querySelector('input[name="cps_email"]'),
                ];

                const defaultContact = contacts[2] || contacts[1] || contacts[0];

                let contact = null;
                contacts.forEach(function (input) {
                    if (input && input.value) {
                        contact = input;
                    }
                });
                return contact || defaultContact;
            },
            formErrorHandler(errors) {
                errors.forEach(function (error) {
                    let wrapper = document.createElement('div');
                    wrapper.innerHTML = '<div class="ym-hint">'+error.text+'</div>';
                    wrapper.appendChild(error.field.cloneNode());
                    wrapper.classList.add('ym-block-with-hint');
                    error.field.parentNode.replaceChild(wrapper, error.field)
                    wrapper.classList.add('ym-visible-hint');
                    setTimeout(() => (wrapper.classList.remove('ym-visible-hint')), 5000);
                })
            },
            isNeedReceipt(form) {
                let products = form.querySelectorAll('.ym-product');
                return products.length > 0;
            },
            buildReceipt(form) {
                let receipt = {customer: {}, items:[]};
                let contactInput = this.getAvailableCustomerContact(form);

                if (contactInput.value.indexOf('@') > -1) {
                    receipt.customer.email = contactInput.value;
                } else {
                    receipt.customer.phone = contactInput.value;
                }

                let products = form.querySelectorAll('.ym-product');
                products.forEach(function (product) {
                    receipt.items.push({
                        text: product.querySelector('input[name="text"]').value,
                        quantity: product.querySelector('input[name="quantity"]').value,
                        price: {
                            amount: product.querySelector('input[name="price"]').value
                        },
                        paymentSubjectType: product.querySelector('input[name="paymentSubjectType"]').value,
                        paymentMethodType: product.querySelector('input[name="paymentMethodType"]').value,
                        tax: product.querySelector('input[name="tax"]').value,
                    })
                })

                return JSON.stringify(receipt);
            }
        }

        const _handlers = {
            countPlus(e) {
                let input = e.target.parentNode.querySelector('.ym-input');
                input.value = parseFloat(input.value) + COUNT_STEP;
                input.findParents('.ym-product').querySelector('input[name="quantity"]').value = input.value;
                input.dispatchEvent(eventChange);
            },
            countMinus(e) {
                let input = e.target.parentNode.querySelector('.ym-input');
                if (input.value <= 0) {
                    return;
                }
                input.value = parseFloat(input.value) - COUNT_STEP;
                input.findParents('.ym-product').querySelector('input[name="quantity"]').value = input.value;
                input.dispatchEvent(eventChange);
            },
            afterChangeInput(e) {
                let input = e.target;
                let form = input.findParents('.yoomoney-payment-form');
                e.target.value = parseFloat(e.target.value).toFixed(2);
                _suppMethods.countFilter(input).calculateProductPrice(input).calculateFullPrice(form);
            },
            sendForm(e) {
                e.preventDefault();
                let form = e.target;


                let errors = _suppMethods.calculateFullPrice(form).validatePaymentForm(form);
                if (errors.length > 0) {
                    _suppMethods.formErrorHandler(errors)
                    return;
                }

                if (_suppMethods.isNeedReceipt(form)) {
                    form.querySelector('input[name="ym_merchant_receipt"]').value = _suppMethods.buildReceipt(form);
                }

                var request_ticket_data = {
                    'tickets': form.querySelectorAll('.ym-product')[0].querySelector('input[name="quantity"]').value,
                    'money': form.querySelector('.ym-sum-input').value,
                    'yookassa_id': form.querySelector('input[name="customerNumber"]').value
                }
                var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
                $.ajaxSetup({
                  headers: { "X-CSRFToken": csrf }
                });
                $.ajax({
                  url: '/request_ticket/',
                  type: 'POST',
                  data: request_ticket_data
                });
                form.submit();
            }
        }

        const eventChange = new Event('change', {
            bubbles: true,
            cancelable: true,
        });

        let _forms;

        /**
         * Constructor
         */
        (function __construct() {
            _setGlobalFunctions();
            _setForms();
            _initHandlers();
        })();

        return {
            updateHandlers() {
                _setForms();
                _forms.forEach(function (form) {
                    _initFormHandlers(form);
                    _initInputsHandlers(form);
                    _calculateDefaultValues(form);
                });
            }
        };

        function _setForms() {
            _forms = document.querySelectorAll('.yoomoney-payment-form');
        }

        function _initHandlers() {
            _forms.forEach(function (form) {
                _initFormHandlers(form);
                _initInputsHandlers(form);
                _calculateDefaultValues(form);
                _suppMethods.calculateFullPrice(form);
            })
        }

        function _initFormHandlers(form) {
            if (form.hasAttribute('data-active-listeners')) {
                return;
            }
            form.addEventListener('submit', _handlers.sendForm.bind(_handlers));
            form.setAttribute('data-active-listeners', 'true');
        }

        function _initInputsHandlers(form) {
            let inputs = form.querySelectorAll('.ym-count-input');
            inputs.forEach(function (input) {
                let inputText = input.querySelector('.ym-input');
                if (inputText.hasAttribute('data-active-listeners')) {
                    return;
                }
                input.querySelector('.ym-count-plus').addEventListener('click', _handlers.countPlus.bind(_handlers));
                input.querySelector('.ym-count-minus').addEventListener('click', _handlers.countMinus.bind(_handlers));
                inputText.addEventListener('change', _handlers.afterChangeInput.bind(_handlers));
                inputText.setAttribute('data-active-listeners', 'true');
            })
        }

        function _calculateDefaultValues(form) {
            setTimeout(function (){
                let inputs = form.querySelectorAll('.ym-count-input');
                inputs.forEach(function (input) {
                    input.querySelector('.ym-input').dispatchEvent(eventChange);
                })
            }, 500)
        }

        function _setGlobalFunctions() {
            Element.prototype.findParents = function(selector) {
                let element = this;

                while ((element = element.parentElement) !== null) {
                    if (element.nodeType !== Node.ELEMENT_NODE) {
                        continue;
                    }

                    if (element.matches(selector)) {
                        return element;
                    }
                }
            };
        }
    })()
})(kassaConstructForm)