"""Every page is styled: open each page in a real browser with realistic records and fail on any text box,
dropdown, checkbox, button or link that still has the browser's default look, and on any page that scrolls
sideways on a phone. Skipped where no browser is installed."""
import logging
import os
import sys
import threading
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, skip  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed")
    done()

m = make_site()
app, db = m.app, m.db
app.config["TESTING"] = False
with app.app_context():
    admin_role = m.Role.query.filter_by(is_admin=True).first()
    u = m.User(username="owner", role=admin_role, hourly_rate=32); u.set_password("a-long-password-1"); db.session.add(u)
    u2 = m.User(username="caden", role=admin_role, hourly_rate=28); u2.set_password("a-long-password-1"); db.session.add(u2)
    db.session.flush()
    c = m.Client(first_name="Ann", last_name="Lee", email="ann@example.com", phone="717-555-1111", address_line1="12 Main St", city="Lancaster", state="PA", postal_code="17601",
                 company_name="Lee Rentals", tags="Landlord, Repeat", lead_source="Referral", other_contacts="Phone (Work): 717-555-0100", notes="Gate code 1234")
    c2 = m.Client(first_name="Bob", last_name="Stone", email="bob@example.com", phone="717-555-2222", address_line1="9 Oak Ave", city="Lititz", state="PA", postal_code="17543")
    db.session.add_all([c, c2]); db.session.flush()
    db.session.add(m.ClientProperty(client=c, address_line1="40 Rental Rd", city="Lititz", state="PA", postal_code="17543"))
    pi = [m.PriceItem(name=n, lookup_key=n.lower(), unit="ea", price=p, labor_hours_per_unit=h, category="Panels", cooper_part_number="QO120") for n, p, h in
          [("200A panel", 410, 6), ("20A breaker", 12.5, .25), ("12/2 Romex 250ft", 129, 0), ("GFCI outlet", 22, .5)]]
    db.session.add_all(pi); db.session.flush()
    svc = m.Service(name="Panel upgrade 200A", description="Replace the main panel", estimated_labor_hours=8)
    db.session.add(svc); db.session.flush()
    for i, p in enumerate(pi[:3]):
        db.session.add(m.ServiceItem(service=svc, name=p.name, quantity=1 + i, unit="ea", unit_price=p.price, item_type="material") if hasattr(m.ServiceItem, "item_type") else m.ServiceItem(service_id=svc.id, name=p.name))
    tool = m.ToolCatalog(name="Fish tape", lookup_key="fish tape", tool_type="hand")
    db.session.add(tool); db.session.flush()
    db.session.add(m.ServiceTool(service_id=svc.id, name="Fish tape", tool_id=tool.id))
    jobs = []
    for i, (title, status) in enumerate([("Panel upgrade", "scheduled"), ("Outlet repair", "new_request"), ("EV charger", "awaiting_decision"), ("Old job", "completed")]):
        j = m.Job(client=c if i % 2 == 0 else c2, title=title, job_number=700 + i, status=status, address_line1="12 Main St", city="Lancaster", state="PA", postal_code="17601",
                  description="Customer wants the work done before winter.", work_start_date=date.today() + timedelta(days=i), duration_days=2,
                  review_at=datetime.now() + timedelta(days=1), contract_amount=4500, internal_notes="Bring the long ladder.", assigned_user_id=u.id)
        db.session.add(j); jobs.append(j)
    db.session.flush()
    q = m.Quote(job=jobs[2], status="confirmed", title="EV charger — Service Options", option_count=3, confirmed_at=datetime.utcnow())
    qd = m.Quote(job=jobs[1], status="draft", title="Outlet repair — Service Options", option_count=3)
    db.session.add_all([q, qd]); db.session.flush()
    for qq in (q, qd):
        for pos, tier in enumerate(["Economy", "Gold", "Platinum"]):
            o = m.QuoteOption(quote=qq, tier=tier, title=tier + " option", description="What this option includes", position=pos, bullets_json='["Permit", "Cleanup"]')
            db.session.add(o); db.session.flush()
            db.session.add(m.QuoteOptionService(option_id=o.id, service_name="Panel upgrade 200A", quantity=1, unit_price=3000 + pos * 900, price_item_id=pi[0].id) if hasattr(m.QuoteOptionService, "quantity") else m.QuoteOptionService(option_id=o.id, service_name="Panel upgrade"))
    db.session.flush()
    opt = m.QuoteOption.query.filter_by(quote_id=q.id).first()
    mo = m.JobMaterialOrder(job_id=jobs[2].id, quote_option_id=opt.id, status="draft")
    db.session.add(mo); db.session.flush()
    db.session.add(m.JobMaterialOrderItem(order_id=mo.id, name="200A panel", quantity=1) if hasattr(m.JobMaterialOrderItem, "quantity") else m.JobMaterialOrderItem(order_id=mo.id, name="200A panel"))
    db.session.add(m.QuickQuote(description="Hot tub circuit, 50A, 40 ft run"))
    db.session.add(m.Training(title="Panel safety", description="Lockout/tagout basics"))
    db.session.add(m.WorkTask(title="Order the panel", assigned_user_id=u.id) if hasattr(m.WorkTask, "assigned_user_id") else m.WorkTask(title="Order the panel"))
    db.session.add(m.BugReport(title="Price will not save", description="Clicked save, nothing happened", created_by_user_id=u.id) if hasattr(m.BugReport, "created_by_user_id") else m.BugReport(title="x", description="y"))
    db.session.add(m.Expense(submitted_by_user_id=u.id, vendor="Cooper Electric", category="Materials direct job", total=212.5, job_id=jobs[0].id) if hasattr(m.Expense, "job_id") else m.Expense(submitted_by_user_id=u.id, vendor="Cooper", category="Materials direct job"))
    cs = m.ClockSession(user_id=u.id, clock_in_at=datetime.utcnow() - timedelta(hours=3)) if hasattr(m.ClockSession, "clock_in_at") else m.ClockSession(user_id=u.id)
    db.session.add(cs); db.session.flush()
    db.session.add(m.OnsiteInspection(job_id=jobs[0].id))
    db.session.commit()
    ids = {"job": jobs[0].id, "job2": jobs[2].id, "client": c.id, "quote": q.id, "draft_job": jobs[1].id, "service": svc.id,
           "training": m.Training.query.first().id, "agent": m.AiAgent.query.first().id}
    ids = {"job": jobs[0].id, "job2": jobs[2].id, "client": c.id, "quote": q.id, "draft_job": jobs[1].id, "service": svc.id,
           "training": m.Training.query.first().id, "agent": m.AiAgent.query.first().id}

