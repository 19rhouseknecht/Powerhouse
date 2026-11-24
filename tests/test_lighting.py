"""Lighting quotes: the lumen method gives how many of each Pricing Sheet light a room needs and where they go; Caden picks
one light per option and its count is the layout's; each option keeps a 3D layout; material pictures show on the draft and
the presentation. Browser half: the 3D view draws and turns. Nothing calls OpenAI."""
import json
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
L = m.lighting

# 1. the math
kitchen = L.read_room({"type": "kitchen", "length": "14", "width": "12", "height": "9"})
check(kitchen["target"] == 30 and kitchen["work_plane"] == 3.0, "a kitchen's general lighting target is 30 fc at counter height")
check(abs(L.room_cavity_ratio(kitchen) - 5 * 6 * 26 / 168) < 1e-9, "room cavity ratio = 5 x cavity height x (L + W) / (L x W)")
check(abs(L.coefficient_of_utilization(2.5) - (0.78 + 0.71) / 2) < 1e-9 and L.coefficient_of_utilization(20) == 0.39, "CU is read off the table, between rows")
plan = L.layout(1200, kitchen)
check((plan["count"], plan["rows"], plan["cols"]) == (9, 3, 3) and plan["footcandles"] >= 30, "14 x 12 kitchen, 1,200 lm lights: 9 in a 3 x 3 grid, at least 30 fc")
check(L.layout(650, kitchen)["count"] > L.layout(2000, kitchen)["count"], "cheaper low-output lights need more of them than high-output ones")
check(all(0 < x < 14 and 0 < y < 12 for x, y in plan["positions"]) and plan["positions"][0] == [round(14 / 6, 2), 2.0], "every light sits inside the room, half a spacing from the walls")
hall = L.layout(20000, L.read_room({"type": "warehouse", "length": "60", "width": "60", "height": "12"}))
check(hall["added_for_spacing"] > 0 and max(hall["spacing"]) <= 1.5 * 12, "high-output lights spread too far apart get more fixtures, so the light is even")
check(L.read_room({"type": "kitchen", "target": "45"})["target"] == 45, "a typed target wins")
check(L.lumens_from_name("Halo 6in LED wafer 1,200 lm 3000K") == 1200 and L.lumens_from_name("4ft panel 5000 lumens") == 5000 and L.lumens_from_name("20A GFCI") is None,
      "lumens are read from a name only when the name says them (never guessed)")

# 1b. round rooms: rings around a middle light, all inside the room
import math  # noqa: E402
round_room = L.read_room({"shape": "circle", "type": "living", "length": "16", "height": "9"})
check(round_room["width"] == 16 and abs(L.floor_area(round_room) - math.pi * 64) < 1e-9, "a round room's length is its diameter; its area is pi r squared")
check(abs(L.room_cavity_ratio(round_room) - 2.5 * 6.5 * math.pi * 16 / (math.pi * 64)) < 1e-9, "its cavity ratio uses its circumference")
ring = L.layout(900, round_room)
check(ring["count"] == ring["needed"] and ring["positions"][0] == [8.0, 8.0], "it gets exactly the lights it needs, one in the middle")
check(all(math.hypot(x - 8, y - 8) < 8 for x, y in ring["positions"]), "every light is inside the round room")
check(ring["footcandles"] >= round_room["target"], "and the target is met")
small = L.layout(1200, L.read_room({"shape": "circle", "type": "kitchen", "length": "12", "height": "9"}))
check(small["count"] <= 6 and [8.0, 8.0] not in small["positions"], "a few lights sit on one ring")

# 2. the quote: lights on the Pricing Sheet, the room, the plans
with app.app_context():
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen lighting", job_number=880, description="New recessed lights in the kitchen."); db.session.add(job)
    cheap = m.PriceItem(name="4in LED wafer 650 lm", lookup_key="w4", category="Lighting", unit="ea", price=11, labor_hours_per_unit=0.5, image_path="img/logo-wordmark.png")
    mid = m.PriceItem(name="Halo 6in LED wafer 1200 lm", lookup_key="w6", category="Lighting", unit="ea", price=24, labor_hours_per_unit=0.5)
    premium = m.PriceItem(name="Juno trimless downlight", lookup_key="juno", category="Lighting", unit="ea", price=95, labor_hours_per_unit=0.75, lumens=2000)
    nolumens = m.PriceItem(name="Surface mount LED flush mount light", lookup_key="flush", category="Lighting", unit="ea", price=30)
    dimmer = m.PriceItem(name="LED dimmer switch", lookup_key="dim", category="Devices", unit="ea", price=28, labor_hours_per_unit=0.4)
    db.session.add_all([cheap, mid, premium, nolumens, dimmer]); db.session.commit()
    job_id = job.id; ids = {"cheap": cheap.id, "mid": mid.id, "premium": premium.id, "flush": nolumens.id, "dimmer": dimmer.id}
