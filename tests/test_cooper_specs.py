"""Update from Cooper brings back a material's details: the office listener reads the product's own page (spec tables,
definition lists, "Label: value" lines) and the site keeps them with the material, lumens included (never over lumens typed
by hand). The (i) next to each material opens its profile. Browser half drives the listener against a pretend Cooper."""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, ROOT, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    light = m.PriceItem(name="RAB 6in LED wafer", lookup_key="rab6", category="Lighting", unit="ea", price=24, cooper_part_number="SLIM6")
    typed = m.PriceItem(name="RAB 4in wafer", lookup_key="rab4", category="Lighting", unit="ea", price=18, cooper_part_number="SLIM4", lumens=700, lumens_source="typed")
    breaker = m.PriceItem(name="20A breaker", lookup_key="qo120", category="Panels", unit="ea", price=9, cooper_part_number="QO120")
    dark = m.PriceItem(name="LED flush mount light", lookup_key="flush", category="Lighting", unit="ea", price=30, cooper_part_number="FLUSH1")
    from werkzeug.security import generate_password_hash
    db.session.add_all([light, typed, breaker, dark, m.CompanySetting(key=m.LOCAL_COOPER_TOKEN_KEY, value=generate_password_hash("tok"))])
    db.session.commit()
    ids = {"light": light.id, "typed": typed.id, "breaker": breaker.id, "dark": dark.id}
client = sign_in(m)
api = {"Authorization": "Bearer tok", "User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.8"}
JSON = {"Accept": "application/json"}

# 1. which items get their product page read
client.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="all"), headers=JSON)
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
want = {item["part_number"]: item["want_specs"] for item in cmd["items"]}
check(want == {"SLIM6": True, "SLIM4": False, "QO120": False, "FLUSH1": True}, "a full price check reads the product page only for lights without lumens")
with app.app_context():
    m.PriceSyncRun.query.update({"status": "cancelled"}); db.session.commit()
client.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="selected", item_ids="%d,%d" % (ids["breaker"], ids["typed"])), headers=JSON)
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
check(all(item["want_specs"] for item in cmd["items"]) and len(cmd["items"]) == 2, "Update from Cooper (selected items) reads every selected item's page")
batch = cmd["id"]

# 2. the site keeps what the page lists
post = lambda payload: client.post("/api/local-cooper/commands/%d/specs" % batch, json=payload, headers=api)
r = post({"item_id": ids["breaker"], "part_number": "QO120", "url": "https://www.cooperelectric.com/product/QO120", "title": "Square D QO 20A breaker",
          "specs": {"Amperage": "20 A", "Poles": "1", "Voltage": "120/240 V", "": "x", "Same": "same"}})
check(r.status_code == 201 and r.json["fields"] == 4, "the details are saved (empty and meaningless pairs left out)")
with app.app_context():
    item = db.session.get(m.PriceItem, ids["breaker"])
    check(item.specs["Amperage"] == "20 A" and item.specs["Title"] == "Square D QO 20A breaker" and item.product_url.endswith("/QO120") and item.specs_at,
          "with the title and Cooper's page address, and when they were read")
    check(item.lumens is None, "a breaker gets no lumens")
r = post({"item_id": ids["typed"], "part_number": "SLIM4", "specs": {"Lumens": "760 lm", "Wattage": "9 W"}})
with app.app_context():
    check(db.session.get(m.PriceItem, ids["typed"]).lumens == 700, "lumens typed by hand are never replaced by Cooper's")
with app.app_context():
    m.PriceSyncRun.query.update({"status": "cancelled"}); db.session.commit()
client.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="selected", item_ids="%d,%d" % (ids["light"], ids["dark"])), headers=JSON)
batch = client.get("/api/local-cooper/commands/next", headers=api).json["command"]["id"]
r = post({"item_id": ids["light"], "part_number": "SLIM6", "specs": {"Light Output (Lumens)": "1,200", "Color Temperature": "3000K", "Wattage": "12 W"}})
check(r.json["lumens"] == 1200, "a light's lumens come from its details ('Light Output (Lumens): 1,200')")
r = post({"item_id": ids["dark"], "part_number": "FLUSH1", "specs": {"Finish": "White"}, "description": "LED flush mount, 1500 lumens, dimmable"})
check(r.json["lumens"] == 1500, "or from its description when no field names them")
with app.app_context():
    check(db.session.get(m.PriceItem, ids["light"]).lumens_source == "cooper", "and the material remembers they came from Cooper")
check(post({"item_id": ids["light"], "part_number": "WRONG", "specs": {"a": "b"}}).status_code == 400, "details for the wrong part number are refused")
check(client.post("/api/local-cooper/commands/%d/specs" % batch, json={"item_id": ids["light"], "part_number": "SLIM6"}).status_code in (401, 403),
      "the listener's token is required")

# 3. the profile: (i) on the Pricing Sheet, the window and its own page
page = client.get("/quoting/pricing").get_data(as_text=True)
check('class="info-icon" data-profile' in page and 'id="profile-dialog"' in page, "every material has an (i) that opens its profile")
profile = client.get("/quoting/pricing/%d/profile?partial=1" % ids["light"]).get_data(as_text=True)
for words in ("RAB 6in LED wafer", "Color Temperature", "3000K", "1,200", "from Cooper&#39;s product page", "Cooper SLIM6".replace("Cooper ", "")):
    check(words in profile or words.replace("&#39;", "'") in profile, f"the profile shows {words.replace('&#39;', chr(39))}")
