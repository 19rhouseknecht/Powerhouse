/* A service's drawing: click a part in the materials list to see everywhere it is on the drawing; "Mark parts on the
   drawing" places those marks (a click adds one for the picked part, a click on a mark takes it off; Save keeps them). */
(function () {
  var node = document.getElementById("drawing-spots-data");
  if (!node) return;
  var data = JSON.parse(node.textContent), spots = data.spots || {}, active = null;
  var COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308", "#a855f7", "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16"];
  var parts = [], seen = {};
  data.parts.forEach(function (key, i) { if (!seen[key]) { seen[key] = true; parts.push({key: key, name: data.names[i], color: COLORS[parts.length % COLORS.length]}); } });
  function colorOf(key) { var p = parts.find(function (x) { return x.key === key; }); return p ? p.color : COLORS[0]; }
  function dot(layer, mark, color, extra) {
    var d = document.createElement(extra === "button" ? "button" : "span");
    d.className = "drawing-mark" + (extra === "pulse" ? " is-pulsing" : "");
    d.style.left = mark.x + "%"; d.style.top = mark.y + "%"; d.style.setProperty("--mark", color);
    if (extra === "button") { d.type = "button"; d.setAttribute("aria-label", "Take this mark off"); }
    layer.append(d); return d;
  }

  /* the page: a row highlights its part's marks */
  var marks = document.querySelector("[data-drawing-marks]"), stage = document.querySelector("[data-drawing-stage]"), hint = document.querySelector("[data-drawing-hint]");
  function show(key) {
    active = active === key ? null : key;
    marks.replaceChildren();
    document.querySelectorAll(".spot-row").forEach(function (row) { row.classList.toggle("is-active", row.getAttribute("data-spot-key") === active); });
    if (!active) { hint.textContent = "Click a part in the materials list to see where it is."; return; }
    var list = spots[active] || [], name = (parts.find(function (p) { return p.key === active; }) || {}).name || "This part";
    list.forEach(function (mark) { dot(marks, mark, colorOf(active), "pulse"); });
    hint.textContent = list.length ? name + ": " + list.length + " spot" + (list.length === 1 ? "" : "s") + " on the drawing." : name + " isn't marked on the drawing yet. Use Mark parts on the drawing.";
    var box = stage.getBoundingClientRect();
    if (list.length && (box.top < 0 || box.bottom > window.innerHeight)) stage.scrollIntoView({block: "nearest", behavior: "smooth"});
  }
  document.querySelectorAll(".spot-row").forEach(function (row) {
    var key = row.getAttribute("data-spot-key");
    row.addEventListener("click", function (e) { if (!e.target.closest("a, button, input, label")) show(key); });
    row.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(key); } });
  });

  /* the marking window */
  var dialog = document.getElementById("marker-dialog");
  if (!dialog) return;
  var list = dialog.querySelector("[data-marker-parts]"), layer = dialog.querySelector("[data-marker-marks]"), mstage = dialog.querySelector("[data-marker-stage]");
  var status = dialog.querySelector("[data-marker-status]"), working = {}, picked = parts.length ? parts[0].key : null;
  function counts() { list.querySelectorAll("[data-part]").forEach(function (b) { b.querySelector("em").textContent = (working[b.getAttribute("data-part")] || []).length; }); }
  function draw() {
    layer.replaceChildren();
    Object.keys(working).forEach(function (key) {
      (working[key] || []).forEach(function (mark, index) {
        var d = dot(layer, mark, colorOf(key), "button");
        d.classList.toggle("is-dim", key !== picked);
        d.addEventListener("click", function (e) { e.stopPropagation(); working[key].splice(index, 1); draw(); counts(); });
      });
    });
  }
  parts.forEach(function (part) {
    var b = document.createElement("button"); b.type = "button"; b.className = "marker-part"; b.setAttribute("role", "radio"); b.setAttribute("data-part", part.key);
    var sw = document.createElement("i"); sw.style.background = part.color;
    var name = document.createElement("span"); name.textContent = part.name;
    var n = document.createElement("em");
    b.append(sw, name, n);
    b.addEventListener("click", function () { picked = part.key; list.querySelectorAll("[data-part]").forEach(function (x) { x.setAttribute("aria-checked", x === b ? "true" : "false"); }); draw(); });
    list.append(b);
  });
  mstage.addEventListener("click", function (e) {
    if (!picked || e.target.closest(".drawing-mark")) return;
    var box = mstage.getBoundingClientRect();
    var x = Math.round((e.clientX - box.left) / box.width * 10000) / 100, y = Math.round((e.clientY - box.top) / box.height * 10000) / 100;
    (working[picked] = working[picked] || []).push({x: x, y: y});
    draw(); counts();
  });
  document.querySelector("[data-open-marker]").addEventListener("click", function () {
    working = JSON.parse(JSON.stringify(spots));
    status.textContent = "";
    list.querySelectorAll("[data-part]").forEach(function (x) { x.setAttribute("aria-checked", x.getAttribute("data-part") === picked ? "true" : "false"); });
    draw(); counts();
    dialog.showModal ? dialog.showModal() : dialog.setAttribute("open", "");
  });
  dialog.querySelectorAll("[data-marker-close]").forEach(function (b) { b.addEventListener("click", function () { dialog.close(); }); });
  dialog.querySelector("[data-marker-save]").addEventListener("click", function () {
    var body = new FormData(); body.append("csrf_token", data.csrf); body.append("spots", JSON.stringify(working));
    status.textContent = "Saving…";
    fetch(data.save, {method: "POST", body: body, headers: {"X-Requested-With": "fetch"}})
      .then(function (r) { return r.json(); })
      .then(function (reply) {
        if (!reply.ok) { status.textContent = reply.error || "Not saved."; return; }
        spots = reply.spots;
        document.querySelectorAll(".spot-row").forEach(function (row) {
          var n = (spots[row.getAttribute("data-spot-key")] || []).length, badge = row.querySelector("[data-spot-count]");
          if (n && !badge) { badge = document.createElement("span"); badge.className = "spot-count"; badge.setAttribute("data-spot-count", ""); row.querySelector("td strong").after(" ", badge); }
          if (badge) { badge.textContent = "◎ " + n; badge.hidden = !n; }
        });
        dialog.close();
        if (active) { var again = active; active = null; show(again); }
        hint.textContent = "Saved. Click a part in the materials list to see where it is.";
      })
      .catch(function () { status.textContent = "Not saved: check the connection."; });
  });
})();