owner = sign_in(m)
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check('name="lighting" value="1"' in page and "Lighting layout" in page and 'id="lighting"' not in page,
      "the message box has a Lighting layout checkbox (no separate section until there is a room)")
with app.app_context():
    quote = m.Quote.query.filter_by(job_id=job_id).first(); quote.option_count = 3; db.session.commit(); quote_id = quote.id
    tiers = m.quote_tiers(3)
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Add recessed lights to the kitchen please", lighting="1"))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(not q.lighting_room and "I need the room's size" in q.messages[-1].content, "ticked with no room size: it asks for the size (and builds nothing)")
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check("Surface mount LED flush mount light" not in page, "no lighting section yet")
owner.post("/quoting/pricing/%d/profile" % ids["flush"], data=dict(CSRF, lumens="1500"))
with app.app_context():
    check(db.session.get(m.PriceItem, ids["flush"]).lumens == 1500, "lumens for a light without them can be typed on its profile")
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Kitchen is 14x12 with 9 ft ceilings", lighting="1"))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(q.lighting_room and (q.lighting_room["length"], q.lighting_room["width"], q.lighting_room["height"], q.lighting_room["type"]) == (14, 12, 9, "kitchen"),
          "the room is read from the message: kitchen, 14 x 12, 9 ft ceiling")
    check(any("Lighting layout: Kitchen, 14 × 12 ft, 9 ft ceiling, kitchen (30 foot-candles)." in msg.content for msg in q.messages if msg.role == "assistant"),
          "and Caden's chat says what it read")
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check('id="lighting"' not in page and "lighting-plans" not in page and "Juno trimless downlight" not in page,
      "there is no lighting box listing the Pricing Sheet's lights: each option shows its own layout instead")
check('name="lighting" value="1" checked' in page, "and the checkbox stays ticked")
with app.app_context():
    names = [plan["name"] for plan in m.lighting_plans_for(db.session.get(m.Quote, quote_id))[1]]
    check(sorted(names) == sorted(["4in LED wafer 650 lm", "Halo 6in LED wafer 1200 lm", "Juno trimless downlight", "Surface mount LED flush mount light"]),
          "every light with lumens can be laid out (a dimmer switch is not mistaken for a light)")

# 3. Caden picks a light per option; the count is the layout's
SEEN = []


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(kwargs)
        picks = [("cheap", 10), ("mid", 6), ("premium", 2)]
        options = [{"tier": tier, "title": "Kitchen lighting", "description": "Bright, even light.", "bullets": ["Turnkey installation of recessed lights"],
                    "services": [], "items": [{"price_item_id": ids[key], "quantity": qty}, {"price_item_id": ids["dimmer"], "quantity": 1}],
                    "direct_priced_items": [], "missing_services": []} for tier, (key, qty) in zip(tiers, picks)]
        return SimpleNamespace(output_text=json.dumps({"ready": True, "message": "Built.", "questions": [], "quote_title": "Kitchen lighting",
                                                       "introduction": "Hi.", "mike_question": "", "options": options, "pricing_needed": []}))


