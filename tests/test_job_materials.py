"""A job's material order (after the client picks a quote option): it shows every material that will be ordered (with its
picture, Cooper part number and whether it goes in the Cooper cart), quantities change as they are typed, Remove takes an
item off at once, and more can be added from the Pricing Sheet. Browser half: the same in a real browser."""
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
    wire = m.PriceItem(name="12/2 NM-B wire", lookup_key="122", unit="ft", price=0.52, cooper_part_number="122NMB250", image_path="img/logo-wordmark.png")
    breaker = m.PriceItem(name="20A breaker", lookup_key="qo120", unit="ea", price=9, cooper_part_number="QO120")
    box = m.PriceItem(name="Single gang old work box", lookup_key="b114r", unit="ea", price=1.2, cooper_part_number="B114R")
    db.session.add_all([wire, breaker, box]); db.session.flush()
    order = m.JobMaterialOrder(job=job)
    order.items.extend([m.JobMaterialOrderItem(price_item=wire, name=wire.name, quantity=90, unit="ft", unit_price=0.52, source_services="Basement circuits", position=0),
                        m.JobMaterialOrderItem(price_item=breaker, name=breaker.name, quantity=3, unit="ea", unit_price=9, position=1),
                        m.JobMaterialOrderItem(name="Customer-supplied fixture", quantity=2, unit="ea", unit_price=0, position=2)])
    db.session.add(order); db.session.commit()
    job_id, ids = job.id, {"wire": wire.id, "breaker": breaker.id, "box": box.id}
    rows = {item.name: item.id for item in order.items}
client = sign_in(m)
url = "/jobs/%d/materials" % job_id

page = client.get(url).get_data(as_text=True)
check("What will be ordered" in page and "3 materials" in page and page.count('class="material-row') == 3, "the page lists every material that will be ordered")
check("Cooper #122NMB250 · goes in the Cooper cart" in page and "Not in the Cooper cart: no Cooper part number" in page, "and says which go in the Cooper cart")
check("img/logo-wordmark.png" in page and "for Basement circuits" in page, "with the material's picture and what it is for")
check("datalist" not in page and "material-catalog-data" not in page, "the page no longer loads the whole Pricing Sheet")

r = client.post("/jobs/%d/materials/items/%d/quantity" % (job_id, rows["12/2 NM-B wire"]), data=dict(CSRF, quantity="120"), headers={"X-Requested-With": "fetch"})
check(r.json["ok"] and r.json["quantity"] == 120 and r.json["total"] == 62.4 and r.json["order_total"] == round(62.4 + 27, 2), "a quantity typed saves, with the new totals")
check(client.post("/jobs/%d/materials/items/%d/quantity" % (job_id, rows["20A breaker"]), data=dict(CSRF, quantity="0"), headers={"X-Requested-With": "fetch"}).status_code == 400,
      "a quantity of 0 is refused (Remove takes an item off)")
r = client.post("/jobs/%d/materials/items/%d/remove" % (job_id, rows["Customer-supplied fixture"]), data=CSRF)
with app.app_context():
    names = [item.name for item in db.session.get(m.Job, job_id).material_order.items]
check(r.status_code == 302 and names == ["12/2 NM-B wire", "20A breaker"], "Remove takes the item off at once")

found = client.get("/quoting/pricing/find.json?items=1&q=old+work").json["results"]
check(len(found) == 1 and found[0]["id"] == str(ids["box"]) and found[0]["part"] == "B114R", "the search finds Pricing Sheet items (with their Cooper part number)")
client.post("/jobs/%d/materials/add" % job_id, data=dict(CSRF, price_item_id=ids["box"], name="x", quantity="8", unit_price="0"))
client.post("/jobs/%d/materials/add" % job_id, data=dict(CSRF, price_item_id=ids["breaker"], name="x", quantity="2", unit_price="0"))
with app.app_context():
    items = {item.name: item for item in db.session.get(m.Job, job_id).material_order.items}
    check(items["Single gang old work box"].quantity == 8 and items["Single gang old work box"].unit_price == 1.2 and items["Single gang old work box"].part_number == "",
          "adding from the Pricing Sheet brings its price (and its Cooper part number comes from the item)")
    check(items["20A breaker"].quantity == 5 and len(items) == 3, "adding something already on the list adds to its quantity, no second line")
    lines, left = m.cooper_cart_lines(db.session.get(m.Job, job_id).material_order)
    check(sorted(line["part_number"] for line in lines) == ["122NMB250", "B114R", "QO120"] and not left, "all three go in the Cooper cart")
with app.app_context():
    o = db.session.get(m.Job, job_id).material_order; o.cart_status = "saved"; o.cart_name = "Smith-MAT-1042"; db.session.commit()
client.post("/jobs/%d/materials/items/%d/remove" % (job_id, rows["20A breaker"]), data=CSRF)
check("The list changed after it went to the Cooper cart" in client.get(url).get_data(as_text=True), "changing the list after it went to the cart says to send it again")
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_quoting = False; role.view_jobs = True; db.session.commit()
check(crew.get("/quoting/pricing/find.json?q=breaker").json["results"] and all(r["kind"].startswith("Pricing Sheet") for r in crew.get("/quoting/pricing/find.json?q=breaker").json["results"]),
      "someone with Jobs but not Quoting can search the Pricing Sheet to add materials")

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
    page = browser.new_page()
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + url)
    row = page.locator(".material-row", has_text="12/2 NM-B wire")
    row.locator('input[name="quantity"]').fill("200")
    row.locator("[data-qty-status]", has_text="Saved").wait_for(timeout=5000)
    check(row.locator("[data-row-total]").inner_text() == "$104.00", "typing a quantity saves it and updates the line's total")
    check(page.locator(".material-order-total [data-order-total]").inner_text() == "$113.60", "and the order's total at the top")
    page.fill("[data-material-find]", "20A")
    pick = page.locator(".material-pick", has_text="20A breaker")
    pick.locator('input[name="quantity"]').fill("4")
    pick.locator("button", has_text="Add").click()
    page.wait_for_load_state()
    check(page.locator(".material-row", has_text="20A breaker").count() == 1 and "Added 4 ea" in page.content(), "searching and pressing Add puts it on the list")
    page.locator(".material-row", has_text="Single gang old work box").locator("button", has_text="Remove").click()
    page.wait_for_load_state()
    check(page.locator(".material-row", has_text="Single gang old work box").count() == 0, "Remove takes it off")
    browser.close()
done()
