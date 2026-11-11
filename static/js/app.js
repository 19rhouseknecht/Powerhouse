document.addEventListener("DOMContentLoaded", () => {
  const menuButton = document.querySelector("[data-menu-toggle]");
  const menuClose = document.querySelector("[data-menu-close]");
  const closeMenu = () => document.body.classList.remove("menu-open");
  if (menuButton) menuButton.addEventListener("click", () => document.body.classList.toggle("menu-open"));
  if (menuClose) menuClose.addEventListener("click", closeMenu);
  document.querySelectorAll(".sidebar a").forEach(link => link.addEventListener("click", closeMenu));
  const catalogNode = document.getElementById("pricing-catalog");
  const pricingCatalog = catalogNode ? JSON.parse(catalogNode.textContent) : [];
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
    const choose = item => {
      input.value = item.name; hidden.value = item.id; unit.value = item.unit; price.value = Number(item.price).toFixed(2);
      if (cooperPart) cooperPart.value = item.cooper_part_number || "";
      menu.classList.remove("open");
    };
    const show = (clearSelection = false) => {
      const query = input.value.trim().toLowerCase();
      if (clearSelection) {
        hidden.value = "";
        if (cooperPart) cooperPart.value = "";
      }
      if (!query) { menu.classList.remove("open"); return; }
      const matches = pricingCatalog.filter(item => item.name.toLowerCase().includes(query) || (item.cooper_part_number || "").toLowerCase().includes(query)).slice(0, 7);
      menu.innerHTML = "";
      matches.forEach(item => {
        const option = document.createElement("button"); option.type = "button"; option.className = "price-suggestion";
        const title = document.createElement("strong"); title.textContent = item.name;
        const meta = document.createElement("span"); meta.textContent = `${item.cooper_part_number ? `Cooper #${item.cooper_part_number} · ` : ""}$${Number(item.price).toFixed(2)} / ${item.unit}`;
        option.append(title, meta);
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
        if (!response.ok) throw new Error(`Status request failed (${response.status})`);
        const data = await response.json();
        const percent = data.total ? Math.round((data.current / data.total) * 100) : (data.status === "complete" ? 100 : 4);
        progress.style.width = `${Math.max(4, percent)}%`;
        log.replaceChildren(...data.logs.map(message => { const line = document.createElement("p"); line.textContent = message; return line; }));
        log.scrollTop = log.scrollHeight;
        if (data.status === "complete") { preview.href = data.preview_url; preview.hidden = false; clearInterval(cooperPoll); }
        else if (data.status === "failed") { cooperBot.classList.add("bot-failed"); clearInterval(cooperPoll); }
      } catch (error) {
        const line = document.createElement("p"); line.textContent = `Activity window lost contact: ${error.message}`; log.append(line); clearInterval(cooperPoll);
      }
    };
    clearInterval(cooperPoll); poll(); cooperPoll = setInterval(poll, 1000);
  };
  cooperForms.forEach(cooperForm => cooperForm.addEventListener("submit", async event => {
    event.preventDefault();
    const button = cooperForm.querySelector("button"); button.disabled = true; button.textContent = "Starting Cooper…";
    try {
      const response = await fetch(cooperForm.action, {method:"POST", body:new FormData(cooperForm), headers:{"Accept":"application/json"}});
      const data = await response.json();
      if (!response.ok && response.status !== 409) throw new Error(data.error || `Could not start (${response.status})`);
      showCooperBot(data.status_url); button.textContent = response.status === 409 ? "Update already in progress" : "Price check queued";
    } catch (error) {
      button.disabled = false; button.textContent = "Check Cooper prices"; alert(`Cooper price check could not start: ${error.message}`);
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
