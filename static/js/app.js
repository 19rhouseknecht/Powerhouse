document.addEventListener("DOMContentLoaded", () => {
  const menuButton = document.querySelector("[data-menu-toggle]");
  const menuClose = document.querySelector("[data-menu-close]");
  const closeMenu = () => document.body.classList.remove("menu-open");
  if (menuButton) menuButton.addEventListener("click", () => document.body.classList.toggle("menu-open"));
  if (menuClose) menuClose.addEventListener("click", closeMenu);
  document.querySelectorAll(".sidebar a").forEach(link => link.addEventListener("click", closeMenu));
  const catalogNode = document.getElementById("pricing-catalog");
  const pricingCatalog = catalogNode ? JSON.parse(catalogNode.textContent) : [];
  const findNode = document.getElementById("pricing-find");
  const findUrl = findNode ? JSON.parse(findNode.textContent) : "";
  const toolCatalogNode = document.getElementById("tool-catalog");
  const toolCatalog = toolCatalogNode ? JSON.parse(toolCatalogNode.textContent) : [];
  const closeSuggestions = except => document.querySelectorAll(".price-suggestions.open").forEach(menu => { if (menu !== except) menu.classList.remove("open"); });
  const bindItemRow = row => {
    const input = row.querySelector(".item-name-input");
    if (!input || input.dataset.bound) return;
    input.dataset.bound = "true";
    const menu = row.querySelector(".price-suggestions");
    const hidden = row.querySelector('input[name="item_price_id[]"]');
    const unit = row.querySelector(".item-unit-input");
    const price = row.querySelector(".item-price-input");
    const cooperPart = row.querySelector(".item-cooper-input");
    const pictureBox = row.querySelector("[data-item-pic]");
    const thumb = (item, cls) => {                       // an item's picture, or a placeholder that fills in when Cooper brings it
      const pic = document.createElement(item && item.image ? "img" : "span");
      pic.className = cls + (item && item.image ? "" : " " + cls + "-none");
      if (item && item.image) { pic.src = item.image; pic.alt = ""; pic.loading = "lazy"; }
      else { pic.textContent = "▢"; pic.title = item ? "No picture yet" : "Pick an item to see its picture"; if (item && item.id) pic.setAttribute("data-pic-for", item.id); }
      return pic;
    };
    const choose = item => {
      input.value = item.name; hidden.value = item.id; unit.value = item.unit; price.value = Number(item.price).toFixed(2);
      if (cooperPart) cooperPart.value = item.cooper_part_number || "";
      if (pictureBox) pictureBox.replaceChildren(thumb(item, "line-thumb"));
      menu.classList.remove("open");
    };
    const show = (clearSelection = false) => {
      const query = input.value.trim().toLowerCase();
      if (clearSelection) {
        hidden.value = "";
        if (cooperPart) cooperPart.value = "";
        if (pictureBox) pictureBox.replaceChildren(thumb(null, "line-thumb"));
      }
      if (!query) { menu.classList.remove("open"); return; }
      if (findUrl) {                                   // search the Pricing Sheet on the server (it can hold Cooper's whole catalog)
        clearTimeout(input._findTimer);
        input._findTimer = setTimeout(() => {
          const asked = input.value.trim();
          if (asked.length < 2) { menu.classList.toggle("open", false); return; }
          fetch(findUrl + "&q=" + encodeURIComponent(asked), {headers: {"Accept": "application/json"}})
            .then(r => r.json())
            .then(data => {
              if (input.value.trim() !== asked) return;
              render((data.results || []).slice(0, 8).map(row => ({id: row.id, name: row.name, unit: row.unit, price: row.price, cooper_part_number: row.part || "", image: row.image || "", unconfirmed: (row.kind || "").includes("Unconfirmed")})));
              if (data.picture_watch && window.PHWatchPictures) window.PHWatchPictures(data.picture_watch);
            }).catch(() => {});
        }, 200);
        return;
      }
      render(pricingCatalog.filter(item => item.name.toLowerCase().includes(query) || (item.cooper_part_number || "").toLowerCase().includes(query)).slice(0, 7));
    };
    const render = matches => {
      menu.innerHTML = "";
      matches.forEach(item => {
        const option = document.createElement("button"); option.type = "button"; option.className = "price-suggestion has-pic";
        const title = document.createElement("strong"); title.textContent = item.name;
        const meta = document.createElement("span"); meta.textContent = `${item.cooper_part_number ? `Cooper #${item.cooper_part_number} · ` : ""}$${Number(item.price).toFixed(2)} / ${item.unit}${item.unconfirmed ? " · Unconfirmed" : ""}`;
        const words = document.createElement("span"); words.className = "price-suggestion-words"; words.append(title, meta);
        option.append(thumb(item, "pick-thumb"), words);
        option.addEventListener("mousedown", event => { event.preventDefault(); choose(item); }); menu.append(option);
      });
      menu.classList.toggle("open", matches.length > 0); closeSuggestions(menu);
    };
    input.addEventListener("input", () => show(true)); input.addEventListener("focus", () => show(false));
    input.addEventListener("blur", () => setTimeout(() => menu.classList.remove("open"), 120));
  };
  const bindAllItemRows = () => document.querySelectorAll(".item-row").forEach(bindItemRow);
  const bindToolRow = row => {
    const input = row.querySelector(".tool-name-input");
    if (!input || input.dataset.bound) return;
    input.dataset.bound = "true";
    const menu = row.querySelector(".tool-suggestions");
    const hidden = row.querySelector('input[name="tool_id[]"]');
    const preview = row.querySelector(".tool-type-preview");
    const renderPreview = tool => {
      if (!preview) return;
      preview.innerHTML = "";
      const label = document.createElement("small"); label.textContent = "Type";
      const type = document.createElement("strong"); type.textContent = tool ? tool.tool_type : "New tool";
      preview.append(label, type);
      if (tool && tool.tool_type.toLowerCase() === "rental") { const cost = document.createElement("span"); cost.textContent = `$${Number(tool.rental_cost || 0).toFixed(2)} rental`; preview.append(cost); }
    };
    const choose = tool => { input.value = tool.name; hidden.value = tool.id; renderPreview(tool); menu.classList.remove("open"); };
    const show = clearSelection => {
      if (clearSelection) { hidden.value = ""; renderPreview(null); }
      const query = input.value.trim().toLowerCase();
      if (!query) { menu.classList.remove("open"); return; }
      const matches = toolCatalog.filter(tool => tool.name.toLowerCase().includes(query) || tool.tool_type.toLowerCase().includes(query)).slice(0, 7);
      menu.innerHTML = "";
      matches.forEach(tool => {
        const option = document.createElement("button"); option.type = "button"; option.className = "price-suggestion";
        const title = document.createElement("strong"); title.textContent = tool.name;
        const meta = document.createElement("span"); meta.textContent = `${tool.tool_type}${tool.tool_type.toLowerCase() === "rental" ? ` · $${Number(tool.rental_cost || 0).toFixed(2)}` : ""}`;
        option.append(title, meta); option.addEventListener("mousedown", event => { event.preventDefault(); choose(tool); }); menu.append(option);
      });
      menu.classList.toggle("open", matches.length > 0); closeSuggestions(menu);
    };
    input.addEventListener("input", () => show(true)); input.addEventListener("focus", () => show(false)); input.addEventListener("blur", () => setTimeout(() => menu.classList.remove("open"), 120));
  };
  const bindAllToolRows = () => document.querySelectorAll(".tool-row").forEach(bindToolRow);
  const reindexTools = () => document.querySelectorAll("#tools-rows .tool-row").forEach((row, index) => {
    const checkbox = row.querySelector('input[name="tool_critical[]"]');
    if (checkbox) checkbox.value = index;
  });
  document.querySelectorAll("[data-add-row]").forEach(button => button.addEventListener("click", () => {
    const type = button.dataset.addRow;
    const template = document.getElementById(type === "items" ? "item-row-template" : "tool-row-template");
    document.getElementById(`${type}-rows`).append(template.content.cloneNode(true));
    reindexTools();
    bindAllItemRows();
    bindAllToolRows();
  }));
  document.addEventListener("click", event => {
    if (!event.target.classList.contains("remove-row")) return;
    const row = event.target.closest(".repeater-row");
    const parent = row.parentElement;
    if (parent.querySelectorAll(".repeater-row").length > 1) row.remove();
    else row.querySelectorAll("input").forEach(input => { input.value = input.type === "number" ? (input.name.includes("quantity") ? "1" : "0.00") : ""; input.checked = false; });
    reindexTools();
  });
  const fileInput = document.querySelector('input[name="drawing"]');
  if (fileInput) fileInput.addEventListener("change", () => { if (fileInput.files[0]) document.querySelector("[data-file-name]").textContent = fileInput.files[0].name; });
  const clientSelect = document.getElementById("job-client");
  const copyAddress = () => {
    if (!clientSelect) return;
    const option = clientSelect.selectedOptions[0];
    const fields = { line1: "job-address-line1", line2: "job-address-line2", city: "job-city", state: "job-state", postal: "job-postal" };
    Object.entries(fields).forEach(([key, id]) => { const input = document.getElementById(id); if (input) input.value = option.dataset[key] || ""; });
  };
  const copyButton = document.getElementById("copy-client-address");
  if (copyButton) copyButton.addEventListener("click", copyAddress);
  if (clientSelect && copyButton && !document.getElementById("job-address-line1").value) copyAddress();
  const mapButton = document.querySelector("[data-map-preview]");
  const mapFrame = document.querySelector("[data-job-map]");
  const updateMap = () => {
    if (!mapFrame) return;
    const ids = ["job-address-line1", "job-address-line2", "job-city", "job-state", "job-postal"];
    const address = ids.map(id => document.getElementById(id)?.value.trim()).filter(Boolean).join(", ");
    if (address) { mapFrame.src = `https://www.google.com/maps?q=${encodeURIComponent(address)}&output=embed`; mapFrame.classList.add("visible"); }
  };
  if (mapButton) mapButton.addEventListener("click", updateMap);
  if (mapFrame && document.getElementById("job-address-line1")?.value) updateMap();
  const chatMessages = document.querySelector(".chat-messages");
  if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
  const formatDuration = seconds => { seconds = Math.max(Math.floor(seconds), 0); const h = Math.floor(seconds / 3600); const m = Math.floor((seconds % 3600) / 60); const s = seconds % 60; return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`; };
  document.querySelectorAll(".live-timer[data-start]").forEach(timer => { const start = new Date(`${timer.dataset.start}Z`); const update = () => { timer.textContent = formatDuration((Date.now() - start.getTime()) / 1000); }; update(); setInterval(update, 1000); });
  // Read a fetch() reply as JSON; when the site sent a page instead (signed out, an old page, an error), say so in words.
  const readJson = async response => {
    const text = await response.text();
    try { return JSON.parse(text); } catch (error) {
      if (response.redirected && /\/login/.test(response.url)) return {error: "You were signed out. Refresh the page, sign in, and try again."};
      if (response.status === 400) return {error: "This page is out of date. Refresh the page and try again."};
      if (response.status >= 500) return {error: `The site hit an error (${response.status}). Try again in a minute; the site may have been updating.`};
      return {error: `The site sent back a page instead of an answer (${response.status}). Refresh the page and try again.`};
    }
  };
  const cooperForms = document.querySelectorAll("[data-cooper-sync-form]");
  const cooperBot = document.querySelector("[data-cooper-bot]");
  let cooperPoll = null;
  const showCooperBot = statusUrl => {
    if (!cooperBot) return;
    cooperBot.hidden = false; cooperBot.dataset.statusUrl = statusUrl;
    const log = cooperBot.querySelector("[data-bot-log]");
    const progress = cooperBot.querySelector("[data-bot-progress]");
    const preview = cooperBot.querySelector("[data-bot-preview]");
    const poll = async () => {
      try {
        const response = await fetch(statusUrl, {headers:{"Accept":"application/json"}});
        const data = await readJson(response);
        if (!response.ok || data.error && !data.status) throw new Error(data.error || `Status request failed (${response.status})`);
        const percent = data.total ? Math.round((data.current / data.total) * 100) : (data.status === "complete" ? 100 : 4);
        progress.style.width = `${Math.max(4, percent)}%`;
        log.replaceChildren(...data.logs.map(message => { const line = document.createElement("p"); line.textContent = message; return line; }));
        log.scrollTop = log.scrollHeight;
        if (data.status === "complete") { preview.href = data.preview_url; if (data.preview_label) preview.textContent = data.preview_label; preview.hidden = false; clearInterval(cooperPoll); }
        else if (data.status === "failed") { cooperBot.classList.add("bot-failed"); clearInterval(cooperPoll); }
      } catch (error) {
        const line = document.createElement("p"); line.textContent = `Activity window lost contact: ${error.message}`; log.append(line); clearInterval(cooperPoll);
      }
    };
    clearInterval(cooperPoll); poll(); cooperPoll = setInterval(poll, 1000);
  };
  cooperForms.forEach(cooperForm => cooperForm.addEventListener("submit", async event => {
    event.preventDefault();
    const button = cooperForm.querySelector("button"), label = button.textContent; button.disabled = true; button.textContent = "Starting Cooper…";
    try {
      const response = await fetch(cooperForm.action, {method:"POST", body:new FormData(cooperForm), headers:{"Accept":"application/json"}});
      const data = await readJson(response);
      if (!data.status_url || (!response.ok && response.status !== 409)) throw new Error(data.error || `Could not start (${response.status})`);
      showCooperBot(data.status_url); button.textContent = response.status === 409 ? "Update already in progress" : "Price check queued";
    } catch (error) {
      button.disabled = false; button.textContent = label; alert(`Cooper could not start: ${error.message}`);
    }
  }));
  if (cooperBot) {
    cooperBot.querySelector("[data-bot-close]").addEventListener("click", () => { cooperBot.hidden = true; clearInterval(cooperPoll); });
    if (cooperBot.dataset.statusUrl) showCooperBot(cooperBot.dataset.statusUrl);
  }
  reindexTools();
  bindAllItemRows();
  bindAllToolRows();
});

(function () {           /* your name, top right: the menu, and Modern / Dark switched at once (kept on your account) */
  var menu = document.querySelector('[data-user-menu]');
  if (!menu) return;
  var button = menu.querySelector('[data-user-menu-button]'), panel = menu.querySelector('[data-user-menu-panel]'), form = menu.querySelector('[data-theme-form]');
  function open(yes) { panel.hidden = !yes; button.setAttribute('aria-expanded', yes ? 'true' : 'false'); }
  button.addEventListener('click', function () { open(panel.hidden); });
  document.addEventListener('click', function (e) { if (!panel.hidden && !menu.contains(e.target)) open(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) { open(false); button.focus(); } });
  form.addEventListener('submit', function (e) {
    var choice = e.submitter && e.submitter.value;
    if (!choice || !window.fetch) return;                         // no script help: the form posts and the page comes back in the new look
    e.preventDefault();
    var look = choice === 'dark' ? 'dark' : 'modern';
    document.documentElement.setAttribute('data-theme', look);
    form.querySelectorAll('[role="radio"]').forEach(function (b) { b.setAttribute('aria-checked', b.value === look ? 'true' : 'false'); });
    var meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.setAttribute('content', look === 'dark' ? '#171412' : '#f6f5fb');
    var body = new FormData(form); body.append('theme', look);
    fetch(form.action, {method: 'POST', body: body, headers: {'X-Requested-With': 'fetch'}}).catch(function () {});
  });
})();


/* Forms that save in place (Settings -> users): no reload to the top. A problem marks its field in red and says what's
   wrong, and nothing typed is cleared. A form that must reload (adding a user) comes back at the same place. */
(function () {
  try {
    var kept = JSON.parse(sessionStorage.getItem("keep-scroll") || "null");
    if (kept && kept.path === location.pathname) { sessionStorage.removeItem("keep-scroll"); window.scrollTo(0, kept.y); }
  } catch (e) {}
  function keepScroll() { try { sessionStorage.setItem("keep-scroll", JSON.stringify({path: location.pathname, y: window.scrollY})); } catch (e) {} }
  document.querySelectorAll("form[data-keep-scroll]").forEach(function (form) { form.addEventListener("submit", keepScroll); });
  document.querySelectorAll("form[data-inline-save]").forEach(function (form) {
    var said = document.querySelector('[data-said-for="' + form.id + '"]');
    function fields() { return Array.prototype.slice.call(form.elements); }
    function clearErrors() {
      fields().forEach(function (f) { f.classList.remove("field-error"); f.removeAttribute("aria-invalid"); });
      document.querySelectorAll('[data-error-for="' + form.id + '"]').forEach(function (n) { n.remove(); });
    }
    form.addEventListener("submit", function (e) {
      if (!window.fetch) { keepScroll(); return; }
      e.preventDefault();
      clearErrors();
      if (said) { said.className = "inline-said"; said.textContent = "Saving…"; }
      fetch(form.action, {method: "POST", body: new FormData(form), headers: {"X-Requested-With": "fetch", "Accept": "application/json"}})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            if (d.reload) { keepScroll(); location.reload(); return; }
            if (said) { said.className = "inline-said is-ok"; said.textContent = "✓ " + (d.message || "Saved"); setTimeout(function () { said.textContent = ""; }, 2500); }
            if (d.clear) fields().forEach(function (f) { if (f.type === "password") f.value = ""; });
            return;
          }
          if (said) { said.className = "inline-said is-bad"; said.textContent = ""; }
          var bad = d.field ? fields().find(function (f) { return f.name === d.field; }) : null;
          var note = document.createElement("small"); note.className = "field-error-note"; note.setAttribute("data-error-for", form.id); note.textContent = d.error || "Not saved.";
          if (bad) {
            bad.classList.add("field-error"); bad.setAttribute("aria-invalid", "true");
            (bad.closest("label") || bad.parentElement).append(note);
            bad.focus();
            bad.addEventListener("input", function once() { bad.classList.remove("field-error"); bad.removeAttribute("aria-invalid"); note.remove(); bad.removeEventListener("input", once); });
          } else if (said) { said.textContent = d.error || "Not saved."; }
        })
        .catch(function () { if (said) { said.className = "inline-said is-bad"; said.textContent = "Not saved: check the connection."; } });
    });
  });
})();

/* After "Update from Cooper": follow the office listener's progress and, when it's done, reload in place (same scroll)
   so the new pictures show without a manual refresh. Prices still come back for review. */
window.PHWatchCooper = function (statusUrl, said, reviewUrl) {
  var started = Date.now();
  function line(text) {
    said.textContent = text + " ";
    if (reviewUrl) { var a = document.createElement("a"); a.href = reviewUrl; a.target = "_blank"; a.textContent = "Review prices"; said.append(a); }
  }
  (function check() {
    fetch(statusUrl, {headers: {"Accept": "application/json"}}).then(function (r) { return r.json(); }).then(function (d) {
      if (d.status === "complete" || d.status === "failed" || d.status === "cancelled") {
        if (d.status !== "complete") { line("Cooper update " + d.status + (d.error ? ": " + d.error : ".")); return; }
        line("Done: showing the new pictures…");
        try { sessionStorage.setItem("keep-scroll", JSON.stringify({path: location.pathname, y: window.scrollY})); } catch (e) {}
        setTimeout(function () { location.reload(); }, 600);
        return;
      }
      var mins = Math.floor((Date.now() - started) / 60000);
      line((d.status === "queued" ? "Waiting for the office listener" : "Getting pictures and prices from Cooper") +
           (d.total ? " (" + (d.current || 0) + " of " + d.total + ")" : "") + (mins ? ", " + mins + " min" : "") + "… the page updates by itself.");
      setTimeout(check, 4000);
    }).catch(function () { setTimeout(check, 8000); });
  })();
};


/* While a page waits for an answer: after a button sends a form (and the browser goes to the next page), a loading
   overlay says what's happening and stops other clicks, so nothing is clicked twice. It only shows if the wait passes
   a quarter second; forms that save in place (fetch) or were cancelled (a confirm box) never show it. A button's
   data-busy="…" says what it's doing; after 20 seconds it says it's still working and can be closed. */
(function () {
  var overlay = null, timer = null, slow = null;
  function build() {
    overlay = document.createElement("div");
    overlay.className = "busy-overlay"; overlay.hidden = true;
    overlay.setAttribute("role", "alertdialog"); overlay.setAttribute("aria-live", "assertive"); overlay.setAttribute("aria-busy", "true");
    overlay.innerHTML = '<div class="busy-card"><span class="busy-spinner" aria-hidden="true"></span><strong data-busy-words>Working on it…</strong>' +
      '<small data-busy-slow hidden>Still working: this can take a moment. <button type="button" class="btn btn-ghost btn-small" data-busy-close>Close</button></small></div>';
    document.body.append(overlay);
    overlay.querySelector("[data-busy-close]").addEventListener("click", hide);
  }
  function show(words) {
    if (!overlay) build();
    overlay.querySelector("[data-busy-words]").textContent = words || "Working on it…";
    overlay.querySelector("[data-busy-slow]").hidden = true;
    overlay.hidden = false; document.documentElement.classList.add("is-busy");
    slow = setTimeout(function () { overlay.querySelector("[data-busy-slow]").hidden = false; }, 20000);
  }
  function hide() {
    clearTimeout(timer); clearTimeout(slow);
    if (overlay) overlay.hidden = true;
    document.documentElement.classList.remove("is-busy");
  }
  document.addEventListener("submit", function (e) {
    var form = e.target, by = e.submitter;
    setTimeout(function () {                              /* after every other handler: did one stop it (fetch, confirm)? */
      if (e.defaultPrevented || form.target === "_blank" || form.hasAttribute("data-no-busy") || (by && by.hasAttribute("data-no-busy"))) return;
      var words = (by && by.getAttribute("data-busy")) || form.getAttribute("data-busy");
      clearTimeout(timer);
      timer = setTimeout(function () { show(words); }, 250);
    }, 0);
  });
  window.addEventListener("pageshow", hide);               /* coming back with the Back button */
  window.PHBusy = {show: show, hide: hide};
})();


/* Pictures asked for by themselves (Generate quote): while the office listener looks for them, every placeholder
   [data-pic-for="<item id>"] on the page becomes its picture as soon as it is saved. Watches until the run ends. */
window.PHWatchPictures = (function () {
  var watching = {};
  function placeholders() { return Array.prototype.slice.call(document.querySelectorAll("[data-pic-for]")); }
  function fill(pictures) {
    placeholders().forEach(function (el) {
      var src = pictures[el.getAttribute("data-pic-for")];
      if (!src) return;
      var img = document.createElement("img");
      img.src = src; img.alt = ""; img.loading = "lazy";
      img.className = el.className.replace(/\b(pick|line)-thumb-none\b/g, "").trim();
      el.replaceWith(img);
    });
  }
  return function (statusUrl) {
    if (!statusUrl || watching[statusUrl]) return;
    watching[statusUrl] = true;
    var picturesUrl = (document.querySelector("meta[name=ph-pictures]") || {}).content || "/quoting/pricing/pictures.json";
    function tick() {
      var ids = placeholders().map(function (el) { return el.getAttribute("data-pic-for"); });
      var looked = ids.length ? fetch(picturesUrl + "?ids=" + ids.join(","), {headers: {"Accept": "application/json"}})
        .then(function (r) { return r.json(); }).then(function (d) { fill(d.pictures || {}); }) : Promise.resolve();
      looked.then(function () { return fetch(statusUrl, {headers: {"Accept": "application/json"}}); })
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.status === "queued" || s.status === "running") { setTimeout(tick, 6000); return; }
          delete watching[statusUrl];
          if (placeholders().length) setTimeout(function () {       /* the last pictures saved as it finished */
            var left = placeholders().map(function (el) { return el.getAttribute("data-pic-for"); });
            fetch(picturesUrl + "?ids=" + left.join(","), {headers: {"Accept": "application/json"}}).then(function (r) { return r.json(); }).then(function (d) { fill(d.pictures || {}); });
          }, 1500);
        })
        .catch(function () { setTimeout(tick, 15000); });
    }
    setTimeout(tick, 4000);
  };
})();