m.OpenAI = FakeOpenAI
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Recessed lights for the kitchen, three choices.", lighting="1"))
words = [k for k in SEEN if k.get("text", {}).get("format", {}).get("name") == "powerhouse_quote_plan"][-1]["instructions"]
check("LIGHTING_PLAN for Kitchen (14 x 12 ft, 9 ft ceiling" in words and '"price_item_id": %d' % ids["mid"] in words and "exactly its quantity" in words,
      "Caden gets the room, every light's layout, and the rule to use its quantity")
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    counts = {line.price_item_id: line.multiplier for option in q.options for line in option.lines if line.price_item_id and line.price_item_id != ids["dimmer"]}
    expected = {ids[key]: L.layout(lm, kitchen)["count"] for key, lm in (("cheap", 650), ("mid", 1200), ("premium", 2000))}
    check(counts == expected, "each option's light count is the layout's (%s), not Caden's 10 / 6 / 2" % sorted(expected.values()))
    wires = [[line for line in option.lines if line.service_name == m.LIGHTING_WIRE_NAME] for option in q.options]
    check(all(len(w) == 1 and w[0].is_missing for w in wires) and all(w[0].multiplier == option.lighting["wire_feet"] == L.wire_run(L.layout(option.lighting["lumens"], kitchen), kitchen)["feet"]
                                                                       for w, option in zip(wires, q.options)),
          "every option carries the wire for its lights, as many feet as its layout needs (to be priced when 14/2 is not on the Pricing Sheet)")
    layouts = [option.lighting for option in q.options]
    check(all(layout and len(layout["positions"]) == layout["count"] for layout in layouts), "every option keeps its layout for the 3D view")
    check(any("set to" in msg.content and "Caden had" in msg.content for msg in q.messages if msg.role == "assistant"), "Caden's message says the counts were set by the layout")
    dimmer_qty = [line.multiplier for option in q.options for line in option.lines if line.price_item_id == ids["dimmer"]]
    check(dimmer_qty == [1, 1, 1], "everything else in the option is left as Caden had it")
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check(page.count("data-light-layout=") == 3 and "drag to turn" in page, "each option draws its 3D layout")
check('class="line-thumb"' in page and "img/logo-wordmark.png" in page, "a material with a Pricing Sheet picture shows it on its line")

# 3b. a RAB light Caden recommends that is not on the Pricing Sheet is laid out from its lumens and listed to price
SEEN.clear()
check("We use RAB lighting for almost everything" in words and "RAB SLIM6 6in LED wafer 1200 lm" in words, "Caden is told we use RAB, and how to name a RAB light that is not on the sheet")


class RabOpenAI(FakeOpenAI):
    def create(self, **kwargs):
        options = [{"tier": tier, "title": "Kitchen lighting", "description": "Even light.", "bullets": ["Turnkey installation of recessed lights"],
                    "services": [], "items": [], "direct_priced_items": [], "missing_services": [{"name": "RAB RDLED6 6in downlight 1500 lm", "quantity": 3, "unit": "ea"}]}
                   for tier in tiers]
        return SimpleNamespace(output_text=json.dumps({"ready": True, "message": "Built.", "questions": [], "quote_title": "Kitchen lighting",
                                                       "introduction": "Hi.", "mike_question": "", "options": options, "pricing_needed": []}))


m.OpenAI = RabOpenAI
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Use the RAB 1500 lm downlights.", lighting="1"))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    line = q.options[0].lines[0]
    check(line.is_missing and line.multiplier == L.layout(1500, kitchen)["count"], "the RAB light is still to price (top of the quote), with the layout's count, not Caden's 3")
    check(q.options[0].lighting and q.options[0].lighting["fixture"].startswith("RAB RDLED6"), "and it gets its 3D layout")


# 3c. Caden leaves the lights out (or picks one with no lumens): every option still gets its own light and its own 3D view
class NoLightOpenAI(FakeOpenAI):
    def create(self, **kwargs):
        SEEN.append(kwargs)
        options = []
        for index, tier in enumerate(tiers):
            items = [{"price_item_id": ids["dimmer"], "quantity": 1}]
            if index == 1:
                items.append({"price_item_id": NOLUMEN, "quantity": 4})
            options.append({"tier": tier, "title": "Kitchen lighting %d" % index, "description": "Even light.", "bullets": ["Lights, tier %d" % index],
                            "services": [], "items": items, "direct_priced_items": [], "missing_services": []})
        return SimpleNamespace(output_text=json.dumps({"ready": True, "message": "Built.", "questions": [], "quote_title": "Kitchen lighting",
                                                       "introduction": "Hi.", "mike_question": "", "options": options, "pricing_needed": []}))


with app.app_context():
    plain = m.PriceItem(name="RAB 6in LED wafer", lookup_key="rabplain", category="Lighting", unit="ea", price=19); db.session.add(plain); db.session.commit()
    NOLUMEN = plain.id