# Allowed on purpose: file pickers are styled through ::file-selector-button (not visible to this check),
# the AI agent prompt editor is monospace, and the review QR code is an image link with no text.
ALLOWED = ("default file picker", "font ui-monospace")
PAGES = ["/", "/flow", "/quoting/generate", "/quoting/generate/%(draft_job)d", "/quotes/%(quote)d", "/quotes/%(quote)d/manual-edit",
         "/quoting/pricing", "/quoting/labor-calculator", "/quoting/pricing/cooper-account", "/quoting/services", "/quoting/services/new",
         "/quoting/services/%(service)d", "/quoting/services/%(service)d/edit", "/quoting/services/%(service)d/order-checklist",
         "/clients", "/clients/new", "/clients/%(client)d", "/clients/%(client)d/edit", "/jobs", "/jobs/new", "/jobs/%(job)d", "/jobs/%(job2)d",
         "/jobs/%(job)d/edit", "/jobs/%(job2)d/materials", "/schedule", "/sales/onsite-checklist", "/sales/onsite-checklist/%(job)d", "/sales/reviews",
         "/time-tracking", "/time-tracking/timesheet", "/bookkeeping/tasks", "/bookkeeping/training", "/bookkeeping/training/new",
         "/bookkeeping/training/%(training)d", "/bookkeeping/training/%(training)d/edit", "/finance", "/finance/expenses",
         "/settings", "/settings/tools", "/settings/bug-reports", "/settings/roles", "/settings/agents/%(agent)d", "/settings/jobber", "/settings/releases", "/generators/sizing?calculate=1", "/caleb"]
