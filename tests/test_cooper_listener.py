"""The office Cooper listener (tools/cooper_price_listener.py) against a pretend Cooper site, and the website's side:
"Get Cooper prices for selected", product pictures, and the Pricing Sheet saving itself. The browser parts are
skipped where no browser is installed."""
import base64
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, ROOT, check, done, make_site, sign_in, skip  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

# ---------------------------------------------------------------- the website side
m = make_site()
app, db = m.app, m.db
with app.app_context():
    items = [m.PriceItem(name=n, lookup_key=n.lower(), unit=u, price=p, cooper_part_number=c)
             for n, u, p, c in (("200A panel", "ea", 410, "QO130L200PG"), ("12/2 Romex", "ft", 0, "122NMB250"),
                                ("GFCI receptacle", "ea", 22, "GF20W"), ("Wire nuts", "box", 9, ""))]
    db.session.add_all(items); db.session.commit()
    ids = {i.name: i.id for i in items}
    token = "listener-token-123"
    from werkzeug.security import generate_password_hash
    db.session.add(m.CompanySetting(key=m.LOCAL_COOPER_TOKEN_KEY, value=generate_password_hash(token)))
    db.session.commit()

client = sign_in(m)
page = client.get("/quoting/pricing").get_data(as_text=True)
check(">Save<" not in page and 'title="Remove pricing item"' not in page, "the Pricing Sheet rows have no Save or × buttons")
check(page.count("data-select-item") == 4 and "data-select-all" in page, "every row has a checkbox, with select-all")
check("Get Cooper prices for selected" in page and "Remove selected" in page, "and a bar to price or remove the selected items")

# autosave: the page's background save answers with the redrawn row
H = {"X-Requested-With": "fetch"}
form = dict(CSRF, name="GFCI receptacle", category="Devices", unit="ea", price="24.75", labor_hours_per_unit="0.5",
            **{"supplier_name[]": "Cooper Electric", "supplier_part_number[]": "GF20W"})
r = client.post("/quoting/pricing/%d/edit" % ids["GFCI receptacle"], data=form, headers=H)
check(r.status_code == 200 and r.json["ok"] and 'value="24.75"' in r.json["row"], "a change saves at once and the row comes back")
with app.app_context():
    check(db.session.get(m.PriceItem, ids["GFCI receptacle"]).price == 24.75, "the new price is stored")
r = client.post("/quoting/pricing/%d/edit" % ids["GFCI receptacle"], data=dict(form, name="200A panel"), headers=H)
check(r.status_code == 400 and "matches existing item" in r.json["error"] and 'value="GFCI receptacle"' in r.json["row"], "a clash is refused, said, and the row shows what is stored")

# pictures by hand
from io import BytesIO
r = client.post("/quoting/pricing/%d/image" % ids["Wire nuts"], data=dict(CSRF, image=(BytesIO(PNG), "nuts.png")), headers=H, content_type="multipart/form-data")
check(r.status_code == 200 and "uploads/price-items/" in r.json["row"], "a picture can be added to an item")
r = client.post("/quoting/pricing/%d/image" % ids["Wire nuts"], data=dict(CSRF, image=(BytesIO(b"<html>not a picture"), "x.png")), headers=H, content_type="multipart/form-data")
check(r.status_code == 400 and "not a JPEG" in r.json["error"], "a file that is not a picture is refused")

# "Get Cooper prices for selected"
r = client.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="selected", item_ids="%d,%d" % (ids["200A panel"], ids["Wire nuts"])), headers={"Accept": "application/json"})
check(r.status_code == 200 and r.json.get("batch_id"), "selected items can be sent to the listener")
api = {"Authorization": "Bearer " + token, "User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.0"}
r = client.get("/api/local-cooper/commands/next", headers=api)
command = r.json["command"]
check([i["part_number"] for i in command["items"]] == ["QO130L200PG"], "the listener gets only the selected items that have a Cooper number")
with app.app_context():
    check(db.session.get(m.CompanySetting, "cooper_listener_version").value == "2.0", "the site notes which listener version checked in")
batch = command["id"]

# the listener sends pictures as it scans
pic = base64.b64encode(PNG).decode()
r = client.post("/api/local-cooper/commands/%d/image" % batch, json={"item_id": ids["200A panel"], "part_number": "QO130L200PG", "data": pic}, headers=api)
check(r.status_code == 201, "a picture from the listener is saved")
r = client.post("/api/local-cooper/commands/%d/image" % batch, json={"item_id": ids["200A panel"], "part_number": "WRONG", "data": pic}, headers=api)
check(r.status_code == 400, "a picture for the wrong part number is refused")
r = client.post("/api/local-cooper/commands/%d/image" % batch, json={"item_id": ids["Wire nuts"], "part_number": "", "data": pic}, headers=api)
with app.app_context():
    check(db.session.get(m.PriceItem, ids["Wire nuts"]).image_source == "upload", "a picture uploaded by hand is never replaced by Cooper's")
    panel = db.session.get(m.PriceItem, ids["200A panel"])
    check(panel.image_source == "cooper" and os.path.isfile(os.path.join(os.getcwd(), "static", panel.image_path)), "Cooper's picture is on disk under static/uploads")