m.OpenAI = NoLightOpenAI
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Lights, three choices.", lighting="1"))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    layouts = [option.lighting for option in q.options]
    check(len(q.options) == 3 and all(layouts), "an option Caden built without a light still gets a light from the layout and its own 3D view")
    check(len({layout["fixture"] for layout in layouts}) == 3 and layouts[0]["fixture"] == "4in LED wafer 650 lm",
          "the tiers get different lights, the most economical one that meets the target first")
    check(all(layout["footcandles"] >= layout["target"] * 0.95 for layout in layouts), "each one meets the room's target")
    middle = [line for line in q.options[1].lines if line.price_item_id not in (ids["dimmer"],) and line.service_name != m.LIGHTING_WIRE_NAME and "connector" not in line.service_name.lower()]
    check(len(middle) == 1 and middle[0].price_item_id != NOLUMEN and middle[0].multiplier == layouts[1]["count"],
          "a light with no lumens is swapped for one that can be laid out (not a second set of lights)")
    check(any("has no lumens" in msg.content for msg in q.messages if msg.role == "assistant"), "and Caden's message says so")
check("never ask about the number of lights" in [k for k in SEEN if k.get("text", {}).get("format", {}).get("name") == "powerhouse_quote_plan"][-1]["instructions"], "Caden is told to build the options rather than ask about the lights")
m.OpenAI = FakeOpenAI
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Back to the Pricing Sheet lights.", lighting="1"))

# 4. the client presentation: layout and pictures
with app.app_context():
    q = db.session.get(m.Quote, quote_id); q.status = "confirmed"; db.session.commit()
pres = owner.get("/quotes/%d" % quote_id).get_data(as_text=True)
check(pres.count("data-light-layout=") == 3 and 'class="proposal-pictures"' in pres, "the presentation shows each option's layout and the pictures of what is used")

# 4b. a round room on the quote
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    q.status = "draft"; db.session.commit()
m.OpenAI = FakeOpenAI
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Actually it's a sunroom, 16 ft round, 9 ft ceiling", lighting="1"))
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(all(option.lighting and option.lighting["shape"] == "circle" for option in q.options), "every option's layout is round now")
check('Update from Cooper</button>' in owner.get("/quoting/pricing").get_data(as_text=True), "selected Pricing Sheet items: Update from Cooper (price and picture)")

owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Never mind the lights, just the outlets"))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(not q.lighting_room and not any(option.lighting for option in q.options), "unticked, it is not a lighting quote any more")
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Lights after all: the 16 ft round sunroom, 9 ft ceiling", lighting="1"))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(all(option.lighting for option in q.options), "ticked again, the layouts come back")
    q.status = "confirmed"; db.session.commit()

# 5. lumens on the Pricing Sheet's own save, and the room cleared
r = owner.post("/quoting/pricing/%d/edit" % ids["dimmer"], data=dict(CSRF, name="LED dimmer switch", category="Devices", unit="ea", price="28", labor_hours_per_unit="0.4", lumens=""),
               headers={"X-Requested-With": "fetch"})
with app.app_context():
    check(db.session.get(m.PriceItem, ids["dimmer"]).lumens is None, "a blank lumens field leaves an item without lumens")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
app.config["TESTING"] = False
with app.app_context():
    u = m.User(username="viewer", role=m.Role.query.filter_by(is_admin=True).first()); u.set_password("a-long-password-1"); db.session.add(u); db.session.commit()
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_port
with sync_playwright() as pw:
    try:
        exe = os.environ.get("PW_CHROMIUM") or None
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 1300, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/login"); page.fill("input[name=username]", "viewer"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(BASE + "/quotes/%d" % quote_id)
    canvas = page.locator("canvas[data-light-layout]").first
    canvas.wait_for(state="attached")
    page.wait_for_function("document.querySelector('canvas[data-light-layout]').dataset.drawn === '1'")
    lit = page.evaluate("""() => { const c = document.querySelector('canvas[data-light-layout]'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let bright = 0; for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i + 1] > 180 && d[i + 3] > 0) bright++; return bright; }""")
    check(lit > 30 and not errors, "the 3D view draws the room with its lights lit (no script errors)")
    before = page.evaluate("() => document.querySelector('canvas[data-light-layout]').toDataURL()")
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 60, box["y"] + 80); page.mouse.down(); page.mouse.move(box["x"] + 200, box["y"] + 120, steps=6); page.mouse.up()
    check(page.evaluate("() => document.querySelector('canvas[data-light-layout]').toDataURL()") != before, "dragging turns the room")
    check(page.evaluate("() => JSON.parse(document.querySelector('canvas[data-light-layout]').dataset.lightLayout).room.shape") == "circle" and not errors,
          "the round room draws without errors")
    browser.close()
done()