check("<html" not in profile and "<html" in client.get("/quoting/pricing/%d/profile" % ids["light"]).get_data(as_text=True).lower(), "a window part, or a page of its own")
r = client.post("/quoting/pricing/%d/profile" % ids["light"], data=dict(CSRF, lumens="1250", next="/quoting/pricing"))
with app.app_context():
    item = db.session.get(m.PriceItem, ids["light"])
    check(item.lumens == 1250 and item.lumens_source == "typed" and r.headers["Location"].endswith("/quoting/pricing"), "lumens can be typed on the profile, and then stay")
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_pricing = False; db.session.commit()
check(crew.get("/quoting/pricing/%d/profile" % ids["light"]).status_code == 403, "a profile needs Pricing Sheet access")

# 4. the listener reads a product page (browser)
try:
    from playwright.async_api import async_playwright  # noqa: F401
except ImportError:
    skip("Playwright is not installed (listener half)"); done()
from flask import Flask  # noqa: E402
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# 3b. the (i) opens the profile in a window on the Pricing Sheet
from playwright.sync_api import sync_playwright  # noqa: E402
app.config["TESTING"] = False
with app.app_context():
    viewer = m.User(username="viewer", role=m.Role.query.filter_by(is_admin=True).first()); viewer.set_password("a-long-password-1")
    db.session.add(viewer); db.session.commit()
site = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=site.serve_forever, daemon=True).start()
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page()
    base = "http://127.0.0.1:%d" % site.server_port
    page.goto(base + "/login"); page.fill("input[name=username]", "viewer"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/quoting/pricing")
    page.locator("#price-item-%d .info-icon" % ids["light"]).click()
    page.wait_for_selector("#profile-dialog[open] .price-profile")
    check(page.url.endswith("/quoting/pricing") and "Color Temperature" in page.inner_text("#profile-dialog"), "clicking the (i) opens the profile in a window, without leaving the sheet")
    page.click("[data-profile-close]")
    check(not page.locator("#profile-dialog").evaluate("d => d.open"), "and it closes")
    browser.close()

shop = Flask("pretend_cooper_specs")
PAGES = {
    "SLIM6": "<h1>RAB SLIM6 6in LED wafer</h1><table><tr><th>Light Output (Lumens)</th><td>1,200</td></tr><tr><th>Wattage</th><td>12 W</td></tr></table>"
             "<dl><dt>Color Temperature</dt><dd>3000K</dd></dl>",
    "PANEL2": "<h1>RAB 2x4 panel</h1><div class='product-specifications'><ul><li>Lumens: 5,000</li><li>Voltage: 120-277 V</li></ul></div>",
    "BARE": "<h1>Bare product</h1><p>Nothing listed here.</p>",
}


@shop.route("/")
def home():
    return "<html><body>Cooper</body></html>"


@shop.route("/product/search")
def search():
    from flask import request
    part = request.args.get("q", "")
    return (f'<html><body><div id="productSearchResults"><div class="product productCard" data-product-id="{part}"><a href="/product/{part}/detail">'
            f'<span data-analytics-description="{part} item">{part}</span></a><span class="pricerPerQty" data-analytics-price="10.00"></span>'
            f'<small class="priceUOM">/ea</small></div></div></body></html>')


@shop.route("/product/<part>/detail")
def detail(part):
    return f"<html><body><nav><a href='/product/category/US_1/lights'>Lights</a></nav>{PAGES.get(part, '')}</body></html>"


server = make_server("127.0.0.1", 0, shop, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
spec = importlib.util.spec_from_file_location("listener_specs", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
listener.log = lambda message: None
listener.COOPER_HOME = "http://127.0.0.1:%d/" % server.server_port
listener.BROWSER_CHANNEL = ""
listener.PROFILE_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="specs-profile-"))
listener.DEBUG_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="specs-debug-"))
listener.login_to_cooper = lambda page, config: asyncio.sleep(0)
if os.environ.get("PW_CHROMIUM"):
    os.environ["COOPER_BROWSER_PATH"] = os.environ["PW_CHROMIUM"]
sent = {}
items = [{"id": 1, "name": "wafer", "part_number": "SLIM6", "want_specs": True}, {"id": 2, "name": "panel", "part_number": "PANEL2", "want_specs": True},
         {"id": 3, "name": "bare", "part_number": "BARE", "want_specs": True}, {"id": 4, "name": "no details wanted", "part_number": "SLIM6", "want_specs": False}]
try:
    results = asyncio.run(listener.scan_all_items(items, {"cooper_username": "u", "cooper_password": "p"}, tabs=1, headless=True,
                                                  send_specs=lambda item, details: sent.setdefault(item["id"], details)))
except Exception as error:  # noqa: BLE001
    if "xecutable" in str(error):
        skip("no browser for the listener"); done()
    raise
check(all(r["status"] == "matched" for r in results) and results[0]["product_url"].endswith("/product/SLIM6/detail"), "the price is read as before, and the product page's link noted")
check(sent[1]["specs"] == {"Light Output (Lumens)": "1,200", "Wattage": "12 W", "Color Temperature": "3000K"} and sent[1]["title"] == "RAB SLIM6 6in LED wafer",
      "a spec table and a definition list are read")
check(sent[2]["specs"] == {"Lumens": "5,000", "Voltage": "120-277 V"}, "and 'Label: value' lines in a specifications box")
check(sent[3]["specs"] == {} and list(listener.DEBUG_DIRECTORY.glob("product-*.html")), "a page with nothing readable is kept in catalog_debug for fitting the reader")
check(4 not in sent, "an item that does not want details is not opened")
check(listener.LISTENER_VERSION == "2.8", "the listener is v2.8")
done()
