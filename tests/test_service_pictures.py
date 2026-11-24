"""Building a service: every option in the item list shows its picture, the chosen item shows its picture beside it, and
items without one are sent to the office Cooper listener by themselves (the picture fills in on the page when it is saved).
Browser half: pick from the list with pictures; a missing picture arrives in place. Nothing calls Cooper."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    pictured = m.PriceItem(name="Halo 6in LED wafer light", lookup_key="halo", unit="ea", price=24, cooper_part_number="HLB6", image_path="img/logo-wordmark.png")
    bare = m.PriceItem(name="RAB 6in LED wafer light", lookup_key="rab", unit="ea", price=19, cooper_part_number="SLIM6")
    nopart = m.PriceItem(name="Generic 6in LED wafer light", lookup_key="gen", unit="ea", price=12)
    db.session.add_all([pictured, bare, nopart]); db.session.flush()
    service = m.Service(name="Recessed light install", category="Lighting", example_quantity="1 light", description="")
    db.session.add(service); db.session.flush()
    for item in (pictured, bare):
        row = m.ServiceItem(service_id=service.id, name=item.name, quantity=1, unit=item.unit, unit_price=item.price)
        row.pricing_link = m.ServiceItemPricingLink(price_item=item)
        db.session.add(row)
    db.session.commit()
    ids = {"pictured": pictured.id, "bare": bare.id, "nopart": nopart.id}; service_id = service.id
owner = sign_in(m)

found = owner.get("/quoting/pricing/find.json?items=1&unpriced=1&q=wafer").json
by = {r["id"]: r for r in found["results"]}
check(by[str(ids["pictured"])]["image"].endswith("logo-wordmark.png") and by[str(ids["bare"])]["image"] == "", "the item list says which items have a picture")
with app.app_context():
    run = m.PriceSyncRun.query.filter_by(mode="pictures").first()
    check(found["picture_watch"] and run is not None and run.item_ids == [ids["bare"]],
          "items it shows without a picture (with a Cooper part number) are asked for from Cooper by themselves")
    run.status = "complete"; db.session.commit()

page = owner.get("/quoting/services/%d/edit" % service_id).get_data(as_text=True)
check('class="line-thumb" src="/static/img/logo-wordmark.png' in page and 'data-pic-for="%d"' % ids["bare"] in page,
      "editing a service shows each material's picture beside it, or a placeholder that fills in")
new = owner.get("/quoting/services/new").get_data(as_text=True)
check("data-item-pic" in new and "Pick an item to see its picture" in new, "a new service's item rows have a picture spot")
detail = owner.get("/quoting/services/%d" % service_id).get_data(as_text=True)
check('data-pic-for="%d"' % ids["bare"] in detail, "the service page shows the placeholder that fills in")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import threading  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
with app.app_context():
    db.session.get(m.PriceItem, ids["bare"]).picture_asked_at = None; db.session.commit()
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
    row = page.locator("#items-rows .item-row").first
    row.locator(".item-name-input").fill("wafer")
    row.locator(".price-suggestions.open .price-suggestion").first.wait_for(timeout=5000)
    options = row.locator(".price-suggestions .price-suggestion")
    check(options.count() == 3 and row.locator(".price-suggestions img.pick-thumb").count() == 1 and row.locator(".price-suggestions .pick-thumb-none").count() == 2,
          "every option in the list shows its picture, or a placeholder")
    row.locator(".price-suggestion", has_text="Halo").dispatch_event("mousedown")
    check(row.locator("[data-item-pic] img.line-thumb").count() == 1, "the chosen item's picture shows beside it")
    row.locator(".item-name-input").fill("RAB wafer")
    row.locator(".price-suggestions.open .price-suggestion").first.wait_for(timeout=5000)
    row.locator(".price-suggestion", has_text="RAB").dispatch_event("mousedown")
    check(row.locator('[data-item-pic] [data-pic-for="%d"]' % ids["bare"]).count() == 1, "an item without one shows a placeholder waiting for Cooper")
    with app.app_context():
        run = m.PriceSyncRun.query.filter_by(mode="pictures", status="queued").first()
        check(run is not None and ids["bare"] in run.item_ids, "and Cooper was asked for it")
        db.session.get(m.PriceItem, ids["bare"]).image_path = "img/logo-wordmark.png"       # the listener saved it
        run.status = "complete"; db.session.commit()
    try:
        page.wait_for_function("!document.querySelector('[data-item-pic] [data-pic-for]')", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    check(row.locator("[data-item-pic] img.line-thumb").count() == 1, "the picture arrives beside the item by itself")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