AUDIT = r"""() => {
  const bodyFont = getComputedStyle(document.body).fontFamily;
  const found = [];
  const name = el => el.tagName.toLowerCase() + (el.type && el.tagName !== 'SELECT' && el.tagName !== 'TEXTAREA' ? '[' + el.type + ']' : '') + (el.id ? '#' + el.id : '') + (el.name ? '[name=' + el.name + ']' : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '');
  const where = el => { const p = []; let n = el.parentElement; for (let i = 0; i < 3 && n && n !== document.body; i++, n = n.parentElement) p.push(n.tagName.toLowerCase() + (n.className && typeof n.className === 'string' ? '.' + n.className.trim().split(/\s+/)[0] : '')); return p.join(' < '); };
  for (const el of document.querySelectorAll('input:not([type=hidden]), select, textarea, button')) {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if ((r.width === 0 && r.height === 0) || cs.visibility === 'hidden') continue;
    const issues = [];
    const t = (el.type || '').toLowerCase();
    if (t === 'checkbox' || t === 'radio') { if (cs.accentColor === 'auto') issues.push('default checkbox colour'); }
    else if (t === 'file') { if (!el.matches('[class]') && cs.backgroundColor === 'rgba(0, 0, 0, 0)') issues.push('default file picker'); }
    else {
      if (cs.fontFamily !== bodyFont) issues.push('font ' + cs.fontFamily.split(',')[0]);
      const bg = cs.backgroundColor;
      if (el.tagName === 'BUTTON' || t === 'submit' || t === 'button') { if (bg === 'rgb(239, 239, 239)' || bg === 'rgb(240, 240, 240)' || bg === 'buttonface') issues.push('default button'); }
      else {
        if (bg === 'rgb(255, 255, 255)' || bg === 'rgb(250, 250, 250)' || bg === 'field') issues.push('white background');
        if (cs.borderTopStyle === 'inset' || cs.borderTopColor === 'rgb(118, 118, 118)' || cs.borderTopColor === 'rgb(133, 133, 133)' || cs.borderTopColor === 'rgb(169, 169, 169)') issues.push('default border');
        if (cs.borderTopLeftRadius === '0px' && cs.borderTopStyle !== 'none') issues.push('square corners');
        if (r.height < 30 && el.tagName !== 'BUTTON') issues.push('short (' + Math.round(r.height) + 'px)');
        if (parseFloat(cs.fontSize) < 12) issues.push('small text ' + cs.fontSize);
      }
    }
    if (issues.length) found.push({el: name(el), where: where(el), issues});
  }
  for (const a of document.querySelectorAll('a[href]')) {
    const r = a.getBoundingClientRect(); if (r.width === 0 && r.height === 0) continue;
    const c = getComputedStyle(a).color;
    if (c === 'rgb(0, 0, 238)' || c === 'rgb(85, 26, 139)' || c === 'rgb(0, 0, 255)' || c === 'rgb(158, 158, 255)' || c === 'rgb(208, 173, 240)') found.push({el: 'a "' + a.textContent.trim().slice(0, 30) + '"', where: where(a), issues: ['default link colour']});
  }
  return found;
}"""

logging.getLogger("werkzeug").setLevel(logging.ERROR)
from werkzeug.serving import make_server  # noqa: E402
server = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_port

with sync_playwright() as pw:
    try:
        exe = os.environ.get("PW_CHROMIUM") or None
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        skip("no browser to drive (%s)" % str(exc).splitlines()[0][:120])
        done()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(BASE + "/login")
    found = [f for f in page.evaluate(AUDIT) if not set(f["issues"]) <= set(ALLOWED)]
    check(not found, "the sign-in page is styled" + ("" if not found else ": %s" % found[:3]))
    page.fill("input[name=username]", "owner")
    page.fill("input[name=password]", "a-long-password-1")
    page.click("form button")
    phone = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=page.context.storage_state()).new_page()
    for path in PAGES:
        url = path % ids
        page.goto(BASE + url)
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        found = [f for f in page.evaluate(AUDIT) if not set(f["issues"]) <= set(ALLOWED) and not f["el"].startswith('a ""')]
        check(not found, "%s: every control is styled%s" % (url, "" if not found else " — " + "; ".join(
            "%s (%s)" % (f["el"][:50], ", ".join(f["issues"])) for f in found[:4])))
        phone.goto(BASE + url)
        # what a person can actually do: try to scroll sideways, and look for content pushed past the edge
        moved = phone.evaluate("() => { window.scrollTo(2000, 0); const x = window.scrollX; window.scrollTo(0, 0); return x; }")
        cut = phone.evaluate("""() => [...document.querySelectorAll('main *')].filter(e => {
            const r = e.getBoundingClientRect();
            if (r.width === 0 || r.right <= innerWidth + 1) return false;
            for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
                const o = getComputedStyle(p).overflowX;
                if (o === 'auto' || o === 'scroll' || o === 'hidden') return false;
            }
            return true;
        }).map(e => e.tagName.toLowerCase() + '.' + String(e.className).split(' ')[0]).slice(0, 3)""")
        check(moved == 0 and not cut, "%s: nothing runs off the side on a phone%s" % (url, "" if moved == 0 and not cut else " (scrolls %dpx; cut off: %s)" % (moved, cut)))
    browser.close()
server.shutdown()
done()
