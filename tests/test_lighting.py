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
check('id="lighting"' in page and "Quoting lights for a room? Set the room here" in page, "the draft offers a Lighting layout panel")
with app.app_context():
    quote = m.Quote.query.filter_by(job_id=job_id).first(); quote.option_count = 3; db.session.commit(); quote_id = quote.id
    tiers = m.quote_tiers(3)
r = owner.post("/quotes/%d/lighting" % quote_id, data=dict(CSRF, name="Kitchen", type="kitchen", length="14", width="12", height="9", **{"lumens_%d" % ids["flush"]: "1500"}))
check(r.status_code == 302 and r.headers["Location"].endswith("#lighting"), "setting the room saves")
with app.app_context():
    check(db.session.get(m.PriceItem, ids["flush"]).lumens == 1500, "lumens typed for a light without them are saved to the Pricing Sheet")
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check("Kitchen · 14 × 12 ft, 9 ft ceiling · 30 fc" in page, "the panel shows the room")
for name in ("4in LED wafer 650 lm", "Halo 6in LED wafer 1200 lm", "Juno trimless downlight", "Surface mount LED flush mount light"):
    check(name in page.split('class="item-table lighting-plans"')[1].split("</table>")[0], f"plans list {name}")
check('name="lumens_%d"' % ids["dimmer"] not in page, "a dimmer switch is not mistaken for a light that needs lumens")
check("9 (3 × 3" in page, "with how many and the grid (9 of the 1,200 lm wafers, 3 × 3)")

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
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Recessed lights for the kitchen, three choices."))
words = SEEN[-1]["instructions"]
check("LIGHTING_PLAN for Kitchen (14 x 12 ft, 9 ft ceiling" in words and '"price_item_id": %d' % ids["mid"] in words and "exactly its quantity" in words,
      "Caden gets the room, every light's layout, and the rule to use its quantity")
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    counts = {line.price_item_id: line.multiplier for option in q.options for line in option.lines if line.price_item_id != ids["dimmer"]}
    expected = {ids[key]: L.layout(lm, kitchen)["count"] for key, lm in (("cheap", 650), ("mid", 1200), ("premium", 2000))}
    check(counts == expected, "each option's light count is the layout's (%s), not Caden's 10 / 6 / 2" % sorted(expected.values()))
    layouts = [option.lighting for option in q.options]
    check(all(layout and len(layout["positions"]) == layout["count"] for layout in layouts), "every option keeps its layout for the 3D view")
    check(any("set to" in msg.content and "Caden had" in msg.content for msg in q.messages if msg.role == "assistant"), "Caden's message says the counts were set by the layout")
    dimmer_qty = [line.multiplier for option in q.options for line in option.lines if line.price_item_id == ids["dimmer"]]
    check(dimmer_qty == [1, 1, 1], "everything else in the option is left as Caden had it")
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check(page.count("data-light-layout=") == 3 and "drag to turn" in page, "each option draws its 3D layout")
check('class="line-thumb"' in page and "img/logo-wordmark.png" in page, "a material with a Pricing Sheet picture shows it on its line")

# 4. the client presentation: layout and pictures
with app.app_context():
    q = db.session.get(m.Quote, quote_id); q.status = "confirmed"; db.session.commit()
pres = owner.get("/quotes/%d" % quote_id).get_data(as_text=True)
check(pres.count("data-light-layout=") == 3 and 'class="proposal-pictures"' in pres, "the presentation shows each option's layout and the pictures of what is used")

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
    browser.close()
done()