r = client.post("/api/local-cooper/commands/%d/image" % batch, json={"item_id": ids["200A panel"], "part_number": "QO130L200PG", "data": pic}, headers={"Authorization": "Bearer nope"})
check(r.status_code == 401, "the picture door needs the listener's token")
r = client.post("/api/local-cooper/commands/%d/results" % batch, json={"results": [{"item_id": ids["200A panel"], "part_number": "QO130L200PG", "status": "matched", "source_price": 389.0, "source_quantity": 1, "source_unit": "ea"}]}, headers=api)
check(r.status_code == 201 and r.json["matched"] == 1, "results still go through the review page")
with app.app_context():
    check(db.session.get(m.PriceItem, ids["200A panel"]).price == 410, "and no price changes until it is approved")

# removing selected items
r = client.post("/quoting/pricing/remove-selected", data=dict(CSRF, item_ids=str(ids["Wire nuts"])), follow_redirects=True)
with app.app_context():
    check(not db.session.get(m.PriceItem, ids["Wire nuts"]).active and b"Removed 1 item" in r.data, "Remove selected removes them (history kept)")

CURRENT = re.search(r'LISTENER_VERSION = "([\d.]+)"', open(os.path.join(ROOT, "tools", "cooper_price_listener.py")).read()).group(1)
page = client.get("/settings").get_data(as_text=True)
check("Download the listener (v%s)" % CURRENT in page, "Settings offers the current listener (v%s) to download" % CURRENT)
r = client.get("/settings/local-cooper/listener.py")
check(r.status_code == 200 and ('LISTENER_VERSION = "%s"' % CURRENT).encode() in r.data, "and serves the file")

# ---------------------------------------------------------------- the listener against a pretend Cooper
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    import playwright.async_api  # noqa: F401
except ImportError:
    skip("Playwright is not installed: the listener and the autosave page were not driven")
    done()

from flask import Flask, request, make_response, redirect  # noqa: E402
cooper = Flask("pretend_cooper")
HITS = {"login_posts": 0, "pictures": 0, "searches": 0, "inflight": 0, "max_inflight": 0}
LOCK = threading.Lock()
PRODUCTS = {"QO130L200PG": ("Square D 200A main breaker panel", "389.00", "/ea"),
            "122NMB250": ("Romex 12/2 NM-B with ground, 250 ft", "129.40", "(250 ft)"),
            "GF20W": ("20A GFCI receptacle, white", "21.15", "/ea")}
HEAD = """<html><head><style>.hidden{display:none}</style></head><body>
<div id="cookie">We use cookies <button onclick="document.getElementById('cookie').remove()">Accept All</button></div>
%s"""


@cooper.route("/")
def home():
    if request.cookies.get("auth"):
        return HEAD % '<a href="/logout">Log Out</a>'
    return HEAD % """<div class="dropdown"><button class="dropdown-toggle" onclick="document.getElementById('f').classList.remove('hidden')">Log In</button>
    <form id="f" class="hidden" method="post" action="/login"><input id="UserName" name="UserName"><input id="Password" name="Password" type="password"><button type="submit">Log In</button></form></div>
    <form><input name="q"><button type="submit">Search</button></form>"""


@cooper.route("/login", methods=["POST"])
def login():
    HITS["login_posts"] += 1
    if request.form.get("UserName") == "office" and request.form.get("Password") == "secret":
        resp = make_response(redirect("/"))
        resp.set_cookie("auth", "1", max_age=3600)
        return resp
    return redirect("/")


