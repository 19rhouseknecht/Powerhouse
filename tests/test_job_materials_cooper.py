"""A job's material list: tick materials and Update from Cooper (the office listener reads their price and picture; prices are
reviewed, then the Pricing Sheet and this list use them). Sending the list to the Cooper cart confirms its Unconfirmed items.
Browser half: the ticks, Select all and the button's count."""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    person = m.Client(first_name="Dana", last_name="Smith"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Basement wiring", job_number=1042); db.session.add(job); db.session.flush()
    db.session.add(m.PriceItem(name="Existing breaker", lookup_key="existing", unit="ea", price=5, category="Breakers & panels"))
    wire = m.PriceItem(name="12/2 NM-B wire", lookup_key="122", unit="ft", price=0.52, cooper_part_number="122NMB250", category="Wire")
    box = m.PriceItem(name="Old work box", lookup_key="b114r", unit="ea", price=1.2, cooper_part_number="B114R", category="Unconfirmed",
                      notes="Added from Cooper's catalog Nov 1, 2026 · Cooper: breakers & PANELS")
    plate = m.PriceItem(name="Decora plate", lookup_key="plate", unit="ea", price=0.9, cooper_part_number="PJ26W", category="Unconfirmed",
                        notes="Added from Cooper's catalog Nov 1, 2026")
    loose = m.PriceItem(name="Customer tape", lookup_key="tape", unit="ea", price=3)
    db.session.add_all([wire, box, plate, loose]); db.session.flush()
    order = m.JobMaterialOrder(job=job)
    order.items.extend([m.JobMaterialOrderItem(price_item=wire, name=wire.name, quantity=90, unit="ft", unit_price=0.52, position=0),
                        m.JobMaterialOrderItem(price_item=box, name=box.name, quantity=4, unit="ea", unit_price=1.2, position=1),
                        m.JobMaterialOrderItem(price_item=plate, name=plate.name, quantity=4, unit="ea", unit_price=0.9, position=2),
                        m.JobMaterialOrderItem(price_item=loose, name=loose.name, quantity=1, unit="ea", unit_price=3, position=3)])
    db.session.add(order); db.session.commit()
    job_id = job.id
    rows = {item.name: item.id for item in order.items}
    sheet = {"wire": wire.id, "box": box.id}
client = sign_in(m)
url = "/jobs/%d/materials" % job_id

page = client.get(url).get_data(as_text=True)
check(page.count('data-tick ') + page.count('data-tick>') >= 4 and "Update from Cooper" in page and "Select all" in page, "every material has a tick box, with Select all and Update from Cooper")
check("Unconfirmed</span>" in page and "confirms 2 Unconfirmed items" in page, "Unconfirmed items are marked, and the cart button says it will confirm them")

r = client.post(url + "/update-from-cooper", data=CSRF)
with app.app_context():
    check(m.PriceSyncRun.query.count() == 0, "nothing ticked: nothing is sent")
r = client.post(url + "/update-from-cooper", data=dict(CSRF, order_item_id=[str(rows["Customer tape"])]))
with app.app_context():
    check(m.PriceSyncRun.query.count() == 0, "a material without a Cooper part number cannot be looked up")
r = client.post(url + "/update-from-cooper", data=dict(CSRF, order_item_id=[str(rows["12/2 NM-B wire"]), str(rows["Old work box"]), str(rows["Customer tape"])]))
with app.app_context():
    run = m.PriceSyncRun.query.one()
    o = db.session.get(m.Job, job_id).material_order
    check(r.status_code == 302 and sorted(run.item_ids) == sorted(sheet.values()) and run.mode != "cart" and o.update_batch_id == run.batch_id,
          "ticked materials go to the office listener as an Update from Cooper for just those Pricing Sheet items")
    batch_id = run.batch_id
check("Waiting for the office listener" in client.get(url).get_data(as_text=True), "the job page shows the update waiting")
r = client.post(url + "/update-from-cooper", data=dict(CSRF, order_item_id=[str(rows["12/2 NM-B wire"])]))
with app.app_context():
    check(m.PriceSyncRun.query.count() == 1, "a second one waits while the listener is busy")

with app.app_context():      # the listener finished: Cooper's prices are back
    run = m.PriceSyncRun.query.one(); run.status = "complete"
    db.session.add_all([m.PriceSyncRow(batch_id=batch_id, price_item_id=sheet["wire"], part_number="122NMB250", old_price=0.52, new_price=0.61, status="matched"),
                        m.PriceSyncRow(batch_id=batch_id, price_item_id=sheet["box"], part_number="B114R", old_price=1.2, new_price=1.35, status="matched")])
    db.session.commit()
    wire_row = m.PriceSyncRow.query.filter_by(price_item_id=sheet["wire"]).one().id
page = client.get(url).get_data(as_text=True)
check("Review the prices" in page, "when Cooper answers, the job page links to the review")
check(url in client.get("/quoting/pricing/cooper-sync/%d" % batch_id).get_data(as_text=True), "the review's Back goes to the job")
r = client.post("/quoting/pricing/cooper-sync/%d/apply" % batch_id, data=dict(CSRF, row_id=[str(wire_row)]))
with app.app_context():
    items = {i.name: i for i in db.session.get(m.Job, job_id).material_order.items}
    check(r.headers["Location"].endswith(url), "applying goes back to the job")
    check(db.session.get(m.PriceItem, sheet["wire"]).price == 0.61 and items["12/2 NM-B wire"].unit_price == 0.61, "an applied price updates the Pricing Sheet and the job's list")
    check(db.session.get(m.PriceItem, sheet["box"]).price == 1.2 and items["Old work box"].unit_price == 1.2, "a price not ticked in the review changes nothing")

with app.app_context():
    run = m.PriceSyncRun.query.one(); run.status = "complete"; db.session.commit()
r = client.post(url + "/add-to-cart", data=CSRF)
with app.app_context():
    box_now, plate_now = db.session.get(m.PriceItem, sheet["box"]), m.PriceItem.query.filter_by(name="Decora plate").one()
    check(box_now.category == "Breakers & panels" and "Confirmed when sent to the Cooper cart" in box_now.notes, "Add to the Cooper cart confirms an Unconfirmed item, in the sheet category matching Cooper's")
    check(plate_now.category == "General", "and one with no matching category goes in General")
    check(db.session.get(m.PriceItem, sheet["wire"]).category == "Wire", "normal items keep their category")
check("Unconfirmed</span>" not in client.get(url).get_data(as_text=True), "the list no longer shows them as Unconfirmed")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
app.config["TESTING"] = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)
with app.app_context():
    o = db.session.get(m.Job, job_id).material_order; o.update_batch_id = None
    for run in m.PriceSyncRun.query.all(): run.status = "complete"
    db.session.commit()
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page()
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + url)
    go = page.locator("[data-tick-go]")
    check(go.is_disabled(), "Update from Cooper waits for a tick")
    page.locator("#material-%d [data-tick]" % rows["Old work box"]).check()
    check(go.is_enabled() and go.inner_text() == "Update 1 from Cooper", "ticking one counts it on the button")
    page.check("[data-tick-all]")
    check(go.inner_text() == "Update 4 from Cooper", "Select all ticks every material")
    page.uncheck("[data-tick-all]"); page.locator("#material-%d [data-tick]" % rows["Old work box"]).check()
    go.click(); page.wait_for_load_state()
    check(page.locator("#cooper-update").count() == 1 and "Waiting for the office listener" in page.locator("#cooper-update").inner_text(), "pressing it sends the ticked one and shows it waiting")
    check(page.locator(".material-row").first.bounding_box()["height"] < 120, "the tick fits in the row")
    browser.close()
done()
