"""Use this confirms an Unconfirmed item; Settings -> users saves in place (no jump to the top; a mistake marks its field and
keeps what was typed); a service's materials show their pictures, and admins can tick them for Update from Cooper.
Browser half: all three in a real browser."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
FETCH = {"X-Requested-With": "fetch"}
m.calculate_estimated_drive_time = lambda a: (20, 10)

# 1. Use this on an Unconfirmed item confirms it
with app.app_context():
    db.session.add(m.PriceItem(name="12/2 Romex 250ft", lookup_key="romex", unit="roll", price=120, category="Wire"))
    loose = m.PriceItem(name="12/2 NM-B 250 ft coil", lookup_key="nmb", unit="roll", price=118, category="Unconfirmed", cooper_part_number="122NMB250",
                        notes="Added from Cooper's catalog Nov 1, 2026 · Cooper: WIRE")
    db.session.add(loose)
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen", job_number=880, address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    db.session.commit(); job_id, loose_id = job.id, loose.id
owner.get("/quoting/generate/%d" % job_id)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3
    for t, tier in enumerate(m.quote_tiers(3)):
        o = m.QuoteOption(tier=tier, title="O%d" % t, description="", position=t); q.options.append(o)
        o.lines.append(m.QuoteOptionService(service=None, service_name="12/2 wire coil", example_quantity="Pricing pending", multiplier=1, unit_price=0,
                                            material_unit_price=0, labor_hours_per_unit=0, labor_rate_snapshot=90, position=0))
    db.session.commit(); quote_id = q.id
key = m.normalize_item_name("12/2 wire coil")
owner.post("/quotes/%d/use-sheet-item" % quote_id, data=dict(CSRF, pick="0:%d" % loose_id, key_0=key))
with app.app_context():
    item = db.session.get(m.PriceItem, loose_id)
    check(item.category == "Wire" and "Confirmed when used on a quote" in item.notes, "Use this on an Unconfirmed item makes it a normal Pricing Sheet item, in the matching category (Wire)")
    check(all(not line.is_missing for o in db.session.get(m.Quote, quote_id).options for line in o.lines), "and prices the line")

# 2. Settings -> users
r = owner.post("/settings/add-user", data=dict(CSRF, username="newhire", password="a-long-password-1", confirm="a-long-password-2", hourly_rate="20"), headers=FETCH)
check(r.status_code == 400 and r.json == {"ok": False, "error": "Passwords didn't match.", "field": "confirm"}, "passwords that don't match: the Confirm field, and why")
r = owner.post("/settings/add-user", data=dict(CSRF, username="ab", password="a-long-password-1", confirm="a-long-password-1"), headers=FETCH)
check(r.json["field"] == "username", "a short username: the Username field")
with app.app_context():
    check(m.User.query.filter_by(username="newhire").first() is None, "nothing is made until it's right")
r = owner.post("/settings/add-user", data=dict(CSRF, username="newhire", password="a-long-password-1", confirm="a-long-password-1", hourly_rate="20"), headers=FETCH)
check(r.json == {"ok": True, "message": 'Added "newhire".', "reload": True}, "adding works")
with app.app_context():
    hire_id = m.User.query.filter_by(username="newhire").one().id
r = owner.post("/settings/users/%d" % hire_id, data=dict(CSRF, hourly_rate="27.5"), headers=FETCH)
with app.app_context():
    check(r.json["ok"] and db.session.get(m.User, hire_id).hourly_rate == 27.5, "saving a user answers in place")
check(owner.post("/settings/users/%d" % hire_id, data=dict(CSRF, hourly_rate="-3"), headers=FETCH).json["field"] == "hourly_rate", "a bad rate marks the rate")
r = owner.post("/settings/reset-password/%d" % hire_id, data=dict(CSRF, password="a-long-password-9", confirm="nope-nope-nope"), headers=FETCH)
check(r.json["field"] == "confirm", "a password reset that doesn't match marks Confirm")
r = owner.post("/settings/users/%d" % hire_id, data=dict(CSRF, hourly_rate="30"))
check(r.headers["Location"].endswith("#user-row-%d" % hire_id), "without the page's script, it comes back to that user's row")

# 3. a service's materials: pictures and ticks
with app.app_context():
    pictured = m.PriceItem(name="200A panel", lookup_key="panel200", unit="ea", price=300, cooper_part_number="HOM3060", image_path="img/logo-wordmark.png")
    db.session.add(pictured); db.session.flush()
    service = m.Service(name="Panel swap", category="Upgrades", estimated_labor_hours=8)
    row = m.ServiceItem(name="200A panel", quantity=1, unit="ea", unit_price=300, position=0); row.pricing_link = m.ServiceItemPricingLink(price_item=pictured)
    service.items.append(row)
    service.items.append(m.ServiceItem(name="Customer's own breaker", quantity=1, unit="ea", unit_price=0, position=1))
    db.session.add(service); db.session.commit(); service_id, panel_id = service.id, pictured.id
page = owner.get("/quoting/services/%d" % service_id).get_data(as_text=True)
check('src="/static/img/logo-wordmark.png' in page and "line-thumb-none" in page, "a service's materials show their pictures (or that they have none)")
check(page.count("data-svc-tick ") + page.count("data-svc-tick>") == 1 or page.count('data-svc-tick aria-label') == 1, "a tick for each material with a Cooper part number")
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_services = True; db.session.commit()
check("data-svc-tick" not in crew.get("/quoting/services/%d" % service_id).get_data(as_text=True), "ticks and Update from Cooper are for admins")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
app.config["TESTING"] = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 1300, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/settings")
    page.evaluate("window.notReloaded = true")
    row = page.locator("#user-row-%d" % hire_id)
    row.scroll_into_view_if_needed()
    y = page.evaluate("window.scrollY")
    row.locator("input[name=hourly_rate]").fill("31")
    row.locator("button", has_text="Save").click()
    page.locator('[data-said-for="user-%d"]' % hire_id, has_text="Saved").wait_for(timeout=5000)
    check(page.evaluate("window.notReloaded === true") and abs(page.evaluate("window.scrollY") - y) < 5, "saving a user doesn't reload or jump to the top")
    page.click("#add-user summary")
    form = page.locator("#add-user-form")
    form.locator("input[name=username]").fill("second")
    form.locator("input[name=password]").fill("a-long-password-1")
    form.locator("input[name=confirm]").fill("something-else-1")
    form.locator("button", has_text="Add user").click()
    form.locator(".field-error-note").wait_for(timeout=5000)
    confirm = form.locator("input[name=confirm]")
    check("field-error" in confirm.get_attribute("class") and "didn't match" in form.locator(".field-error-note").inner_text(), "mismatched passwords: Confirm turns red and says why")
    check(form.locator("input[name=username]").input_value() == "second" and page.evaluate("window.notReloaded === true"), "nothing typed is cleared, and the page doesn't reload")
    confirm.fill("a-long-password-1")
    check(form.locator(".field-error-note").count() == 0, "fixing it clears the red")
    y = page.evaluate("window.scrollY")
    form.locator("button", has_text="Add user").click()
    page.wait_for_function("window.notReloaded !== true", timeout=8000)
    page.wait_for_load_state()
    check("second" in page.content() and abs(page.evaluate("window.scrollY") - y) < 80, "adding a user reloads to the same place (%d vs %d)" % (page.evaluate("window.scrollY"), y))
    page.goto(base + "/quoting/services/%d" % service_id)
    page.locator("[data-svc-tick]").check()
    page.click("[data-svc-cooper]")
    page.locator("[data-svc-said]", has_text="the page updates by itself").wait_for(timeout=8000)
    with app.app_context():
        run = m.PriceSyncRun.query.order_by(m.PriceSyncRun.id.desc()).first()
        check(run.item_ids == [panel_id], "ticking a material and pressing Update from Cooper sends it to the office listener")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
