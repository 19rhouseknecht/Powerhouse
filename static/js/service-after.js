/* See it after the upgrade: the AI's picture of the finished job, with a box over each part it found.
   Click a part (or a box) to see it; Before / After switches the photos. While the picture is being made, the page waits. */
(function () {
  /* a phone photo is made smaller before it is sent */
  var form = document.querySelector("[data-after-form]");
  if (form) {
    var input = form.querySelector("[data-after-photo]"), said = form.querySelector("[data-after-picked]");
    input.addEventListener("change", function () {
      var picked = input.files[0];
      if (!picked) return;
      said.textContent = picked.name;
      if (!/^image\/(jpeg|png|webp)$/.test(picked.type) || picked.size < 700 * 1024) return;
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(1, 1536 / Math.max(img.width, img.height)), c = document.createElement("canvas");
        c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(function (blob) {
          if (!blob || !window.DataTransfer) return;
          var dt = new DataTransfer(); dt.items.add(new File([blob], picked.name.replace(/\.[^.]+$/, "") + ".jpg", {type: "image/jpeg"}));
          input.files = dt.files; said.textContent = picked.name + " (" + Math.round(blob.size / 1024) + " KB)";
        }, "image/jpeg", 0.88);
      };
      img.src = URL.createObjectURL(picked);
    });
  }

  var box = document.querySelector("[data-after-result]");
  if (!box) return;
  var data = JSON.parse(box.querySelector("[data-after-data]").textContent);
  var image = box.querySelector("[data-after-image]"), boxes = box.querySelector("[data-after-boxes]"), list = box.querySelector("[data-after-parts]");
  var label = box.querySelector("[data-after-label]"), active = null, showing = "after";
  var COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#eab308", "#a855f7", "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16"];
  function color(name) { var i = data.parts.indexOf(name); return COLORS[(i < 0 ? 0 : i) % COLORS.length]; }
  function pick(name) {
    active = active === name ? null : name;
    boxes.querySelectorAll(".after-box").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-part") === active); b.classList.toggle("is-off", !!active && b.getAttribute("data-part") !== active); });
    list.querySelectorAll("[data-part]").forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-part") === active ? "true" : "false"); });
    label.hidden = !active;
    if (active) label.textContent = active;
  }
  function drawBoxes() {
    boxes.replaceChildren(); list.replaceChildren();
    if (showing !== "after") return;
    data.hotspots.forEach(function (spot) {
      var b = document.createElement("button"); b.type = "button"; b.className = "after-box"; b.setAttribute("data-part", spot.part);
      b.style.left = spot.x + "%"; b.style.top = spot.y + "%"; b.style.width = spot.w + "%"; b.style.height = spot.h + "%"; b.style.setProperty("--mark", color(spot.part));
      b.title = spot.part + (spot.note ? ": " + spot.note : "");
      b.setAttribute("aria-label", b.title);
      b.addEventListener("click", function () { pick(spot.part); });
      boxes.append(b);
    });
    data.parts.forEach(function (name) {
      var count = data.hotspots.filter(function (s) { return s.part === name; }).length;
      var b = document.createElement("button"); b.type = "button"; b.className = "marker-part"; b.setAttribute("data-part", name); b.setAttribute("aria-pressed", "false");
      var sw = document.createElement("i"); sw.style.background = color(name);
      var n = document.createElement("span"); n.textContent = name;
      var c = document.createElement("em"); c.textContent = count ? count + " in picture" : "not seen";
      b.append(sw, n, c); b.disabled = !count;
      b.addEventListener("click", function () { pick(name); });
      list.append(b);
    });
  }
  function show(which) {
    showing = which; active = null; label.hidden = true;
    image.src = which === "after" ? data.image : data.photo;
    image.alt = which === "after" ? "The job after the upgrade (AI picture)" : "The photo before";
    box.querySelectorAll("[data-after-show]").forEach(function (t) { t.setAttribute("aria-selected", t.getAttribute("data-after-show") === which ? "true" : "false"); });
    drawBoxes();
  }
  box.querySelectorAll("[data-after-show]").forEach(function (t) { t.addEventListener("click", function () { show(t.getAttribute("data-after-show")); }); });
  if (data.status === "done") show("after");
  if (data.status === "working") {
    var started = Date.now(), time = box.querySelector("[data-after-time]");
    (function check() {
      fetch(box.getAttribute("data-status-url"), {headers: {"Accept": "application/json"}}).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status !== "working") { location.reload(); return; }
        var s = Math.round((Date.now() - started) / 1000); time.textContent = "(" + s + " s)";
        setTimeout(check, 4000);
      }).catch(function () { setTimeout(check, 8000); });
    })();
  }
})();
