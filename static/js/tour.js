/* Central Powerhouse tour: update this step list whenever a workflow changes. */
document.addEventListener("DOMContentLoaded", () => {
  const routeNode = document.getElementById("tour-routes");
  const launch = document.querySelector("[data-tour-launch]");
  if (!routeNode || !launch) return;
  const routes = JSON.parse(routeNode.textContent);
  const storageKey = "powerhouse-guided-tour";
  const allSteps = [
    { route: routes.dashboard, target: ".page-heading", title: "Welcome to Powerhouse", text: "This dashboard gives you a quick view of the system and shortcuts into the work your team uses most." },
    { route: routes.dashboard, target: ".sidebar", title: "Your company workspace", text: "The sidebar organizes Powerhouse into Quoting, Clients & Jobs, Time Tracking, Bookkeeping, and Settings. The tour will open each area for you." },
    { route: routes.quotes, target: ".page-heading", title: "Generate Quote", text: "Select a job or capture a quick quote before the job exists. Caden builds all six tiers with minimal questions. Missing library services remain clearly unpriced until you add them." },
    { route: routes.pricing, target: ".page-heading", title: "Pricing Sheet", text: "This is the single source for material prices, Cooper part numbers, and labor rate. The visible Local Cooper Updater scans from an office computer and always creates a preview before anything changes." },
    { route: routes.services, target: ".page-heading", title: "Services", text: "Services are repeatable job building blocks containing materials, example quantities, labor time, required tools, notes, and drawings. Quotes multiply these examples by the needed quantity." },
    { route: routes.clients, target: ".page-heading", title: "Clients", text: "Client records keep names, contact information, primary addresses, notes, and job history together. Create the client before creating work for them." },
    { route: routes.jobs, target: ".page-heading", title: "Jobs", text: "Each job has a number beginning at 700, a materials PO, map links, assignments, dates, costs, tracked visits, and saved quotes." },
    { route: routes.schedule, target: ".page-heading", title: "Schedule", text: "Switch between month, week, and day. Installers see only their assigned schedule; authorized office and foreman roles can see the whole team." },
    { route: routes.onsite, target: ".page-heading", title: "Onsite Checklist", text: "Select a job, review its request, follow Caden's inspection tip, and save room-by-room findings plus client priorities. The saved inspection becomes verified context for the six quote options." },
    { route: routes.reviews, target: ".review-card-screen", title: "Customer Reviews", text: "Open this customer-facing card at the end of a visit. The homeowner can scan the Power House QR code and leave a five-star Google review." },
    { route: routes.clock, target: ".page-heading", title: "Time Clock", text: "Employees clock in for their paid day, then start and stop job visits inside that shift. This separates job time from paid driving, preparation, shop, and general time." },
    { route: routes.timesheet, target: ".page-heading", title: "Timesheet", text: "Payroll uses two-week periods running Thursday through the second Wednesday. Timesheets separate tracked job visits from paid general and drive time." },
    { route: routes.tasks, target: ".page-heading", title: "Tasking and Brendon", text: "Organize To Do, In Progress, and Completed work with categories, priorities, assignments, and due dates. Brendon can interpret conversation to create, assign, update, or complete matching tasks." },
    { route: routes.training, target: ".page-heading", title: "Training and Darwin", text: "Store classes, descriptions, future notes, photos, papers, and reference files. Darwin summarizes the record and answers questions using the information saved with it." },
    { route: routes.finance, target: ".page-heading", title: "Finance dashboard", text: "Review completed-job revenue, materials, tracked employee labor, and remaining margin across weekly, monthly, yearly, or all-time views." },
    { route: routes.expenses, target: ".page-heading", title: "Expenses", text: "Employees can upload receipts, choose an accounting category, request reimbursement, and link direct costs to a job." },
    { route: routes.settings, target: ".page-heading", title: "Administrative settings", text: "Administrators manage the Local Cooper Updater connection token, AI agents, users, hourly pay rates, and the Admin, Office, Foreman, and Installer role permissions here." },
    { route: routes.dashboard, target: ".page-heading", title: "You’re ready", text: "That is the complete Powerhouse workspace. Restart this tour anytime, and add new features to this centralized guide as the system grows.", finish: true },
  ];
  const steps = allSteps.filter(step => { const routeKey = Object.keys(routes).find(key => routes[key] === step.route); return !routes.allowed || routes.allowed[routeKey] !== false; });
  let state = null;
  let highlighted = null;
  const cleanPath = value => (value || "/").replace(/\/$/, "") || "/";
  const clearHighlight = () => { if (highlighted) highlighted.classList.remove("tour-highlight"); highlighted = null; };
  const save = () => sessionStorage.setItem(storageKey, JSON.stringify(state));
  const stop = () => { sessionStorage.removeItem(storageKey); state = null; clearHighlight(); document.body.classList.remove("tour-active"); document.querySelector("[data-tour-overlay]")?.remove(); };
  const move = index => { state.index = Math.max(0, Math.min(index, steps.length - 1)); save(); const destination = steps[state.index].route; if (cleanPath(location.pathname) !== cleanPath(destination)) location.assign(destination); else render(); };
  const render = () => {
    if (!state?.active) return;
    const step = steps[state.index] || steps[0];
    if (cleanPath(location.pathname) !== cleanPath(step.route)) { location.assign(step.route); return; }
    clearHighlight();
    const target = document.querySelector(step.target) || document.querySelector("main.app-page");
    if (target) { highlighted = target; target.classList.add("tour-highlight"); target.scrollIntoView({behavior:"smooth", block:"center"}); }
    document.querySelector("[data-tour-overlay]")?.remove();
    const overlay = document.createElement("div"); overlay.className = "tour-overlay"; overlay.dataset.tourOverlay = "";
    const card = document.createElement("section"); card.className = "tour-card"; card.setAttribute("role", "dialog"); card.setAttribute("aria-modal", "true");
    const top = document.createElement("div"); top.className = "tour-card-top";
    const progress = document.createElement("span"); progress.textContent = `Tour ${state.index + 1} of ${steps.length}`;
    const close = document.createElement("button"); close.type = "button"; close.textContent = "×"; close.setAttribute("aria-label", "Exit tour"); close.addEventListener("click", stop); top.append(progress, close);
    const title = document.createElement("h2"); title.textContent = step.title;
    const copy = document.createElement("p"); copy.textContent = step.text;
    const meter = document.createElement("div"); meter.className = "tour-meter"; const fill = document.createElement("span"); fill.style.width = `${((state.index + 1) / steps.length) * 100}%`; meter.append(fill);
    const actions = document.createElement("div"); actions.className = "tour-actions";
    if (state.index > 0) { const back = document.createElement("button"); back.className = "btn btn-secondary"; back.textContent = "Back"; back.addEventListener("click", () => move(state.index - 1)); actions.append(back); }
    const next = document.createElement("button"); next.className = "btn btn-primary"; next.textContent = step.finish ? "Finish Tour" : "Next"; next.addEventListener("click", () => step.finish ? stop() : move(state.index + 1)); actions.append(next);
    card.append(top, title, copy, meter, actions); overlay.append(card); document.body.append(overlay); document.body.classList.add("tour-active"); next.focus();
  };
  launch.addEventListener("click", () => { stop(); state = {active:true, index:0}; save(); move(0); });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && state?.active) stop(); });
  try { state = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_error) { sessionStorage.removeItem(storageKey); }
  if (state?.active) render();
});
