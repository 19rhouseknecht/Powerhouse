/* Quick actions: the round + on every page. Upload expense: a receipt photo the AI reads (store, total, date, a likely
   accounting code), reimbursable to the person signed in, linked to today's job unless another is picked. */
(function () {
  var box = document.querySelector("[data-quick]");
  if (!box) return;
  var fab = box.querySelector("[data-quick-fab]"), menu = box.querySelector("[data-quick-menu]");
  function openMenu(yes) { menu.hidden = !yes; fab.setAttribute("aria-expanded", yes ? "true" : "false"); box.classList.toggle("is-open", yes); }
  fab.addEventListener("click", function () { openMenu(menu.hidden); });
  document.addEventListener("click", function (e) { if (!menu.hidden && !box.contains(e.target)) openMenu(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !menu.hidden) { openMenu(false); fab.focus(); } });

  var dialog = document.getElementById("quick-expense");
  var form = dialog.querySelector("[data-quick-expense]"), codes = form.querySelector("[data-quick-codes]"), jobs = form.querySelector("[data-quick-jobs]");
  var photo = form.querySelector("[data-quick-receipt]"), preview = form.querySelector("[data-quick-preview]"), words = form.querySelector("[data-quick-photo-words]");
  var read = form.querySelector("[data-quick-read]"), status = form.querySelector("[data-quick-status]"), jobNote = form.querySelector("[data-quick-job-note]");
  var loaded = false, token = form.querySelector('input[name="csrf_token"]').value;
  function option(select, value, text) { var o = document.createElement("option"); o.value = value; o.textContent = text; select.append(o); return o; }
  function load() {
    if (loaded) return;
    fetch(dialog.getAttribute("data-options"), {headers: {"Accept": "application/json"}}).then(function (r) { return r.json(); }).then(function (d) {
      loaded = true;
      d.codes.forEach(function (c) { option(codes, c, c); });
      if (!jobs) { /* no Jobs access: no job picker */ }
      else if (d.today.length) {
        var group = document.createElement("optgroup"); group.label = "Today"; jobs.append(group);
        d.today.forEach(function (j) { var o = option(group, j.id, j.label); });
        jobs.value = String(d.today[0].id);
        jobNote.textContent = "Today's job is picked. Change it if this was for another job.";
      } else { jobNote.textContent = "You're not on a job today: pick one, or leave it with no job."; }
      if (jobs) { var rest = document.createElement("optgroup"); rest.label = "Open jobs"; jobs.append(rest);
        d.jobs.forEach(function (j) { option(rest, j.id, j.label); }); }
      if (!d.ai) words.textContent = "📷 Add a photo of the receipt (fill in the details yourself: the AI isn't connected)";
    }).catch(function () { status.textContent = "Couldn't load the jobs: check the connection."; });
  }
  box.querySelector("[data-quick-open]").addEventListener("click", function () {
    openMenu(false); load(); status.textContent = "";
    dialog.showModal ? dialog.showModal() : dialog.setAttribute("open", "");
  });
  dialog.querySelectorAll("[data-quick-close]").forEach(function (b) { b.addEventListener("click", function () { dialog.close(); }); });

  function shrink(file, done) {                         /* a phone photo: at most 1800 px, as JPEG, before it's sent */
    if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 600 * 1024) { done(file); return; }
    var img = new Image();
    img.onload = function () {
      var scale = Math.min(1, 1800 / Math.max(img.width, img.height)), c = document.createElement("canvas");
      c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      c.toBlob(function (b) { done(b ? new File([b], file.name.replace(/\.[^.]+$/, "") + ".jpg", {type: "image/jpeg"}) : file); }, "image/jpeg", 0.85);
    };
    img.onerror = function () { done(file); };
    img.src = URL.createObjectURL(file);
  }
  photo.addEventListener("change", function () {
    var picked = photo.files[0];
    if (!picked) return;
    shrink(picked, function (file) {
      if (file !== picked && window.DataTransfer) { var dt = new DataTransfer(); dt.items.add(file); photo.files = dt.files; }
      if (/^image\//.test(file.type)) { preview.src = URL.createObjectURL(file); preview.hidden = false; }
      words.textContent = file.name;
      if (!/^image\//.test(file.type)) return;
      read.hidden = false; read.textContent = "Reading the receipt…";
      var body = new FormData(); body.append("csrf_token", token); body.append("receipt", file);
      fetch(dialog.getAttribute("data-read"), {method: "POST", body: body, headers: {"X-Requested-With": "fetch"}})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) { read.textContent = d.error; return; }
          if (d.vendor) form.vendor.value = d.vendor;
          if (d.total !== "") form.total.value = Number(d.total).toFixed(2);
          if (d.date) form.expense_date.value = d.date;
          if (d.code) form.category.value = d.code;
          read.textContent = "Read from the receipt" + (d.summary ? ": " + d.summary : "") + ". Check it, then save.";
        })
        .catch(function () { read.textContent = "Couldn't read it just now; fill it in yourself."; });
    });
  });
  form.addEventListener("submit", function (e) {
    if (!window.fetch) return;
    e.preventDefault();
    status.textContent = "Saving…";
    fetch(form.action, {method: "POST", body: new FormData(form), headers: {"X-Requested-With": "fetch"}})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        status.textContent = d.message;
        if (!d.ok) return;
        form.reset(); preview.hidden = true; read.hidden = true; words.textContent = "📷 Take or choose a photo of the receipt";
        loaded = false; codes.length = 1; if (jobs) { jobs.length = 1; jobs.querySelectorAll("optgroup").forEach(function (g) { g.remove(); }); }
        setTimeout(function () { dialog.close(); status.textContent = ""; }, 1400);
      })
      .catch(function () { status.textContent = "Not saved: check the connection."; });
  });
})();
