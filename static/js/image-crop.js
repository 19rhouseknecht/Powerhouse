/* A crop window for pictures before they're uploaded: drag the box to move it, drag its corners to size it, pick a shape,
   turn the picture a quarter at a time. PHCrop.open(fileOrUrl, name, done) calls done(File) with the cropped picture
   (a JPEG, at most 1600 px on its long side), or done(null) when the person keeps the picture as it is. */
(function () {
  var MAX_SIDE = 1600, dialog, stage, img, box, ratioButtons, state = {}, finish = null;
  var SHAPES = [["Free", 0], ["4:3", 4 / 3], ["1:1", 1], ["3:4", 3 / 4], ["16:9", 16 / 9]];

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "sheet-dialog crop-dialog";
    dialog.setAttribute("aria-label", "Crop the picture");
    dialog.innerHTML =
      '<div class="sheet-dialog-head"><h2>Crop the picture</h2><button type="button" class="btn btn-ghost btn-small" data-crop-skip aria-label="Close">×</button></div>' +
      '<div class="crop-body"><div class="crop-stage" data-crop-stage><img alt="Picture to crop" draggable="false"><div class="crop-box" data-crop-box tabindex="0" aria-label="Crop area: drag to move, arrow keys to nudge">' +
      '<i data-h="nw"></i><i data-h="ne"></i><i data-h="sw"></i><i data-h="se"></i></div></div></div>' +
      '<div class="crop-foot"><div class="crop-shapes" role="radiogroup" aria-label="Shape" data-crop-shapes></div>' +
      '<button type="button" class="btn btn-ghost btn-small" data-crop-rotate>⟳ Turn</button>' +
      '<span class="crop-spacer"></span><button type="button" class="btn btn-secondary" data-crop-skip>Use it as it is</button><button type="button" class="btn btn-primary" data-crop-apply>Crop</button></div>';
    document.body.append(dialog);
    stage = dialog.querySelector("[data-crop-stage]"); img = stage.querySelector("img"); box = dialog.querySelector("[data-crop-box]");
    var shapes = dialog.querySelector("[data-crop-shapes]");
    SHAPES.forEach(function (shape) {
      var b = document.createElement("button"); b.type = "button"; b.className = "crop-shape"; b.textContent = shape[0];
      b.setAttribute("role", "radio"); b.setAttribute("data-ratio", shape[1]);
      b.addEventListener("click", function () { setRatio(shape[1]); });
      shapes.append(b);
    });
    ratioButtons = shapes.querySelectorAll("[data-ratio]");
    dialog.querySelectorAll("[data-crop-skip]").forEach(function (b) { b.addEventListener("click", function () { close(null); }); });
    dialog.addEventListener("cancel", function (e) { e.preventDefault(); close(null); });
    dialog.querySelector("[data-crop-apply]").addEventListener("click", apply);
    dialog.querySelector("[data-crop-rotate]").addEventListener("click", rotate);
    box.addEventListener("pointerdown", startDrag);
    box.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 10 : 2, moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]};
      if (!moves[e.key]) return;
      e.preventDefault(); state.x += moves[e.key][0]; state.y += moves[e.key][1]; clampBox(); draw();
    });
    window.addEventListener("resize", function () { if (dialog.open) fit(); });
  }

  function view() { return {w: img.clientWidth, h: img.clientHeight}; }
  function clampBox() {
    var v = view();
    state.w = Math.max(24, Math.min(state.w, v.w)); state.h = Math.max(24, Math.min(state.h, v.h));
    state.x = Math.max(0, Math.min(state.x, v.w - state.w)); state.y = Math.max(0, Math.min(state.y, v.h - state.h));
  }
  function draw() { box.style.left = state.x + "px"; box.style.top = state.y + "px"; box.style.width = state.w + "px"; box.style.height = state.h + "px"; }
  function fit() {                                      // the box starts at the whole picture (or the biggest of the shape)
    var v = view(), r = state.ratio;
    if (!r) { state.x = 0; state.y = 0; state.w = v.w; state.h = v.h; }
    else {
      var w = v.w, h = w / r;
      if (h > v.h) { h = v.h; w = h * r; }
      state.w = w; state.h = h; state.x = (v.w - w) / 2; state.y = (v.h - h) / 2;
    }
    draw();
  }
  function setRatio(r) {
    state.ratio = Number(r) || 0;
    ratioButtons.forEach(function (b) { b.setAttribute("aria-checked", Number(b.getAttribute("data-ratio")) === state.ratio ? "true" : "false"); });
    fit();
  }
  function startDrag(e) {
    e.preventDefault();
    var handle = e.target.getAttribute("data-h"), sx = e.clientX, sy = e.clientY, start = {x: state.x, y: state.y, w: state.w, h: state.h};
    box.setPointerCapture(e.pointerId);
    function move(ev) {
      var dx = ev.clientX - sx, dy = ev.clientY - sy, v = view();
      if (!handle) { state.x = start.x + dx; state.y = start.y + dy; clampBox(); draw(); return; }
      var left = start.x, top = start.y, right = start.x + start.w, bottom = start.y + start.h;
      if (handle.indexOf("w") > -1) left = Math.min(Math.max(0, left + dx), right - 24);
      if (handle.indexOf("e") > -1) right = Math.max(Math.min(v.w, right + dx), left + 24);
      if (handle.indexOf("n") > -1) top = Math.min(Math.max(0, top + dy), bottom - 24);
      if (handle.indexOf("s") > -1) bottom = Math.max(Math.min(v.h, bottom + dy), top + 24);
      var w = right - left, h = bottom - top;
      if (state.ratio) {                                // keep the shape: the width leads, the height follows
        h = w / state.ratio;
        if (handle.indexOf("n") > -1) top = bottom - h; else bottom = top + h;
        if (top < 0 || bottom > v.h) { h = handle.indexOf("n") > -1 ? bottom : v.h - top; w = h * state.ratio; if (handle.indexOf("w") > -1) left = right - w; top = handle.indexOf("n") > -1 ? bottom - h : top; }
      }
      state.x = left; state.y = top; state.w = w; state.h = h; clampBox(); draw();
    }
    function up() { box.removeEventListener("pointermove", move); box.removeEventListener("pointerup", up); box.removeEventListener("pointercancel", up); }
    box.addEventListener("pointermove", move); box.addEventListener("pointerup", up); box.addEventListener("pointercancel", up);
  }
  function rotate() {
    var c = document.createElement("canvas"), w = img.naturalWidth, h = img.naturalHeight;
    c.width = h; c.height = w;
    var g = c.getContext("2d"); g.translate(h, 0); g.rotate(Math.PI / 2); g.drawImage(img, 0, 0);
    load(c.toDataURL("image/jpeg", 0.95));
  }
  function load(src) {
    img.onload = function () { setRatio(state.ratio || 0); };
    img.src = src;
  }
  function apply() {
    var scale = img.naturalWidth / img.clientWidth;
    var sx = state.x * scale, sy = state.y * scale, sw = state.w * scale, sh = state.h * scale;
    var out = Math.min(1, MAX_SIDE / Math.max(sw, sh)), c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(sw * out)); c.height = Math.max(1, Math.round(sh * out));
    var g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
    g.drawImage(img, sx, sy, sw, sh, 0, 0, c.width, c.height);
    c.toBlob(function (blob) { close(blob ? new File([blob], state.name, {type: "image/jpeg"}) : null); }, "image/jpeg", 0.88);
  }
  function close(result) {
    var done = finish; finish = null;
    if (dialog.open) dialog.close();
    if (done) done(result);
  }

  window.PHCrop = {
    open: function (source, name, done) {
      if (!dialog) build();
      finish = done;
      state = {ratio: 0, name: String(name || "picture").replace(/\.[^.]+$/, "") + ".jpg"};
      dialog.showModal ? dialog.showModal() : dialog.setAttribute("open", "");
      load(typeof source === "string" ? source : URL.createObjectURL(source));
    }
  };
})();
