"""Pages stay quick with a full Pricing Sheet (Cooper's catalog), many jobs, clients and services: every main page answers
with a handful of database queries (not one per row), and the service form searches the Pricing Sheet instead of carrying
all of it. Browser half: the service form's item box finds a Pricing Sheet item as you type."""
import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
m.calculate_estimated_drive_time = lambda address: (30, 20)
from sqlalchemy import event  # noqa: E402

QUERIES = {"n": 0}
with app.app_context():
    event.listen(db.engine, "before_cursor_execute", lambda *a, **k: QUERIES.__setitem__("n", QUERIES["n"] + 1))
random.seed(4)
W = "led wafer breaker gfci receptacle wire romex box cover plate switch dimmer panel conduit fitting strap".split()
with app.app_context():
    items = [m.PriceItem(name=" ".join(random.sample(W, 3)) + " #%d" % i, lookup_key="k%d" % i, unit="ea", price=1 + i % 70,
                         category="Unconfirmed" if i > 1500 else "General", cooper_part_number="P%06d" % i) for i in range(5000)]
    items.append(m.PriceItem(name="Siemens 200A meter main combo", lookup_key="meter-main", unit="ea", price=389, cooper_part_number="MC2040B1200"))
    db.session.add_all(items); db.session.flush()
    for i in items[:400]:
        i.supplier_parts.append(m.SupplierPartNumber(supplier="Cooper Electric", part_number=i.cooper_part_number))
    for s in range(40):
        service = m.Service(name="Service %d" % s, category="Upgrades", estimated_labor_hours=4)
        for k in range(10):
            item = items[random.randrange(1500)]
            row = m.ServiceItem(name=item.name, quantity=2, unit="ea", unit_price=item.price, position=k)
            row.pricing_link = m.ServiceItemPricingLink(price_item=item)
            service.items.append(row)
        db.session.add(service)
    clients = [m.Client(first_name="F%d" % c, last_name="L%d" % c) for c in range(150)]
    db.session.add_all(clients); db.session.flush()
    for j, client in enumerate(clients):
        db.session.add(m.Job(client=client, title="Job %d" % j, job_number=2000 + j, description="Work", address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"))
    db.session.commit()
    service_id = m.Service.query.first().id; job_id = m.Job.query.first().id; client_id = clients[0].id
PAGES = ["/", "/quoting/pricing", "/quoting/services", "/quoting/services/%d" % service_id, "/quoting/services/%d/edit" % service_id, "/quoting/services/new",
         "/clients", "/clients/%d" % client_id, "/jobs", "/jobs/%d" % job_id, "/schedule", "/quoting/generate", "/quoting/generate/%d" % job_id,
         "/finance", "/finance/expenses", "/generators/sizing", "/generators/upgrade", "/settings", "/time-tracking"]
slow = []
for page in PAGES:
    owner.get(page)
    QUERIES["n"] = 0
    started = time.time()
    r = owner.get(page)
    took = time.time() - started
    if r.status_code != 200 or QUERIES["n"] > 40 or took > 1.5:
        slow.append("%s: %d queries, %.2fs (%d)" % (page, QUERIES["n"], took, r.status_code))
check(not slow, "every main page answers with a handful of queries" + ("" if not slow else ": " + "; ".join(slow)))
form = owner.get("/quoting/services/new").get_data(as_text=True)
check(len(form) < 200_000 and "pricing-catalog" not in form and "pricing-find" in form, "the service form no longer carries the whole Pricing Sheet (%d KB)" % (len(form) // 1024))
found = owner.get("/quoting/pricing/find.json?items=1&unpriced=1&q=meter+main").json["results"]
check(found and found[0]["name"] == "Siemens 200A meter main combo", "the service form's item box finds Pricing Sheet items")
with app.app_context():
    rate = m.get_labor_rate()
r = owner.post("/quoting/pricing/labor-rate", data=dict(CSRF, labor_rate="100"))
with app.app_context():
    check(m.get_labor_rate() == 100.0, "a new labor rate takes effect (the rate is remembered per page, not forever)")

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
    page.goto(base + "/quoting/services/new")
    box = page.locator(".item-name-input").first
    box.fill("meter main")
    pick = page.locator(".price-suggestion", has_text="Siemens 200A meter main combo")
    pick.wait_for(timeout=5000)
    pick.dispatch_event("mousedown")
    row = page.locator(".item-row").first
    check(row.locator(".item-price-input").input_value() == "389.00" and row.locator('input[name="item_price_id[]"]').input_value() != "",
          "typing in the item box finds the Pricing Sheet item and picking it fills its price")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
