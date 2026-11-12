/* Pricing Sheet: every row saves itself, pictures upload on pick, and rows can be selected for
   "Update from Cooper" (price and picture) or "Remove selected". */
(function () {
  'use strict';
  var table = document.querySelector('.pricing-table');
  if (!table) return;
  var bar = document.querySelector('[data-selection-bar]');
  var selected = new Set();

  // ---------------------------------------------------------------- autosave
  var timers = {}, running = {}, again = {};

  function status(row, text, state) {
    var el = row && row.querySelector('[data-autosave-status]');
    if (!el) return;
    el.textContent = text;
    el.className = 'autosave-status' + (state ? ' is-' + state : '');
  }

  function swapRow(row, html) {
    // keep the cursor where it was when the row is redrawn
    var active = document.activeElement, keep = null;
    if (active && row.contains(active) && active.name) {
      var same = Array.prototype.filter.call(row.querySelectorAll('[name="' + active.name + '"]'), function (n) { return true; });
      keep = {name: active.name, index: same.indexOf(active), start: active.selectionStart, end: active.selectionEnd};
    }
    var holder = document.createElement('tbody');
    holder.innerHTML = html.trim();
    var fresh = holder.firstElementChild;
    row.replaceWith(fresh);
    var box = fresh.querySelector('[data-select-item]');
    if (box && selected.has(box.value)) box.checked = true;
    if (keep) {
      var target = fresh.querySelectorAll('[name="' + keep.name + '"]')[keep.index];
      if (target) {
        target.focus({preventScroll: true});
        try { if (keep.start != null) target.setSelectionRange(keep.start, keep.end); } catch (e) { /* number inputs */ }
      }
    }
    return fresh;
  }

  function save(id) {
    var row = document.getElementById('price-item-' + id);
    var form = document.getElementById('price-edit-' + id);
    if (!row || !form) return;
    if (running[id]) { again[id] = true; return; }
    running[id] = true;
    status(row, 'Saving…', 'busy');
    fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: {'X-Requested-With': 'fetch', 'Accept': 'application/json'}})
      .then(function (r) { return r.json().then(function (d) { return {ok: r.ok, d: d}; }); })
      .then(function (res) {
        running[id] = false;
        var current = document.getElementById('price-item-' + id);
        if (again[id]) { again[id] = false; save(id); return; }
        if (res.d && res.d.row && current) current = swapRow(current, res.d.row);
        if (res.ok && res.d.ok) { status(current, 'Saved ✓', 'ok'); setTimeout(function () { status(document.getElementById('price-item-' + id), '', ''); }, 2500); }
        else status(current, (res.d && res.d.error) || 'Not saved.', 'error');
      })
      .catch(function () { running[id] = false; status(document.getElementById('price-item-' + id), 'Not saved: no connection. Change it again to retry.', 'error'); });
  }

  function rowId(el) {
    var form = el.getAttribute && el.getAttribute('form');
    return form && form.indexOf('price-edit-') === 0 ? form.slice(11) : null;
  }

  table.addEventListener('change', function (event) {
    var el = event.target;
    if (el.matches('[data-image-input]')) return uploadImage(el);
    var id = rowId(el);
    if (!id) return;
    clearTimeout(timers[id]);
    timers[id] = setTimeout(function () { save(id); }, 150);
  });
  table.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && rowId(event.target) && event.target.tagName === 'INPUT') {
      event.preventDefault();
      event.target.blur();                     // blurring fires change, which saves
    }
  });

  // ---------------------------------------------------------------- pictures
  function uploadImage(input) {
    var row = input.closest('tr'), form = input.closest('form');
    if (!input.files || !input.files.length) return;
    status(row, 'Uploading picture…', 'busy');
    fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: {'X-Requested-With': 'fetch', 'Accept': 'application/json'}})
      .then(function (r) { return r.json().then(function (d) { return {ok: r.ok, d: d}; }); })
      .then(function (res) {
        var fresh = res.d && res.d.row ? swapRow(row, res.d.row) : row;
        status(fresh, res.ok ? 'Picture saved ✓' : (res.d.error || 'Picture not saved.'), res.ok ? 'ok' : 'error');
      })
      .catch(function () { status(row, 'Picture not saved: no connection.', 'error'); });
  }

  // ---------------------------------------------------------------- selection
  var all = table.querySelector('[data-select-all]');
  function boxes() { return Array.prototype.slice.call(table.querySelectorAll('[data-select-item]')); }
  function refresh() {
    var list = boxes();
    var ids = Array.from(selected);
    document.querySelectorAll('[data-selected-ids]').forEach(function (input) { input.value = ids.join(','); });
    var count = document.querySelector('[data-selected-count]');
    if (count) count.textContent = ids.length;
    if (bar) bar.hidden = ids.length === 0;
    if (all) {
      var on = list.filter(function (b) { return b.checked; }).length;
      all.checked = on > 0 && on === list.length;
      all.indeterminate = on > 0 && on < list.length;
    }
  }
  table.addEventListener('change', function (event) {
    var box = event.target;
    if (box.matches('[data-select-item]')) { if (box.checked) selected.add(box.value); else selected.delete(box.value); refresh(); }
    if (box.matches('[data-select-all]')) { boxes().forEach(function (b) { b.checked = box.checked; if (box.checked) selected.add(b.value); else selected.delete(b.value); }); refresh(); }
  });
  var clear = document.querySelector('[data-clear-selection]');
  if (clear) clear.addEventListener('click', function () { selected.clear(); boxes().forEach(function (b) { b.checked = false; }); refresh(); });
  var remove = document.querySelector('[data-confirm-remove]');
  if (remove) remove.addEventListener('submit', function (event) {
    var n = selected.size;
    if (!n || !confirm('Remove ' + n + ' item' + (n === 1 ? '' : 's') + ' from the Pricing Sheet and from every service that uses ' + (n === 1 ? 'it' : 'them') + '? Confirmed quote history stays unchanged.')) event.preventDefault();
  });
  refresh();
})();

/* The (i) next to a material: its profile in a window (the link still opens the page on its own without scripts). */
(function () {
  var dialog = document.getElementById('profile-dialog');
  if (!dialog || !dialog.showModal) return;
  var body = dialog.querySelector('[data-profile-body]');
  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[data-profile]');
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    body.innerHTML = '<p class="muted">Loading…</p>';
    dialog.showModal();
    var next = encodeURIComponent(location.pathname + location.search);
    fetch(link.href + (link.href.indexOf('?') < 0 ? '?' : '&') + 'partial=1&next=' + next, {headers: {'X-Requested-With': 'fetch'}})
      .then(function (response) { if (!response.ok) throw new Error(response.status); return response.text(); })
      .then(function (html) { body.innerHTML = html; })
      .catch(function () { location.href = link.href; });
  });
  dialog.addEventListener('click', function (event) { if (event.target === dialog || event.target.closest('[data-profile-close]')) dialog.close(); });
})();
