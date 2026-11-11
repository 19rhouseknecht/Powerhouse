/* Power House Flow: the business as a flow chart.
 *
 * Steps are placed automatically so the chart always stays neat: each step sits one row below
 * the furthest step that leads into it, branches spread sideways, and an arrow that goes back to
 * an earlier step (a loop) runs down the right-hand side. Nothing is dragged into place.
 *
 * Data: {nodes: [{id, title, kind, who, auto, notes}], edges: [{from, to, label}]}
 * Saved to /flow/save (debounced) with the version it was loaded at; a newer version on the
 * server is never overwritten.
 */
(function () {
  'use strict';

  var app = document.getElementById('flow-app');
  if (!app) return;

  var W = 236, H = 96, GX = 40, GY = 64, PAD = 44;
  var KINDS = [['start', 'Start'], ['step', 'Step'], ['decision', 'Decision'], ['end', 'End']];
  var KIND_LABEL = {start: 'Start', step: 'Step', decision: 'Decision', end: 'End'};
  var AUTOS = [['', 'Not set'], ['manual', 'Manual'], ['partial', 'Partly'], ['software', 'Software']];
  var AUTO_LABEL = {manual: 'Manual', partial: 'Partly in software', software: 'Software handles it'};
  var WHO_DEFAULTS = ['Office', 'Sales', 'Field tech', 'Owner', 'Client', 'Supplier'];

  var data = JSON.parse(document.getElementById('flow-data').textContent || '{}');
  data.nodes = data.nodes || [];
  data.edges = data.edges || [];

  var version = parseInt(app.getAttribute('data-version'), 10) || 0;
  var saveUrl = app.getAttribute('data-save-url');
  var csrf = app.getAttribute('data-csrf');

  var stage = document.getElementById('flow-stage');
  var scroller = document.getElementById('flow-scroll');
  var sizer = document.getElementById('flow-sizer');
  var canvas = document.getElementById('flow-canvas');
  var svg = document.getElementById('flow-edges');
  var panel = document.getElementById('flow-panel');
  var empty = document.getElementById('flow-empty');
  var linkingBar = document.getElementById('flow-linking');
  var savedEl = document.getElementById('flow-saved');

  var selected = null;          // {type: 'node'|'edge', id} — an edge's id is "from>to"
  var linkFrom = null;          // the step an arrow is being drawn from
  var zoom = 1;
  var undoStack = [], redoStack = [];
  var lastLayout = null;

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'text') node.textContent = v;
      else if (k === 'class') node.className = v;
      else if (k.slice(0, 2) === 'on') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    });
    (kids || []).forEach(function (kid) { if (kid) node.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid); });
    return node;
  }
  function svgEl(tag, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }
  function newId() { return 'n' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  function nodeById(id) { for (var i = 0; i < data.nodes.length; i++) if (data.nodes[i].id === id) return data.nodes[i]; return null; }
  function edgeKey(e) { return e.from + '>' + e.to; }
  function edgeByKey(key) { for (var i = 0; i < data.edges.length; i++) if (edgeKey(data.edges[i]) === key) return data.edges[i]; return null; }
  function outgoing(id) { return data.edges.filter(function (e) { return e.from === id; }); }
  function incoming(id) { return data.edges.filter(function (e) { return e.to === id; }); }
  function hasEdge(a, b) { return data.edges.some(function (e) { return e.from === a && e.to === b; }); }

  // ------------------------------------------------------------------ history + saving
  function snapshot() { return JSON.stringify(data); }
  function remember() {
    undoStack.push(snapshot());
    if (undoStack.length > 120) undoStack.shift();
    redoStack = [];
    syncHistoryButtons();
  }
  function restore(json) {
    data = JSON.parse(json);
    if (selected && selected.type === 'node' && !nodeById(selected.id)) selected = null;
    if (selected && selected.type === 'edge' && !edgeByKey(selected.id)) selected = null;
    changed();
  }
  function undo() { if (!undoStack.length) return; redoStack.push(snapshot()); restore(undoStack.pop()); syncHistoryButtons(); }
  function redo() { if (!redoStack.length) return; undoStack.push(snapshot()); restore(redoStack.pop()); syncHistoryButtons(); }
  function syncHistoryButtons() {
    app.querySelector('[data-flow="undo"]').disabled = !undoStack.length;
    app.querySelector('[data-flow="redo"]').disabled = !redoStack.length;
  }

  var saveTimer = null, saving = false, dirty = false, again = false, blocked = false;
  function scheduleSave() {
    if (blocked) return;
    dirty = true;
    setSaved('Unsaved changes…', '');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
  }
  function setSaved(text, state, withReload) {
    savedEl.textContent = text;
    savedEl.className = 'flow-saved' + (state ? ' is-' + state : '');
    if (withReload) {
      savedEl.appendChild(document.createTextNode(' '));
      savedEl.appendChild(el('button', {type: 'button', class: 'btn btn-small btn-secondary', text: 'Reload', onclick: function () { dirty = false; window.location.reload(); }}));
    }
  }
  function save() {
    if (saving) { again = true; return; }
    saving = true; again = false;
    setSaved('Saving…', '');
    fetch(saveUrl, {method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
                    body: JSON.stringify({version: version, data: data})})
      .then(function (r) { return r.json().then(function (body) { return {status: r.status, body: body}; }); })
      .then(function (res) {
        saving = false;
        if (res.body && res.body.ok) {
          version = res.body.version;
          if (again) { save(); return; }
          dirty = false;
          var t = new Date();
          setSaved('Saved ' + t.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}), 'ok');
        } else {
          if (res.body && res.body.conflict) blocked = true;
          setSaved((res.body && res.body.said) || 'Not saved.', 'error', blocked);
        }
      })
      .catch(function () {
        saving = false;
        setSaved('Not saved: no connection. Retrying…', 'error');
        clearTimeout(saveTimer);
        saveTimer = setTimeout(save, 4000);
      });
  }
  window.addEventListener('beforeunload', function (e) { if (dirty && !blocked) { e.preventDefault(); e.returnValue = ''; } });

  function changed() {
    scheduleSave();
    render();
  }

  // ------------------------------------------------------------------ layout
  function layout() {
    var ids = data.nodes.map(function (n) { return n.id; });
    var out = {}, inc = {};
    ids.forEach(function (id) { out[id] = []; inc[id] = []; });
    data.edges.forEach(function (e) { if (out[e.from] && inc[e.to]) { out[e.from].push(e); inc[e.to].push(e); } });

    // loops: an arrow back to a step still being walked is drawn on the side, never ranked
    var back = new Set(), state = {};
    var roots = ids.filter(function (id) { return !inc[id].length; });
    var order = roots.concat(ids.filter(function (id) { return inc[id].length; }));
    function walk(u) {
      state[u] = 1;
      out[u].forEach(function (e) { if (state[e.to] === 1) back.add(e); else if (!state[e.to]) walk(e.to); });
      state[u] = 2;
    }
    order.forEach(function (u) { if (!state[u]) walk(u); });

    // rows: the longest way in from a starting step
    var rank = {}, left = {}, queue = [];
    ids.forEach(function (id) { rank[id] = 0; left[id] = 0; });
    data.edges.forEach(function (e) { if (!back.has(e) && left[e.to] !== undefined && out[e.from]) left[e.to]++; });
    order.forEach(function (id) { if (!left[id]) queue.push(id); });
    while (queue.length) {
      var u = queue.shift();
      out[u].forEach(function (e) {
        if (back.has(e)) return;
        rank[e.to] = Math.max(rank[e.to], rank[u] + 1);
        if (--left[e.to] === 0) queue.push(e.to);
      });
    }

    // columns: a tree on each step's nearest parent, branches side by side
    var parent = {};
    ids.forEach(function (id) {
      var best = null;
      inc[id].forEach(function (e) { if (!back.has(e) && (best === null || rank[e.from] > rank[best])) best = e.from; });
      parent[id] = best;
    });
    var kids = {};
    ids.forEach(function (id) { kids[id] = out[id].filter(function (e) { return !back.has(e) && parent[e.to] === id; }).map(function (e) { return e.to; }); });
    var col = {}, cursor = 0, placed = {};
    function place(id) {
      placed[id] = true;
      var mine = kids[id].filter(function (k) { return !placed[k]; });
      if (!mine.length) { col[id] = cursor + 0.5; cursor += 1; return; }
      mine.forEach(place);
      col[id] = (col[mine[0]] + col[mine[mine.length - 1]]) / 2;
    }
    order.forEach(function (id) { if (parent[id] === null && !placed[id]) place(id); });
    ids.forEach(function (id) { if (!placed[id]) place(id); });

    var pos = {}, maxRight = 0, maxBottom = 0;
    ids.forEach(function (id) {
      var x = PAD + col[id] * (W + GX) - W / 2;
      var y = PAD + rank[id] * (H + GY);
      pos[id] = {x: x, y: y, rank: rank[id]};
      maxRight = Math.max(maxRight, x + W);
      maxBottom = Math.max(maxBottom, y + H);
    });
    var backList = data.edges.filter(function (e) { return back.has(e); });
    return {pos: pos, back: back, backCount: backList.length, backList: backList,
            width: maxRight + PAD + (backList.length ? 40 + backList.length * 18 : 0), height: maxBottom + PAD};
  }

  // ------------------------------------------------------------------ drawing
  function render(keepPanel) {
    var lay = lastLayout = layout();
    empty.hidden = data.nodes.length > 0;
    scroller.hidden = data.nodes.length === 0;
    canvas.querySelectorAll('.flow-node, .flow-edge-label').forEach(function (n) { n.remove(); });
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    canvas.style.width = lay.width + 'px';
    canvas.style.height = lay.height + 'px';
    canvas.style.transform = 'scale(' + zoom + ')';
    sizer.style.width = Math.ceil(lay.width * zoom) + 'px';
    sizer.style.height = Math.ceil(lay.height * zoom) + 'px';
    svg.setAttribute('width', lay.width);
    svg.setAttribute('height', lay.height);
    svg.setAttribute('viewBox', '0 0 ' + lay.width + ' ' + lay.height);

    var defs = svgEl('defs');
    [['flow-arrow', 'flow-arrowhead'], ['flow-arrow-on', 'flow-arrowhead is-on']].forEach(function (m) {
      var marker = svgEl('marker', {id: m[0], viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '8', markerHeight: '8', orient: 'auto-start-reverse'});
      marker.appendChild(svgEl('path', {d: 'M0 0 L10 5 L0 10 z', class: m[1]}));
      defs.appendChild(marker);
    });
    svg.appendChild(defs);

    // loops get their own lane down the right of everything they pass
    var laneOf = {}, lane = 0;
    lay.backList.forEach(function (e) { laneOf[edgeKey(e)] = lane++; });

    data.edges.forEach(function (e) {
      var a = lay.pos[e.from], b = lay.pos[e.to];
      if (!a || !b) return;
      var key = edgeKey(e), on = selected && selected.type === 'edge' && selected.id === key;
      var d, lx, ly;
      if (lay.back.has(e)) {
        var top = Math.min(a.y, b.y), bottom = Math.max(a.y, b.y) + H, right = 0;
        Object.keys(lay.pos).forEach(function (id) { var p = lay.pos[id]; if (p.y + H >= top && p.y <= bottom) right = Math.max(right, p.x + W); });
        var x = right + 26 + laneOf[key] * 18, y1 = a.y + H / 2, y2 = b.y + H / 2, x1 = a.x + W, x2 = b.x + W, r = 10;
        var dir = y2 < y1 ? -1 : 1;
        d = 'M' + x1 + ' ' + y1 + ' L' + (x - r) + ' ' + y1 + ' Q' + x + ' ' + y1 + ' ' + x + ' ' + (y1 + dir * r) +
            ' L' + x + ' ' + (y2 - dir * r) + ' Q' + x + ' ' + y2 + ' ' + (x - r) + ' ' + y2 + ' L' + (x2 + 2) + ' ' + y2;
        lx = x; ly = (y1 + y2) / 2;
      } else {
        var sx = a.x + W / 2, sy = a.y + H, tx = b.x + W / 2, ty = b.y - 2;
        var dy = Math.max(30, (ty - sy) / 2);
        d = 'M' + sx + ' ' + sy + ' C' + sx + ' ' + (sy + dy) + ' ' + tx + ' ' + (ty - dy) + ' ' + tx + ' ' + ty;
        lx = (sx + tx) / 2; ly = (sy + ty) / 2;
      }
      svg.appendChild(svgEl('path', {d: d, class: 'flow-edge' + (on ? ' is-on' : '') + (lay.back.has(e) ? ' is-loop' : ''),
                                     'marker-end': on ? 'url(#flow-arrow-on)' : 'url(#flow-arrow)'}));
      var hit = svgEl('path', {d: d, class: 'flow-edge-hit'});
      hit.addEventListener('click', function (ev) { ev.stopPropagation(); select({type: 'edge', id: key}); });
      svg.appendChild(hit);
      if (e.label) {
        canvas.appendChild(el('button', {type: 'button', class: 'flow-edge-label' + (on ? ' is-on' : '') + (lay.back.has(e) ? ' is-loop' : ''), style: 'left:' + lx + 'px;top:' + ly + 'px',
                                         text: e.label, onclick: function (ev) { ev.stopPropagation(); select({type: 'edge', id: key}); }}));
      }
    });

    data.nodes.forEach(function (n) {
      var p = lay.pos[n.id];
      var isOn = selected && selected.type === 'node' && selected.id === n.id;
      var cls = 'flow-node kind-' + n.kind + (n.auto ? ' auto-' + n.auto : '') + (isOn ? ' is-on' : '') +
                (linkFrom ? (linkFrom === n.id ? ' is-link-from' : ' is-link-target') : '');
      var card = el('div', {class: cls, style: 'left:' + p.x + 'px;top:' + p.y + 'px;width:' + W + 'px;height:' + H + 'px',
                            tabindex: '0', role: 'button', 'data-id': n.id,
                            'aria-label': KIND_LABEL[n.kind] + ': ' + n.title + (n.auto ? '. ' + AUTO_LABEL[n.auto] : '')}, [
        el('div', {class: 'flow-node-top'}, [
          el('span', {class: 'flow-node-kind', text: n.kind === 'decision' ? '◆ Decision' : KIND_LABEL[n.kind]}),
          n.auto ? el('span', {class: 'flow-node-auto', text: n.auto === 'software' ? 'Software' : n.auto === 'partial' ? 'Partly' : 'Manual'}) : null
        ]),
        el('div', {class: 'flow-node-title', text: n.title}),
        n.who || n.notes ? el('div', {class: 'flow-node-who'}, [n.who ? n.who : '', n.notes ? el('span', {class: 'flow-node-note', title: 'Has notes', text: '✎'}) : null]) : null
      ]);
      card.addEventListener('click', function (ev) {
        ev.stopPropagation();
        if (linkFrom) { finishLink(n.id); return; }
        select({type: 'node', id: n.id});
      });
      card.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); card.click(); } });
      if (!linkFrom && n.kind !== 'end') {
        var add = el('button', {type: 'button', class: 'flow-add', title: outgoing(n.id).length ? 'Add a branch from this step' : 'Add the next step',
                                'aria-label': outgoing(n.id).length ? 'Add a branch' : 'Add the next step', text: '+'});
        add.addEventListener('click', function (ev) { ev.stopPropagation(); addAfter(n.id, false); });
        card.appendChild(add);
      }
      canvas.appendChild(card);
    });

    linkingBar.hidden = !linkFrom;
    stage.classList.toggle('is-linking', !!linkFrom);
    app.querySelector('.flow-zoom-value').textContent = Math.round(zoom * 100) + '%';
    if (!keepPanel) renderPanel();
  }

  function reveal(id) {
    var p = lastLayout && lastLayout.pos[id];
    if (!p) return;
    var x = p.x * zoom, y = p.y * zoom, w = W * zoom, h = H * zoom;
    var view = {l: scroller.scrollLeft, t: scroller.scrollTop, r: scroller.scrollLeft + scroller.clientWidth, b: scroller.scrollTop + scroller.clientHeight};
    if (x < view.l || x + w > view.r) scroller.scrollLeft = Math.max(0, x - scroller.clientWidth / 2 + w / 2);
    if (y < view.t || y + h + 40 > view.b) scroller.scrollTop = Math.max(0, y - scroller.clientHeight / 3);
  }

  // ------------------------------------------------------------------ the side panel
  function field(label, input, hint) {
    return el('label', {class: 'flow-field'}, [el('span', {text: label}), input, hint ? el('small', {text: hint}) : null]);
  }
  function editsText(input, apply) {
    var pending = null, recorded = false;
    input.addEventListener('focus', function () { pending = snapshot(); recorded = false; });
    input.addEventListener('input', function () {
      if (!recorded && pending) { undoStack.push(pending); redoStack = []; recorded = true; syncHistoryButtons(); }
      apply(input.value);
      scheduleSave();
      render(true);          // redraw the chart; the panel keeps its focus
    });
  }

  function renderPanel() {
    panel.textContent = '';
    if (selected && selected.type === 'node' && nodeById(selected.id)) return nodePanel(nodeById(selected.id));
    if (selected && selected.type === 'edge' && edgeByKey(selected.id)) return edgePanel(edgeByKey(selected.id));
    overviewPanel();
  }

  function overviewPanel() {
    var counts = {software: 0, partial: 0, manual: 0, '': 0}, all = data.nodes.length;
    data.nodes.forEach(function (n) { if (n.kind !== 'start' && n.kind !== 'end') counts[n.auto || '']++; });
    var total = counts.software + counts.partial + counts.manual + counts[''];
    var bar = el('div', {class: 'flow-coverage', role: 'img', 'aria-label': counts.software + ' handled by software, ' + counts.partial + ' partly, ' + counts.manual + ' manual, ' + counts[''] + ' not marked'});
    [['software', counts.software], ['partial', counts.partial], ['manual', counts.manual], ['none', counts['']]].forEach(function (pair) {
      if (pair[1]) bar.appendChild(el('span', {class: 'seg-' + pair[0], style: 'flex:' + pair[1]}));
    });
    var legend = el('ul', {class: 'flow-legend'}, [
      el('li', {}, [el('i', {class: 'dot-software'}), 'Software handles it', el('b', {text: String(counts.software)})]),
      el('li', {}, [el('i', {class: 'dot-partial'}), 'Partly in software', el('b', {text: String(counts.partial)})]),
      el('li', {}, [el('i', {class: 'dot-manual'}), 'Done by hand', el('b', {text: String(counts.manual)})]),
      el('li', {}, [el('i', {class: 'dot-none'}), 'Not marked yet', el('b', {text: String(counts[''])})])
    ]);
    panel.appendChild(el('div', {class: 'flow-panel-head'}, [el('span', {class: 'eyebrow', text: 'Overview'}), el('h2', {text: all ? all + ' step' + (all === 1 ? '' : 's') : 'No steps yet'})]));
    if (total) {
      panel.appendChild(el('p', {class: 'flow-big', text: Math.round(100 * (counts.software + counts.partial * 0.5) / total) + '% handled by software'}));
      panel.appendChild(bar);
      panel.appendChild(el('small', {class: 'muted', text: 'Counting the ' + total + ' working step' + (total === 1 ? '' : 's') + ' (not start and end). “Partly” counts as half.'}));
    }
    panel.appendChild(legend);
    panel.appendChild(el('div', {class: 'flow-help'}, [
      el('h3', {text: 'How to use it'}),
      el('ul', {}, [
        el('li', {text: 'Click a step to edit it.'}),
        el('li', {text: '+ under a step adds the next step. If it already leads somewhere, + starts a branch.'}),
        el('li', {text: '“Connect to a step” draws an arrow to a step that exists already, including back to an earlier one.'}),
        el('li', {text: 'Click an arrow to name it (“Approved”, “Declined”) or remove it.'}),
        el('li', {text: 'Delete removes the selected step. Ctrl+Z undoes.'})
      ])
    ]));
    if (data.nodes.length) {
      panel.appendChild(el('button', {type: 'button', class: 'btn btn-secondary btn-small', text: '＋ Add another starting step',
                                      onclick: function () { remember(); var n = makeNode('start', 'New starting step'); data.nodes.push(n); selectNew(n); }}));
    }
  }

  function nodePanel(n) {
    var title = el('input', {type: 'text', value: n.title, maxlength: '200'});
    editsText(title, function (v) { n.title = v.trim() || 'Untitled step'; });
    var kind = el('div', {class: 'flow-seg', role: 'radiogroup', 'aria-label': 'Type of step'});
    KINDS.forEach(function (k) {
      kind.appendChild(el('button', {type: 'button', role: 'radio', 'aria-checked': n.kind === k[0] ? 'true' : 'false', class: n.kind === k[0] ? 'is-on' : '', text: k[1],
                                     onclick: function () { if (n.kind === k[0]) return; remember(); n.kind = k[0]; changed(); }}));
    });
    var whoList = 'flow-who-list';
    var who = el('input', {type: 'text', value: n.who, maxlength: '80', list: whoList, placeholder: 'Office, field tech, client…'});
    editsText(who, function (v) { n.who = v.trim(); });
    var names = {};
    WHO_DEFAULTS.concat(data.nodes.map(function (x) { return x.who; })).forEach(function (w) { if (w) names[w] = true; });
    var datalist = el('datalist', {id: whoList}, Object.keys(names).map(function (w) { return el('option', {value: w}); }));
    var auto = el('div', {class: 'flow-seg flow-seg-auto', role: 'radiogroup', 'aria-label': 'How much the software handles'});
    AUTOS.forEach(function (a) {
      auto.appendChild(el('button', {type: 'button', role: 'radio', 'aria-checked': n.auto === a[0] ? 'true' : 'false', class: (n.auto === a[0] ? 'is-on ' : '') + 'auto-' + (a[0] || 'none'), text: a[1],
                                     onclick: function () { if (n.auto === a[0]) return; remember(); n.auto = a[0]; changed(); }}));
    });
    var notes = el('textarea', {rows: '5', maxlength: '4000', placeholder: 'What happens here, what goes wrong, what the software could do…'});
    notes.value = n.notes;
    editsText(notes, function (v) { n.notes = v; });

    var outs = outgoing(n.id), ins = incoming(n.id);
    panel.appendChild(el('div', {class: 'flow-panel-head'}, [
      el('span', {class: 'eyebrow', text: KIND_LABEL[n.kind]}),
      el('button', {type: 'button', class: 'flow-close', 'aria-label': 'Close', text: '×', onclick: function () { select(null); }})
    ]));
    panel.appendChild(field('Step', title));
    panel.appendChild(field('Type', kind));
    panel.appendChild(field('Who does it', who));
    panel.appendChild(datalist);
    panel.appendChild(field('How much does the software handle?', auto));
    panel.appendChild(field('Notes', notes));

    var actions = el('div', {class: 'flow-actions'});
    if (n.kind !== 'end') {
      actions.appendChild(el('button', {type: 'button', class: 'btn btn-primary btn-small', text: outs.length ? '＋ Add a branch' : '＋ Add next step', onclick: function () { addAfter(n.id, false); }}));
      if (outs.length) actions.appendChild(el('button', {type: 'button', class: 'btn btn-secondary btn-small', text: 'Insert a step after', title: 'A new step between this one and what it leads to', onclick: function () { addAfter(n.id, true); }}));
      actions.appendChild(el('button', {type: 'button', class: 'btn btn-secondary btn-small', text: 'Connect to a step…', onclick: function () { startLink(n.id); }}));
    }
    panel.appendChild(actions);

    if (outs.length) {
      panel.appendChild(el('h3', {class: 'flow-sub', text: 'Leads to'}));
      panel.appendChild(el('ul', {class: 'flow-links'}, outs.map(function (e) {
        var label = el('input', {type: 'text', value: e.label, maxlength: '80', placeholder: outs.length > 1 ? 'Name this path (e.g. Approved)' : 'Name this arrow (optional)'});
        editsText(label, function (v) { e.label = v.trim(); });
        return el('li', {}, [
          el('button', {type: 'button', class: 'flow-link-target', text: '→ ' + nodeById(e.to).title, onclick: function () { select({type: 'node', id: e.to}); reveal(e.to); }}),
          label,
          el('button', {type: 'button', class: 'flow-link-remove', title: 'Remove this arrow', 'aria-label': 'Remove this arrow', text: '×', onclick: function () { remember(); data.edges.splice(data.edges.indexOf(e), 1); changed(); }})
        ]);
      })));
    }
    if (ins.length) {
      panel.appendChild(el('h3', {class: 'flow-sub', text: 'Comes from'}));
      panel.appendChild(el('ul', {class: 'flow-links flow-links-in'}, ins.map(function (e) {
        return el('li', {}, [el('button', {type: 'button', class: 'flow-link-target', text: '← ' + nodeById(e.from).title + (e.label ? '  (' + e.label + ')' : ''), onclick: function () { select({type: 'node', id: e.from}); reveal(e.from); }})]);
      })));
    }
    panel.appendChild(el('button', {type: 'button', class: 'btn btn-danger btn-small flow-delete', text: 'Delete step', onclick: function () { deleteNode(n.id); }}));
  }

  function edgePanel(e) {
    var label = el('input', {type: 'text', value: e.label, maxlength: '80', placeholder: 'e.g. Approved, Declined, Needs changes'});
    editsText(label, function (v) { e.label = v.trim(); });
    panel.appendChild(el('div', {class: 'flow-panel-head'}, [
      el('span', {class: 'eyebrow', text: 'Arrow'}),
      el('button', {type: 'button', class: 'flow-close', 'aria-label': 'Close', text: '×', onclick: function () { select(null); }})
    ]));
    panel.appendChild(el('p', {class: 'flow-arrow-text'}, [
      el('button', {type: 'button', class: 'flow-link-target', text: nodeById(e.from).title, onclick: function () { select({type: 'node', id: e.from}); }}),
      el('span', {text: ' → '}),
      el('button', {type: 'button', class: 'flow-link-target', text: nodeById(e.to).title, onclick: function () { select({type: 'node', id: e.to}); }})
    ]));
    panel.appendChild(field('Name on the arrow', label, 'Shown on the chart. Useful after a decision.'));
    panel.appendChild(el('button', {type: 'button', class: 'btn btn-danger btn-small', text: 'Remove arrow', onclick: function () { remember(); data.edges.splice(data.edges.indexOf(e), 1); selected = null; changed(); }}));
    setTimeout(function () { label.focus(); }, 0);
  }

  // ------------------------------------------------------------------ actions
  function makeNode(kind, title) { return {id: newId(), title: title, kind: kind, who: '', auto: '', notes: ''}; }
  function select(sel) { selected = sel; linkFrom = null; render(); }
  function selectNew(n) {
    selected = {type: 'node', id: n.id};
    changed();
    reveal(n.id);
    var input = panel.querySelector('input');
    if (input) { input.focus(); input.select(); }
  }
  function addAfter(id, inline) {
    remember();
    var n = makeNode('step', 'New step');
    data.nodes.push(n);
    if (inline) {
      data.edges.forEach(function (e) { if (e.from === id) e.from = n.id; });
    }
    data.edges.push({from: id, to: n.id, label: ''});
    selectNew(n);
  }
  function deleteNode(id) {
    remember();
    var ins = incoming(id).filter(function (e) { return e.from !== id; });
    var outs = outgoing(id).filter(function (e) { return e.to !== id; });
    data.edges = data.edges.filter(function (e) { return e.from !== id && e.to !== id; });
    if (ins.length <= 1 || outs.length <= 1) {      // keep the flow joined up around the gap
      ins.forEach(function (a) {
        outs.forEach(function (b) {
          if (a.from !== b.to && !hasEdge(a.from, b.to)) data.edges.push({from: a.from, to: b.to, label: outs.length > 1 ? b.label : a.label});
        });
      });
    }
    data.nodes = data.nodes.filter(function (n) { return n.id !== id; });
    selected = null;
    changed();
  }
  function startLink(id) { linkFrom = id; selected = {type: 'node', id: id}; render(); }
  function finishLink(target) {
    var from = linkFrom;
    linkFrom = null;
    if (target === from) { render(); return; }
    if (hasEdge(from, target)) { selected = {type: 'edge', id: from + '>' + target}; render(); return; }
    remember();
    data.edges.push({from: from, to: target, label: ''});
    selected = {type: 'edge', id: from + '>' + target};
    changed();
  }

  function sample() {
    var steps = [
      ['call', 'start', 'Customer reaches out', 'Office', 'manual', 'Phone call, text, website or referral.'],
      ['job', 'step', 'Create the client and job', 'Office', 'software', ''],
      ['review', 'step', 'Schedule the site review', 'Office', 'partial', ''],
      ['onsite', 'step', 'Site review and onsite checklist', 'Field tech', 'partial', ''],
      ['quote', 'step', 'Build the quote options', 'Office', 'software', 'Quote Assistant with the pricing sheet and services.'],
      ['present', 'step', 'Present the quote to the client', 'Sales', 'partial', ''],
      ['decide', 'decision', 'Client decision', 'Client', '', ''],
      ['order', 'step', 'Order the materials', 'Office', 'partial', 'Material order from the chosen option; Cooper cart.'],
      ['schedule', 'step', 'Schedule the work', 'Office', 'partial', ''],
      ['work', 'step', 'Do the work', 'Field tech', 'partial', 'Time clock and job visits.'],
      ['walk', 'step', 'Final walkthrough with the client', 'Field tech', 'manual', ''],
      ['invoice', 'step', 'Invoice and collect payment', 'Office', 'manual', ''],
      ['google', 'step', 'Ask for a Google review', 'Office', 'partial', ''],
      ['done', 'end', 'Job closed', '', '', ''],
      ['follow', 'step', 'Follow up later', 'Sales', 'manual', ''],
      ['lost', 'end', 'Closed — not won', '', '', '']
    ];
    var ids = {};
    data.nodes = steps.map(function (s) { var n = makeNode(s[1], s[2]); n.who = s[3]; n.auto = s[4]; n.notes = s[5]; ids[s[0]] = n.id; return n; });
    var links = [['call', 'job'], ['job', 'review'], ['review', 'onsite'], ['onsite', 'quote'], ['quote', 'present'], ['present', 'decide'],
                 ['decide', 'order', 'Approved'], ['decide', 'quote', 'Needs changes'], ['decide', 'follow', 'Declined'],
                 ['order', 'schedule'], ['schedule', 'work'], ['work', 'walk'], ['walk', 'invoice'], ['invoice', 'google'], ['google', 'done'], ['follow', 'lost']];
    data.edges = links.map(function (l) { return {from: ids[l[0]], to: ids[l[1]], label: l[2] || ''}; });
  }

  // ------------------------------------------------------------------ wiring
  app.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-flow]');
    if (!btn) return;
    var what = btn.getAttribute('data-flow');
    if (what === 'undo') undo();
    else if (what === 'redo') redo();
    else if (what === 'zoom-in') { zoom = Math.min(1.6, Math.round((zoom + 0.1) * 10) / 10); render(); }
    else if (what === 'zoom-out') { zoom = Math.max(0.4, Math.round((zoom - 0.1) * 10) / 10); render(); }
    else if (what === 'zoom-reset') { zoom = 1; render(); }
    else if (what === 'print') window.print();
    else if (what === 'cancel-link') { linkFrom = null; render(); }
    else if (what === 'start-blank') { remember(); var n = makeNode('start', 'Customer reaches out'); data.nodes.push(n); selectNew(n); }
    else if (what === 'start-sample') { remember(); sample(); selected = null; changed(); }
  });
  scroller.addEventListener('click', function (ev) {
    if (ev.target.closest('.flow-node, .flow-edge-label, .flow-edge-hit')) return;
    if (linkFrom) { linkFrom = null; render(); return; }
    if (selected) select(null);
  });

  // drag the empty background to move around a large chart
  var drag = null;
  scroller.addEventListener('pointerdown', function (ev) {
    if (ev.button !== 0 || ev.target.closest('.flow-node, .flow-edge-label, .flow-edge-hit, button')) return;
    drag = {x: ev.clientX, y: ev.clientY, l: scroller.scrollLeft, t: scroller.scrollTop, moved: false};
  });
  window.addEventListener('pointermove', function (ev) {
    if (!drag) return;
    var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
    drag.moved = true;
    scroller.classList.add('is-panning');
    scroller.scrollLeft = drag.l - dx;
    scroller.scrollTop = drag.t - dy;
  });
  window.addEventListener('pointerup', function () {
    if (drag && drag.moved) {
      var swallow = function (e) { e.stopPropagation(); scroller.removeEventListener('click', swallow, true); };
      scroller.addEventListener('click', swallow, true);
      setTimeout(function () { scroller.removeEventListener('click', swallow, true); }, 0);
    }
    drag = null;
    scroller.classList.remove('is-panning');
  });

  document.addEventListener('keydown', function (ev) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName);
    var mod = ev.ctrlKey || ev.metaKey;
    if (mod && ev.key.toLowerCase() === 'z' && !typing) { ev.preventDefault(); if (ev.shiftKey) redo(); else undo(); return; }
    if (mod && ev.key.toLowerCase() === 'y' && !typing) { ev.preventDefault(); redo(); return; }
    if (ev.key === 'Escape') {
      if (linkFrom) { linkFrom = null; render(); }
      else if (selected) { if (typing) ev.target.blur(); select(null); }
      return;
    }
    if (typing) return;
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && selected) {
      ev.preventDefault();
      if (selected.type === 'node') deleteNode(selected.id);
      else { var e = edgeByKey(selected.id); if (e) { remember(); data.edges.splice(data.edges.indexOf(e), 1); selected = null; changed(); } }
    }
  });

  if (window.innerWidth < 700) zoom = 0.8;
  render();
  syncHistoryButtons();
  // open on the first step, centred
  (function () {
    var first = data.nodes.filter(function (n) { return n.kind === 'start'; })[0] || data.nodes[0];
    var p = first && lastLayout && lastLayout.pos[first.id];
    if (p) scroller.scrollLeft = Math.max(0, (p.x + W / 2) * zoom - scroller.clientWidth / 2);
  })();
})();