@cooper.route("/product/search")
def search():
    with LOCK:
        HITS["searches"] += 1; HITS["inflight"] += 1; HITS["max_inflight"] = max(HITS["max_inflight"], HITS["inflight"])
    try:
        time.sleep(0.4)                                  # a slow page, so parallel tabs show
        q = request.args.get("q", "")
        if q == "DIRECT1":                               # an exact part number: Cooper goes straight to the product's page
            return redirect("/product/direct-1-wire-nut")
        if q == "NOTHING1":
            return HEAD % "<h2>No products were found matching NOTHING1</h2>"
        if q == "BLANK1":
            return HEAD % "<div>Loading…</div>"
        cards = []
        for part, (name, price, uom) in PRODUCTS.items():
            if part.startswith(q.upper().replace("-", "")[:3]):
                priced = request.cookies.get("auth")
                cards.append(f"""<div class="product productCard" data-product-id="{part}"><img data-src="/img/{part}.png" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
                <span data-analytics-description="{name}">{name}</span><span class="pricerPerQty" data-price="{price if priced else ''}"></span><small class="priceUOM">{uom}</small></div>""")
        # the price arrives a moment after the page, like Cooper's
        return HEAD % f"""<div id="productSearchResults">{''.join(cards) or '<div class="product productCard" data-product-id="OTHER123"></div>'}</div>
        <script>setTimeout(() => document.querySelectorAll('.pricerPerQty').forEach(e => e.setAttribute('data-analytics-price', e.dataset.price)), 500)</script>"""
    finally:
        with LOCK:
            HITS["inflight"] -= 1


@cooper.route("/product/direct-1-wire-nut")
def product_page():
    priced = request.cookies.get("auth")
    return HEAD % f"""<meta property="og:image" content="/img/DIRECT1.png"><h1>Yellow wire nut, box of 100</h1>
    <div class="productDetail" data-product-id="DIRECT1"><span class="pricePerQty" data-price="{'12.50' if priced else ''}"></span><small class="priceUOM">(100 ea)</small></div>
    <script>setTimeout(() => document.querySelectorAll('.pricePerQty').forEach(e => e.setAttribute('data-analytics-price', e.dataset.price)), 300)</script>"""


@cooper.route("/img/<part>.png")
def picture(part):
    HITS["pictures"] += 1
    resp = make_response(PNG); resp.headers["Content-Type"] = "image/png"
    return resp


import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
cooper_server = make_server("127.0.0.1", 0, cooper, threaded=True)
threading.Thread(target=cooper_server.serve_forever, daemon=True).start()
os.environ["COOPER_HOME"] = "http://127.0.0.1:%d/" % cooper_server.server_port
os.environ["COOPER_BROWSER_CHANNEL"] = ""
if os.environ.get("PW_CHROMIUM"):
    os.environ["COOPER_BROWSER_PATH"] = os.environ["PW_CHROMIUM"]
spec = importlib.util.spec_from_file_location("cooper_price_listener", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
listener.DEBUG_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="cooper-debug-"))
listener.PROFILE_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="cooper-profile-"))
check(listener.parse_source_quantity_and_unit("(250 ft)") == (250.0, "ft") and listener.parse_source_quantity_and_unit("/ea") == (1.0, "ea"), "package sizes are read as before")
check(listener.picture_url({"src": "data:x", "data-src": "/img/a.png"}, "https://cooper.test/search") == "https://cooper.test/img/a.png", "a lazy-loaded picture's real address is found")

wanted = [{"id": 1, "name": "Panel", "part_number": "QO130L200PG"}, {"id": 2, "name": "Romex", "part_number": "122NMB250"},
          {"id": 3, "name": "GFCI", "part_number": "GF20W"}, {"id": 4, "name": "Gone", "part_number": "ZZZ999"}] * 2
progress, pictures = [], []
import asyncio  # noqa: E402
try:
    started = time.monotonic()
    results = asyncio.run(listener.scan_all_items(wanted, {"cooper_username": "office", "cooper_password": "secret"},
                                                  progress=lambda d, t, msg: progress.append(d), send_picture=lambda item, data: pictures.append(item["part_number"]),
                                                  tabs=3, headless=True))
    took = time.monotonic() - started
except Exception as error:  # noqa: BLE001
    if "Executable doesn't exist" in str(error) or "executable" in str(error).lower() and "not found" in str(error).lower():
        skip("no browser to drive the listener (%s)" % str(error).splitlines()[0][:100]); done()
    raise
check([r["part_number"] for r in results] == [w["part_number"] for w in wanted], "results come back in the items' order")
panel = results[0]
check(panel["status"] == "matched" and panel["source_price"] == 389.0 and panel["source_unit"] == "ea", "a price is read off the card once it appears")
check(results[1]["source_quantity"] == 250 and results[1]["source_unit"] == "ft", "and the package size (250 ft)")
check(results[3]["status"] == "error" and "no exact match for ZZZ999" in results[3]["error"], "a part Cooper does not list is an error for that item only")
check(sorted(progress) == list(range(1, 9)), "progress is reported after every item")
check(HITS["login_posts"] == 1, "it signed in once")
check(HITS["max_inflight"] >= 2, "tabs really scan at the same time (%d at once)" % HITS["max_inflight"])
check(sorted(pictures) == sorted(["QO130L200PG", "122NMB250", "GF20W"] * 2) and HITS["pictures"] == 6, "each product picture is fetched once and sent; pages never load pictures themselves (%d fetched)" % HITS["pictures"])
check(took < 8 * 0.9 + 6, "8 items took %.1fs (one tab with the old slow-down would take far longer)" % took)

