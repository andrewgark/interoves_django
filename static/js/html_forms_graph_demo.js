(function () {
  'use strict';

  var schemaSelector = '[data-graph-schema="categorka-schema"], [data-html-forms-graph-demo]';

  var nodes = [
    {id: 'G1', type: 'green', text: '_______а_', x: 34.4, y: 5},
    {id: 'G2', type: 'green', text: '______', x: 65.6, y: 5},
    {id: 'G3', type: 'green', text: '____', x: 34.4, y: 29},
    {id: 'G4', type: 'green', text: '_____', x: 65.6, y: 29},
    {id: 'G5', type: 'green', text: '____', x: 7.4, y: 51},
    {id: 'G6', type: 'green', text: '___', x: 92.6, y: 51},
    {id: 'G7', type: 'green', text: '__ф_______', x: 36.5, y: 51},
    {id: 'G8', type: 'green', text: '___', x: 64.5, y: 51},
    {id: 'G9', type: 'green', text: '______', x: 34.4, y: 73},
    {id: 'G10', type: 'green', text: '_______', x: 65.6, y: 73},
    {id: 'G11', type: 'green', text: '_________', x: 34.4, y: 97},
    {id: 'G12', type: 'green', text: '______р', x: 65.6, y: 97},
    {id: 'Y1', type: 'yellow', text: 'Ц__А', x: 50, y: 5},
    {id: 'Y2', type: 'yellow', text: 'В_____ж', x: 34.4, y: 17},
    {id: 'Y3', type: 'yellow', text: 'Новокуз___кая', x: 65.6, y: 17},
    {id: 'Y4', type: 'yellow', text: 'Я____б', x: 50, y: 29},
    {id: 'Y5', type: 'yellow', text: 'К_______я', x: 19.8, y: 40},
    {id: 'Y6', type: 'yellow', text: 'Х__к', x: 80.2, y: 40},
    {id: 'Y7', type: 'yellow', text: 'Твинд_к', x: 22, y: 51},
    {id: 'Y8', type: 'yellow', text: 'Коронк_', x: 51, y: 51},
    {id: 'Y9', type: 'yellow', text: 'Б__ь__', x: 79, y: 51},
    {id: 'Y10', type: 'yellow', text: 'С__ц', x: 19.8, y: 62},
    {id: 'Y11', type: 'yellow', text: 'С______о', x: 50, y: 73},
    {id: 'Y12', type: 'yellow', text: 'М__и_о', x: 34.4, y: 85},
    {id: 'Y13', type: 'yellow', text: 'Ка__юратор', x: 65.6, y: 85},
    {id: 'Y14', type: 'yellow', text: 'О___а', x: 50, y: 97},
    {id: 'Y15', type: 'yellow', text: 'Сфе__', x: 80.2, y: 62}
  ];

  var segments = [
    {start: 'G1', middle: 'Y1', end: 'G2'},
    {start: 'G1', middle: 'Y2', end: 'G3'},
    {start: 'G2', middle: 'Y3', end: 'G4'},
    {start: 'G3', middle: 'Y4', end: 'G4'},
    {start: 'G3', middle: 'Y5', end: 'G5'},
    {start: 'G4', middle: 'Y6', end: 'G6'},
    {start: 'G5', middle: 'Y7', end: 'G7'},
    {start: 'G7', middle: 'Y8', end: 'G8'},
    {start: 'G8', middle: 'Y9', end: 'G6'},
    {start: 'G5', middle: 'Y10', end: 'G9'},
    {start: 'G6', middle: 'Y15', end: 'G10'},
    {start: 'G9', middle: 'Y11', end: 'G10'},
    {start: 'G9', middle: 'Y12', end: 'G11'},
    {start: 'G10', middle: 'Y13', end: 'G12'},
    {start: 'G11', middle: 'Y14', end: 'G12'}
  ];

  function nodeText(element, value) {
    value.split('').forEach(function (character) {
      if (character === '_') {
        var blank = document.createElement('span');
        blank.className = 'html-forms-graph-demo__blank';
        blank.textContent = '▪';
        element.appendChild(blank);
      } else {
        element.appendChild(document.createTextNode(character));
      }
    });
  }

  function drawConnections(stage, svg, elements) {
    var stageRect = stage.getBoundingClientRect();
    svg.replaceChildren();
    segments.forEach(function (segment) {
      [[segment.start, segment.middle], [segment.middle, segment.end]].forEach(function (pair) {
        var first = elements[pair[0]].getBoundingClientRect();
        var second = elements[pair[1]].getBoundingClientRect();
        var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('class', 'html-forms-graph-demo__line');
        line.setAttribute('x1', first.left + first.width / 2 - stageRect.left);
        line.setAttribute('y1', first.top + first.height / 2 - stageRect.top);
        line.setAttribute('x2', second.left + second.width / 2 - stageRect.left);
        line.setAttribute('y2', second.top + second.height / 2 - stageRect.top);
        svg.appendChild(line);
      });
    });
  }

  function render(root) {
    if (root.dataset.graphRendered === '1') return;
    root.dataset.graphRendered = '1';
    var formTemplates = {};
    root.querySelectorAll('template[data-graph-form]').forEach(function (template) {
      formTemplates[template.dataset.graphForm] = template;
    });
    var stage = document.createElement('div');
    stage.className = 'html-forms-graph-demo__stage';
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('html-forms-graph-demo__svg');
    svg.setAttribute('aria-hidden', 'true');
    stage.appendChild(svg);

    var elements = {};
    nodes.forEach(function (node) {
      var element = document.createElement('div');
      element.className = 'html-forms-graph-demo__node html-forms-graph-demo__node--' + node.type;
      element.style.left = node.x + '%';
      element.style.top = node.y + '%';
      element.setAttribute('aria-label', node.text.replace(/_/g, ' пропуск '));
      if (formTemplates[node.id]) {
        element.classList.add('html-forms-graph-demo__node--answer');
        var solvedAnswer = formTemplates[node.id].content.querySelector('[data-html-form-answer-value]');
        if (solvedAnswer) {
          element.classList.add('html-forms-graph-demo__node--solved');
          element.textContent = solvedAnswer.textContent;
        } else {
          element.appendChild(formTemplates[node.id].content.cloneNode(true));
          element.querySelectorAll('input[name="text"]').forEach(function (input) {
            var placeholderLength = Number(input.getAttribute('size')) || 0;
            input.style.width = Math.max(7, Math.min(placeholderLength + 2, 14)) + 'ch';
          });
        }
      } else {
        nodeText(element, node.text);
      }
      elements[node.id] = element;
      stage.appendChild(element);
    });
    root.replaceChildren(stage);

    var redraw = function () { drawConnections(stage, svg, elements); };
    redraw();
    if (window.ResizeObserver) {
      var resizeObserver = new ResizeObserver(redraw);
      resizeObserver.observe(stage);
      root._htmlFormsGraphResizeObserver = resizeObserver;
    } else {
      window.addEventListener('resize', redraw);
    }
  }

  function scan(scope) {
    if (scope.nodeType === 1 && scope.matches(schemaSelector)) render(scope);
    if (scope.querySelectorAll) scope.querySelectorAll(schemaSelector).forEach(render);
  }

  scan(document);
  if (window.MutationObserver && document.documentElement) {
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.removedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          var removed = [];
          if (node.matches(schemaSelector)) removed.push(node);
          if (node.querySelectorAll) removed = removed.concat(
            Array.from(node.querySelectorAll(schemaSelector))
          );
          removed.forEach(function (root) {
            if (root._htmlFormsGraphResizeObserver) {
              root._htmlFormsGraphResizeObserver.disconnect();
              delete root._htmlFormsGraphResizeObserver;
            }
          });
        });
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) scan(node);
        });
      });
    }).observe(document.documentElement, {childList: true, subtree: true});
  }
}());
