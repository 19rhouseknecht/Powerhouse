"""The Generate Quote page stays quick with Cooper's whole catalog on the Pricing Sheet: Missing pricing offers the likely
matches and a search, never a menu of every item; a drive-time lookup that just failed is not tried again on every load.
Browser half: searching the Pricing Sheet in Missing pricing fills the item menu, and Use item prices the line."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
CALLS = []


def failing_drive(address):
    CALLS.append(address); raise RuntimeError("routing service timed out")


m.calculate_estimated_drive_time = failing_drive
words = "led wafer breaker gfci receptacle wire romex box cover plate switch dimmer panel conduit fitting strap connector".split()
with app.app_context():
    db.session.add_all([m.PriceItem(name="%s %s %s #%d" % (words[i % 17], words[(i * 7) % 17], words[(i * 3) % 17], i), lookup_key="k%d" % i, unit="ea",
                                    price=1 + i % 50, category="Unconfirmed" if i > 500 else "General", cooper_part_number="P%06d" % i) for i in range(6000)])
    db.session.add(m.PriceItem(name="RAB SLIM6 6in LED wafer 1200 lm", lookup_key="rabslim6", unit="ea", price=24, cooper_part_number="SLIM6"))
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen", job_number=880, description="Lights", address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543")
    db.session.add(job); db.session.commit(); job_id = job.id
url = "/quoting/generate/%d" % job_id
owner.get(url)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3; quote_id = q.id
    for t, tier in enumerate(m.quote_tiers(3)):
        option = m.QuoteOption(tier=tier, title="Option %d" % t, description="d", position=t); q.options.append(option)
        for j in range(8):
            option.lines.append(m.QuoteOptionService(service=None, service_name="%s widget %d" % (words[j], j) if j else "6in LED wafer light",
                                                     example_quantity="Pricing pending", multiplier=2, unit_price=0, material_unit_price=0,
                                                     labor_hours_per_unit=0, labor_rate_snapshot=90, position=j))
    db.session.commit()
calls_before = len(CALLS)
started = time.time()
page = owner.get(url).get_data(as_text=True)
took = time.time() - started
check(took < 5, "the Generate Quote page opens quickly with 6,000 Pricing Sheet items (%.2fs)" % took)
check(len(page) < 2_000_000 and "All pricing items" not in page, "it no longer lists every Pricing Sheet item in each Missing pricing menu (%d KB)" % (len(page) // 1024))
check("data-repair-find" in page and "RAB SLIM6 6in LED wafer 1200 lm" in page, "Missing pricing offers the likely matches and a search box")
owner.get(url)
check(len(CALLS) == calls_before, "a drive-time lookup that just failed is not tried again on every page load")
with app.app_context():
    job = db.session.get(m.Job, job_id); job.drive_checked_at -= m.timedelta(hours=m.DRIVE_RETRY_HOURS + 1); db.session.commit()
owner.get(url)
check(len(CALLS) == calls_before + 1, "it is tried again after %d hours" % m.DRIVE_RETRY_HOURS)
found = owner.get("/quoting/pricing/find.json?items=1&q=SLIM6").json["results"]
with app.app_context():
    group = [g for g in m.build_missing_quote_groups(db.session.get(m.Quote, quote_id)) if g["name"].startswith("6in LED wafer")][0]
r = owner.post("/quotes/%d/repair-missing" % quote_id, data=dict(CSRF, missing_key=group["key"], strategy="price_item", price_item_id=found[0]["id"], quantity="4"))
with app.app_context():
    priced = [line for option in db.session.get(m.Quote, quote_id).options for line in option.lines if line.price_item_id == int(found[0]["id"])]
    check(r.status_code == 302 and len(priced) == 3, "an item found by search prices the line in every tier")

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
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + url)
    card = page.locator(".missing-service-card").first
    card.locator("summary").click()
    box = card.locator("[data-repair-find]")
    box.fill("dimmer panel")
    pick = card.locator("[data-repair-pick]")
    page.wait_for_function("el => el.querySelector('[data-repair-results]') && el.querySelector('[data-repair-results]').children.length > 0", arg=pick.element_handle(), timeout=5000)
    check(pick.input_value() != "", "searching fills the item menu and picks the first result")
    box.press("Enter")
    check(page.url.endswith(url), "Enter in the search box doesn't send the form")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
