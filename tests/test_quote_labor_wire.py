"""Generate quote, 1.42.0: a count written in a name is charged (12x lights is 12), parts for every light follow the light
count (2x bulbs with 12 lights is 24), a lighting layout carries its wire (feet from the layout, from the Pricing Sheet's
14/2), items with 0 hr labor get Mike's suggested time to accept or edit (saved on the Pricing Sheet), and items shown with
no picture are sent to the office listener by themselves. Browser half: a picture arrives in place; Mike's time is accepted.
Nothing calls OpenAI."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
L = m.lighting

# 1. counts written in names
check(m.count_from_name("12x LED wafer light") == ("LED wafer light", 12.0) and m.count_from_name("LED wafer light x12") == ("LED wafer light", 12.0)
      and m.count_from_name("A19 bulbs (24)") == ("A19 bulbs", 24.0) and m.count_from_name("qty 4 dimmers") == ("dimmers", 4.0),
      "a count written in a name is read: 12x …, … x12, … (24), qty 4 …")
check(all(m.count_from_name(n)[1] is None for n in ("6in LED wafer 1200 lm", "14/2 NM-B wire", "20A breaker", "4ft LED panel", "1/2in EMT")),
      "sizes and ratings are never read as a count")

# 2. the wire a layout needs
kitchen = L.read_room({"type": "kitchen", "length": "14", "width": "12", "height": "9"})
plan = L.layout(1200, kitchen)
wire = L.wire_run(plan, kitchen)
check(wire["feet"] % 5 == 0 and wire["feet"] >= L.HOME_RUN_FT + wire["chain"] + wire["switch_leg"] and wire["chain"] >= (plan["count"] - 1) * min(plan["spacing"]) - 0.01,
      "the wire is the home run, the switch leg and light to light, with slack and waste, rounded up to 5 ft (%s ft for 9 lights)" % wire["feet"])
check(L.wire_run(L.layout(650, kitchen), kitchen)["feet"] > wire["feet"], "more lights take more wire")

with app.app_context():
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen lighting", job_number=881, address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    light = m.PriceItem(name="Halo 6in LED wafer 1200 lm", lookup_key="w6", category="Lighting", unit="ea", price=24, labor_hours_per_unit=0.5, image_path="img/logo-wordmark.png")
    romex = m.PriceItem(name="14/2 NM-B Romex wire", lookup_key="romex142", category="Wire", unit="ft", price=0.62, labor_hours_per_unit=0, cooper_part_number="142NMB")
    nuts = m.PriceItem(name="14/2 wire nuts", lookup_key="nuts", category="Wire", unit="box", price=6)
    bulb = m.PriceItem(name="LED A19 bulb", lookup_key="a19", category="Lighting", unit="ea", price=3, labor_hours_per_unit=0, cooper_part_number="A19LED")
    db.session.add_all([light, romex, nuts, bulb]); db.session.commit()
    job_id = job.id; ids = {"light": light.id, "romex": romex.id, "bulb": bulb.id}
m.calculate_estimated_drive_time = lambda a: (20, 10)
owner = sign_in(m)
url = "/quoting/generate/%d" % job_id
owner.get(url)

with app.app_context():
    check(m.lighting_wire_item().id == ids["romex"], "the wire comes from the Pricing Sheet's 14/2 by the foot (never wire nuts)")
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 1
    option = m.QuoteOption(tier="Good", title="Lights", description="", position=0); q.options.append(option)
    S = m.QuoteOptionService
    option.lines.append(S(price_item_id=ids["light"], service_name="Halo 6in LED wafer 1200 lm", example_quantity="1 ea", multiplier=12, unit_price=24, material_unit_price=24, labor_hours_per_unit=0.5, labor_rate_snapshot=90, position=0))
    option.lines.append(S(service_name="2x LED A19 bulbs", example_quantity="Pricing pending", multiplier=1, unit_price=0, position=1))
    option.lines.append(S(service_name="12x wafer mounting brackets", example_quantity="Pricing pending", multiplier=1, unit_price=0, position=2))
    notes = m.fix_written_counts(option)
    bulbs, brackets = option.lines[1], option.lines[2]
    check(bulbs.multiplier == 24 and brackets.multiplier == 12 and brackets.service_name == "wafer mounting brackets",
          "a count in a name is charged: 2x bulbs with 12 lights is 24, 12x brackets is 12 (and the name loses the 12x)")
    check(any("24" in n for n in notes), "and it says so")
    bulbs.multiplier = 12; bulbs.service_name = "LED A19 bulbs, 2 per fixture"
    m.per_light_parts(option, option.lines[0], 12)
    check(bulbs.multiplier == 24, "in a lighting layout, parts for every light follow the count (2 per fixture × 12 lights = 24)")
    check(all(line.total == line.multiplier * line.unit_price for line in option.lines if line.material_unit_price is None), "every line is charged for its quantity")
    notes = m.wire_for_lighting(option, 110, 90)
    wire_line = option.lines[-1]
    check(wire_line.price_item.id == ids["romex"] and wire_line.multiplier == 110 and abs(wire_line.material_total - 110 * 0.62) < 1e-9 and "110 ft" in notes[0],
          "the lights get 110 ft of the Pricing Sheet's 14/2, charged by the foot")
    check(m.wire_for_lighting(option, 110, 90) == [] and len([l for l in option.lines if l.price_item and l.price_item.id == ids["romex"]]) == 1, "never a second wire line")
    m.wire_for_lighting(option, 140, 90)
    check(wire_line.multiplier == 140, "a wire line already there takes the layout's footage")
    db.session.commit(); quote_id = q.id
    db.session.get(m.PriceItem, ids["bulb"]).id
    option.lines[1].price_item_id = ids["bulb"]; option.lines[1].unit_price = 3; option.lines[1].material_unit_price = 3; option.lines[1].labor_rate_snapshot = 90
    db.session.commit()

# 3. the page: each line's math, No labor time yet, pictures asked for by themselves
page = owner.get(url).get_data(as_text=True)
check("24 × $3.00 / ea" in page and "140 × $0.62 / ft" in page, "each line shows its math: quantity × unit price")
labor = page.split('id="labor-check"')[1].split("</section>")[0] if 'id="labor-check"' in page else ""
check("14/2 NM-B Romex wire" in labor and "LED A19 bulb" in labor and "Halo 6in" not in labor, "No labor time yet lists the items with 0 hr (not the ones that have a time)")
with app.app_context():
    runs = m.PriceSyncRun.query.filter_by(mode="pictures").all()
    check(len(runs) == 1 and sorted(runs[0].item_ids) == sorted([ids["romex"], ids["bulb"]]) and runs[0].status == "queued",
          "items shown with no picture are sent to the office listener by themselves (just those)")
    check(all(db.session.get(m.PriceItem, i).picture_asked_at for i in (ids["romex"], ids["bulb"])), "and marked as asked")
check('data-pic-for="%d"' % ids["romex"] in page and "PHWatchPictures" in page, "the page watches for them")
owner.get(url)
with app.app_context():
    check(m.PriceSyncRun.query.filter_by(mode="pictures").count() == 1, "opening the page again asks nothing twice")
pics = owner.get("/quoting/pricing/pictures.json?ids=%d,%d" % (ids["light"], ids["romex"])).json["pictures"]
check(pics[str(ids["light"])].endswith("logo-wordmark.png") and pics[str(ids["romex"])] == "", "pictures.json says which have a picture now")

# a person's Update from Cooper goes first
r = owner.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="selected", item_ids=str(ids["bulb"])), headers={"Accept": "application/json"})
with app.app_context():
    check(r.status_code == 200 and m.PriceSyncRun.query.filter_by(mode="pictures").first().status == "cancelled"
          and db.session.get(m.PriceItem, ids["romex"]).picture_asked_at is None, "Update from Cooper replaces a waiting pictures run (those are asked for again later)")
    for run in m.PriceSyncRun.query.filter(m.PriceSyncRun.status.in_(("queued", "running"))).all():
        run.status = "complete"
    db.session.commit()

# 4. Mike's labor times
calls = []
real_mike = m.mike_labor_suggestions
m.mike_labor_suggestions = lambda items: calls.append([i.id for i in items]) or {ids["romex"]: {"hours": 0.02, "reason": "Labor sheet: NM cable 2 hr per 100 ft."}}
r = owner.post("/quotes/%d/labor-suggestions" % quote_id, data=CSRF, headers={"Accept": "application/json"})
check(r.status_code == 200 and r.json["suggestions"][str(ids["romex"])]["hours"] == 0.02 and sorted(calls[0]) == sorted([ids["romex"], ids["bulb"]]),
      "Mike suggests a time for each item with none")
owner.post("/quotes/%d/labor-suggestions" % quote_id, data=CSRF, headers={"Accept": "application/json"})
check(len(calls) == 2 and calls[1] == [ids["bulb"]], "asked once per item: only the one he had no answer for is asked again")
page = owner.get(url).get_data(as_text=True)
check('value="0.02"' in page and "NM cable 2 hr per 100 ft" in page and ">Accept<" in page, "his time and why are filled in, ready to Accept")
r = owner.post("/quotes/%d/labor-time" % quote_id, data=dict(CSRF, price_item_id=ids["romex"], hours="0.025"))
with app.app_context():
    item = db.session.get(m.PriceItem, ids["romex"])
    line = next(l for o in db.session.get(m.Quote, quote_id).options for l in o.lines if l.price_item_id == ids["romex"])
    check(r.status_code == 302 and item.labor_hours_per_unit == 0.025, "an edited time is saved on the Pricing Sheet item, so the next quote has it")
    check(line.labor_hours_per_unit == 0.025 and abs(line.labor_total - 140 * 0.025 * 90) < 1e-6 and abs(line.unit_price - (0.62 + 0.025 * 90)) < 1e-9,
          "and this quote's line is charged for it")
page = owner.get(url).get_data(as_text=True)
check("14/2 NM-B Romex wire" not in page.split('id="labor-check"')[1].split("</section>")[0], "it leaves the No labor time yet list")
r = owner.post("/quotes/%d/labor-time" % quote_id, data=dict(CSRF, price_item_id=ids["bulb"], hours="-1"))
with app.app_context():
    check(db.session.get(m.PriceItem, ids["bulb"]).labor_hours_per_unit == 0, "a time below 0 is refused")
m.mike_labor_suggestions = real_mike
os.environ.pop("OPENAI_API_KEY", None)
with app.app_context():
    db.session.get(m.Quote, quote_id).labor_suggest_json = ""; db.session.commit()
r = owner.post("/quotes/%d/labor-suggestions" % quote_id, data=CSRF, headers={"Accept": "application/json"})
check(r.status_code == 503 and "type a time" in r.json["error"], "without the AI connection it says to type a time")

# 5. browser: a picture arrives in place; Mike's time is accepted
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import threading  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
m.mike_labor_suggestions = lambda items: {i.id: {"hours": 0.3, "reason": "Like the other bulbs."} for i in items}
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
with app.app_context():
    db.session.get(m.PriceItem, ids["romex"]).picture_asked_at = None; db.session.commit()
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
    row = page.locator('[data-labor-row="%d"]' % ids["bulb"])
    page.wait_for_function("document.querySelector('[data-labor-row=\"%d\"] [data-labor-hours]').value !== ''" % ids["bulb"], timeout=8000)
    check(row.locator("[data-labor-hours]").input_value() == "0.3" and "Like the other bulbs" in row.inner_text() and row.locator("[data-labor-go]").inner_text() == "Accept",
          "Mike's time fills in on the page, with why, and the button says Accept")
    with app.app_context():
        run = m.PriceSyncRun.query.filter_by(mode="pictures", status="queued").first()
        check(run is not None and ids["romex"] in run.item_ids, "the page asked for the wire's picture")
        db.session.get(m.PriceItem, ids["romex"]).image_path = "img/logo-wordmark.png"      # the listener saved it
        run.status = "complete"; db.session.commit()
    page.evaluate("window.notReloaded = true")
    try:
        page.wait_for_function("!document.querySelector('[data-pic-for=\"%d\"]')" % ids["romex"], timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    check(page.locator('[data-pic-for="%d"]' % ids["romex"]).count() == 0 and page.evaluate("window.notReloaded === true"), "the picture arrives in place, no reload")
    row.locator("[data-labor-go]").click()
    page.wait_for_load_state()
    page.locator("#labor-check, .draft-option-list").first.wait_for(timeout=8000)
    with app.app_context():
        check(db.session.get(m.PriceItem, ids["bulb"]).labor_hours_per_unit == 0.3, "Accept saves Mike's time on the Pricing Sheet")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
