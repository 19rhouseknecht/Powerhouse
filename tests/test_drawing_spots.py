"""A service's drawing with its parts marked on it: Mark parts on the drawing places marks for each part, and clicking a
part in the materials list highlights every spot it is on the drawing. Marks survive editing the service.
Browser half: marking, removing a mark, saving, and the highlight."""
import base64
import io
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
with app.app_context():
    meter = m.PriceItem(name="200A meter base", lookup_key="meterbase", unit="ea", price=120, cooper_part_number="U4101")
    db.session.add(meter); db.session.commit(); meter_id = meter.id
form = dict(CSRF, name="200A Service Upgrade", category="Upgrades", example_quantity="1", estimated_labor_hours="8", description="",
            **{"item_name[]": ["200A meter base", "Locknut 2in"], "item_quantity[]": ["1", "6"], "item_unit[]": ["ea", "ea"], "item_price[]": ["120", "0.5"],
               "item_price_id[]": [str(meter_id), ""], "item_cooper_part_number[]": ["U4101", ""]},
            drawing=(io.BytesIO(PNG), "service.png"))
owner.post("/quoting/services/new", data=form, content_type="multipart/form-data")
with app.app_context():
    service = m.Service.query.one()
    keys = [item.spot_key for item in service.items]
    service_id = service.id
check(keys[0] == "p%d" % meter_id and keys[1].startswith("p") and keys[0] != keys[1], "a part's marks are kept under its Pricing Sheet item")
with app.app_context():
    loose = m.ServiceItem(name="Locknut, 2 in.", quantity=1, unit="ea", unit_price=0)
    check(loose.spot_key == "n" + m.canonical_item_name("Locknut, 2 in."), "or, for a part with no Pricing Sheet item, under its name")
url = "/quoting/services/%d" % service_id
page = owner.get(url).get_data(as_text=True)
check("data-open-marker" in page and 'class="spot-row"' in page and "drawing-spots.js" in page, "a service with a picture drawing offers Mark parts on the drawing, and its rows can be clicked")
spots = {keys[0]: [{"x": 10, "y": 20}, {"x": 150, "y": 5}, {"x": "x"}], keys[1]: [{"x": 50, "y": 50}], "p999999": [{"x": 1, "y": 1}]}
r = owner.post(url + "/drawing-spots", data=dict(CSRF, spots=json.dumps(spots)))
check(r.json["ok"] and r.json["spots"] == {keys[0]: [{"x": 10.0, "y": 20.0}], keys[1]: [{"x": 50.0, "y": 50.0}]},
      "marks are saved for the service's own parts, inside the picture only")
page = owner.get(url).get_data(as_text=True)
check(page.count("◎ 1") == 2, "each marked part shows how many spots it has")
edit = dict(form); edit.pop("drawing"); edit["item_quantity[]"] = ["2", "8"]
owner.post(url + "/edit", data=edit, content_type="multipart/form-data")
with app.app_context():
    service = db.session.get(m.Service, service_id)
    check(service.drawing_spots.get(keys[0]) == [{"x": 10.0, "y": 20.0}] and [i.spot_key for i in service.items] == keys, "editing the service keeps its marks")
check(owner.post(url + "/drawing-spots", data=dict(CSRF, spots="not json")).status_code == 400, "unreadable marks are refused")
with app.app_context():
    service = db.session.get(m.Service, service_id); service.drawing_filename = "plan.pdf"; db.session.commit()
check(owner.post(url + "/drawing-spots", data=dict(CSRF, spots="{}")).status_code == 400 and "data-open-marker" not in owner.get(url).get_data(as_text=True),
      "a PDF drawing can't be marked (it isn't a picture)")
with app.app_context():
    service = db.session.get(m.Service, service_id); service.drawing_filename = [f for f in os.listdir(os.path.join(app.static_folder, "uploads", "services")) if f.endswith("service.png")][0]; db.session.commit()

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
    page.locator(".spot-row").nth(0).click()
    check(page.locator("[data-drawing-marks] .drawing-mark").count() == 1 and "1 spot" in page.locator("[data-drawing-hint]").inner_text(), "clicking a part shows its spot on the drawing")
    page.locator(".spot-row").nth(0).click()
    check(page.locator("[data-drawing-marks] .drawing-mark").count() == 0, "clicking it again hides it")
    page.click("[data-open-marker]")
    dialog = page.locator("#marker-dialog")
    check(dialog.is_visible() and dialog.locator(".drawing-mark").count() == 2, "the marking window opens with the marks already placed")
    dialog.locator("[data-part]").nth(1).click()
    stage = dialog.locator("[data-marker-stage]")
    box = stage.bounding_box()
    stage.click(position={"x": box["width"] * 0.25, "y": box["height"] * 0.75})
    stage.click(position={"x": box["width"] * 0.75, "y": box["height"] * 0.25})
    check(dialog.locator("[data-part]").nth(1).locator("em").inner_text() == "3", "clicking the drawing adds marks for the picked part")
    dialog.locator(".drawing-mark").nth(1).click()
    check(dialog.locator("[data-part]").nth(1).locator("em").inner_text() == "2", "clicking a mark takes it off")
    dialog.locator("[data-marker-save]").click()
    page.wait_for_function("!document.getElementById('marker-dialog').open", timeout=5000)
    with app.app_context():
        saved = db.session.get(m.Service, service_id).drawing_spots
        check(len(saved[keys[1]]) == 2 and any(abs(mk["x"] - 75) < 2 and abs(mk["y"] - 25) < 2 for mk in saved[keys[1]]), "Save keeps them where they were clicked")
    page.locator(".spot-row").nth(1).click()
    check(page.locator("[data-drawing-marks] .drawing-mark.is-pulsing").count() == 2 and "◎ 2" in page.locator(".spot-row").nth(1).inner_text(),
          "the part now shows both spots, and its count")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