listener.FIND_WAIT_SECONDS = 3
odd = [{"id": 11, "name": "Wire nut", "part_number": "DIRECT1"}, {"id": 12, "name": "Nothing", "part_number": "NOTHING1"},
       {"id": 13, "name": "Never loads", "part_number": "BLANK1"}, {"id": 14, "name": "GFCI", "part_number": "gf-20w"}]
results = asyncio.run(listener.scan_all_items(odd, {"cooper_username": "office", "cooper_password": "secret"}, tabs=2, headless=True))
direct = results[0]
check(direct["status"] == "matched" and direct["source_price"] == 12.5 and direct["source_quantity"] == 100 and direct["name"] == "Yellow wire nut, box of 100",
      "when Cooper goes straight to the product's page, the price, package and name are read there")
check(direct["image_url"].endswith("/img/DIRECT1.png") and "/product/direct-1-wire-nut" in direct["product_url"], "with its picture and its page")
check(results[1]["status"] == "error" and "found nothing for NOTHING1" in results[1]["error"], "Cooper's \"no results\" is said plainly, not as a timeout")
check(results[2]["status"] == "error" and "neither results nor a product" in results[2]["error"] and "Timeout" not in results[2]["error"],
      "a page that never shows either is said plainly, and a copy is kept")
check(results[3]["status"] == "matched" and results[3]["source_price"] == 21.15, "a part number typed with a dash or in lower case still matches")
results = asyncio.run(listener.scan_all_items(wanted[:1], {"cooper_username": "office", "cooper_password": "secret"}, tabs=1, headless=True))
check(results[0]["status"] == "matched" and HITS["login_posts"] == 1, "the next run reuses the saved login instead of signing in again")
cooper_server.shutdown()

# ---------------------------------------------------------------- the Pricing Sheet in a browser
from playwright.sync_api import sync_playwright  # noqa: E402
app.config["TESTING"] = False
with app.app_context():
    viewer = m.User(username="viewer", role=m.Role.query.filter_by(is_admin=True).first()); viewer.set_password("a-long-password-1")
    db.session.add(viewer); db.session.commit()
site = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=site.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % site.server_port
with sync_playwright() as pw:
    exe = os.environ.get("PW_CHROMIUM") or None
    browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "viewer"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/quoting/pricing")
    row = page.locator("#price-item-%d" % ids["12/2 Romex"])
    price = row.locator("input[name=price]")
    price.fill("0.52"); price.press("Tab")
    page.wait_for_selector("#price-item-%d .autosave-status.is-ok" % ids["12/2 Romex"], timeout=5000)
    with app.app_context():
        check(db.session.get(m.PriceItem, ids["12/2 Romex"]).price == 0.52, "typing a price and moving on saves it, no button")
    row.locator("input[name=labor_hours_per_unit]").fill("0.05")
    row.locator("input[name=labor_hours_per_unit]").press("Enter")
    page.wait_for_timeout(600)
    with app.app_context():
        check(db.session.get(m.PriceItem, ids["12/2 Romex"]).labor_hours_per_unit == 0.05, "Enter saves too")
    row = page.locator("#price-item-%d" % ids["12/2 Romex"])
    row.locator("select[name=unit]").select_option("roll")
    page.wait_for_timeout(600)
    with app.app_context():
        check(db.session.get(m.PriceItem, ids["12/2 Romex"]).unit == "roll", "changing the unit saves at once")
    page.locator("[data-select-item]").nth(0).check(); page.locator("[data-select-item]").nth(1).check()
    check(page.locator("[data-selection-bar]").is_visible() and page.locator("[data-selected-count]").inner_text() == "2", "ticking two items shows the selection bar")
    check(page.locator("[data-select-all]").evaluate("e => e.indeterminate"), "select-all shows some are ticked")
    page.locator("[data-select-all]").check()
    check(page.locator("[data-selected-count]").inner_text() == str(page.locator("[data-select-item]").count()), "select-all ticks every item shown")
    page.locator("[data-clear-selection]").click()
    check(not page.locator("[data-selection-bar]").is_visible(), "Clear empties the selection")
    check(page.locator("#price-item-%d .price-thumb img" % ids["200A panel"]).count() == 1, "an item's picture shows in its row")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
site.shutdown()
done()
