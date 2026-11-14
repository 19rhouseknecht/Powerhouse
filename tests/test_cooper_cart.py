"""Add all items to the Cooper cart: a job's materials with Cooper part numbers go to the office listener, which puts each in
the Cooper cart at its quantity (in Cooper's packages when it sells in them) and saves the cart as Lastname-MAT-JobNumber.
The site shows the run live, then what was added, what was not, and whether the cart was saved. Browser half: the listener
against a pretend Cooper (listing Add buttons, a product page's Add button, a cart page with a Save cart window)."""
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
    from werkzeug.security import generate_password_hash
    person = m.Client(first_name="Dana", last_name="Smith"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Basement wiring", job_number=1042); db.session.add(job); db.session.flush()
    wire = m.PriceItem(name="12/2 NM-B wire", lookup_key="122", unit="ft", price=0.52, cooper_part_number="122NMB250")
    breaker = m.PriceItem(name="20A breaker", lookup_key="qo120", unit="ea", price=9, cooper_part_number="QO120")
    db.session.add_all([wire, breaker, m.CompanySetting(key=m.LOCAL_COOPER_TOKEN_KEY, value=generate_password_hash("tok"))]); db.session.flush()
    order = m.JobMaterialOrder(job=job)
    order.items.extend([m.JobMaterialOrderItem(price_item=wire, name=wire.name, quantity=90, unit="ft", unit_price=0.52, position=0),
                        m.JobMaterialOrderItem(price_item=breaker, name=breaker.name, quantity=3, unit="ea", unit_price=9, position=1),
                        m.JobMaterialOrderItem(name="Customer-supplied fixture", quantity=2, unit="ea", unit_price=0, position=2)])
    db.session.add(order)
    db.session.add(m.Quote(job=job, status="confirmed", title="Basement wiring options"))
    db.session.commit()
    job_id, order_id = job.id, order.id
    check(m.cart_name_for(job) == "Smith-MAT-1042", "the cart is named Lastname-MAT-JobNumber: Smith-MAT-1042")
client = sign_in(m)
api = {"Authorization": "Bearer tok", "User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.9"}

# 1. the button and what it will do
page = client.get("/jobs/%d/materials" % job_id).get_data(as_text=True)
check("Add all items to the Cooper cart</button>" in page and "saved as <strong>Smith-MAT-1042</strong>" in page, "the materials page offers Add all items to the Cooper cart, with the cart's name")
check("Left out (no Cooper part number): Customer-supplied fixture" in page, "and says which items have no Cooper part number")

# 2. queued for the office listener
cart_url = [p.split('"')[0] for p in page.split('action="') if "cart" in p.split('"')[0]][0]
r = client.post(cart_url, data=CSRF)
check(r.status_code == 302 and r.headers["Location"].endswith("#cooper-cart"), "pressing it sends the cart to the office listener")
with app.app_context():
    order = db.session.get(m.JobMaterialOrder, order_id)
    check(order.cart_status == "queued" and order.cart_name == "Smith-MAT-1042" and len(order.cart_results) == 2, "the order waits for the listener, with its two Cooper items")
r = client.post(cart_url, data=CSRF, follow_redirects=True)
check("busy with another Cooper job" in r.get_data(as_text=True), "only one Cooper job at a time")

old = client.get("/api/local-cooper/commands/next", headers=dict(api, **{"User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.8"}))
check(old.json["command"] is None, "a listener older than 2.9 is refused")
page = client.get("/jobs/%d/materials" % job_id).get_data(as_text=True)
check("The Cooper cart was not filled" in page and "download v2.9" in page, "and the order says why")
client.post(cart_url, data=CSRF)
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
check(cmd["mode"] == "cart" and cmd["cart_name"] == "Smith-MAT-1042" and [(i["part_number"], i["quantity"], i["unit"]) for i in cmd["items"]] == [("122NMB250", 90, "ft"), ("QO120", 3, "ea")],
      "the listener gets the cart name and each Cooper part number at its quantity and unit")
page = client.get("/jobs/%d/materials" % job_id).get_data(as_text=True)
check("The office listener is filling the Cooper cart" in page and "data-cart-poll" in page, "the page shows it working, and follows it live")

# 3. the listener's report
post = lambda payload: client.post("/api/local-cooper/commands/%d/cart" % cmd["id"], json=payload, headers=api)
ids = [i["order_item_id"] for i in cmd["items"]]
post({"results": [{"order_item_id": ids[0], "status": "added", "message": "1 x 250 ft for 90 ft", "added_quantity": 1}], "message": "1 of 2 added"})
post({"results": [{"order_item_id": ids[1], "status": "error", "message": "No Add to cart button"}], "message": "2 of 2"})
post({"results": [], "finished": True, "saved": True, "message": "Cart saved as Smith-MAT-1042 (saved by the listener)."})
page = client.get("/jobs/%d/materials" % job_id).get_data(as_text=True)
check("Saved in the Cooper cart, with items to check" in page and "1 x 250 ft for 90 ft" in page and "No Add to cart button" in page,
      "afterwards: saved, with what was added (1 roll of 250 ft for 90 ft) and what was not")
with app.app_context():
    check(db.session.get(m.JobMaterialOrder, order_id).cart_status == "partial", "a saved cart with an item missing is 'check it'")
check("In the Cooper cart (check it)" in client.get("/jobs/%d" % job_id).get_data(as_text=True), "the job page says so too")
client.post(cart_url, data=CSRF)
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
client.post("/api/local-cooper/commands/%d/failed" % cmd["id"], json={"error": "Chrome closed"}, headers=api)
page = client.get("/jobs/%d/materials" % job_id).get_data(as_text=True)
check("The Cooper cart was not filled" in page and "Chrome closed" in page, "a run that fails says so on the order")
check(client.post("/api/local-cooper/commands/%d/cart" % cmd["id"], json={"finished": True}, headers=api).status_code == 409, "a finished run takes no more reports")

# 4. the listener against a pretend Cooper
try:
    from playwright.async_api import async_playwright
except ImportError:
    skip("Playwright is not installed (listener half)"); done()
from flask import Flask, request as req  # noqa: E402
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
shop = Flask("pretend_cooper_cart")
CART, SAVED = [], []
LISTING = {"122NMB250": ("(250 ft)", True), "QO120": ("/ea", False)}


@shop.route("/")
def home():
    return '<html><body><a href="/cart">Cart</a> Cooper</body></html>'


@shop.route("/product/search")
def search():
    part = req.args.get("q", "")
    if part not in LISTING:
        return '<html><body><div id="productSearchResults"><div class="product productCard" data-product-id="OTHER1"></div></div></body></html>'
    uom, has_button = LISTING[part]
    add = (f'<input type="number" name="qty" value="1"><button type="button" class="addToCart" onclick="fetch(\'/cart/add?part={part}&qty=\' + '
           f'this.previousElementSibling.value, {{method: \'POST\'}})">Add to Cart</button>') if has_button else ""
    return (f'<html><body><div id="productSearchResults"><div class="product productCard" data-product-id="{part}"><a href="/product/{part}/detail">{part}</a>'
            f'<span class="pricerPerQty" data-analytics-price="10"></span><small class="priceUOM">{uom}</small>{add}</div></div></body></html>')


@shop.route("/product/<part>/detail")
def detail(part):
    return (f'<html><body><h1>{part}</h1><label>Qty <input id="qty" type="number" value="1"></label>'
            f'<button onclick="fetch(\'/cart/add?part={part}&qty=\' + document.getElementById(\'qty\').value, {{method: \'POST\'}})">Add</button></body></html>')


@shop.route("/cart/add", methods=["POST"])
def cart_add():
    CART.append((req.args["part"], int(req.args["qty"])))
    return "ok"


@shop.route("/cart")
def cart_page():
    items = "".join(f"<li>{part} x {qty}</li>" for part, qty in CART) or "<p>Your cart is empty.</p>"
    return f'''<html><body><ul>{items}</ul><button onclick="document.getElementById('save').style.display='block'">Save Cart</button>
    <div id="save" role="dialog" style="display:none"><label>Cart name <input type="text" id="cartname"></label>
    <button onclick="fetch('/cart/save?name=' + encodeURIComponent(document.getElementById('cartname').value), {{method: 'POST'}}).then(() => document.body.append('Saved'))">Save</button></div></body></html>'''


@shop.route("/cart/save", methods=["POST"])
def cart_save():
    SAVED.append(req.args["name"]); return "ok"


@shop.route("/plain")
def plain():
    return '<html><body><a href="/cart-plain">Cart</a></body></html>'


@shop.route("/cart-plain")
def cart_plain():
    return "<html><body><p>Your cart is empty.</p><p>No way to save it here.</p></body></html>"


server = make_server("127.0.0.1", 0, shop, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
spec = importlib.util.spec_from_file_location("listener_cart", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
listener.log = lambda message: None
listener.COOPER_HOME = "http://127.0.0.1:%d/" % server.server_port
listener.BROWSER_CHANNEL = ""
listener.PROFILE_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="cart-profile-"))
listener.DEBUG_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="cart-debug-"))
listener.login_to_cooper = lambda page, config: asyncio.sleep(0)
exe = os.environ.get("PW_CHROMIUM") or None
if exe:
    os.environ["COOPER_BROWSER_PATH"] = exe
reports = []
command = {"cart_name": "Smith-MAT-1042", "items": [{"order_item_id": 1, "part_number": "122NMB250", "name": "wire", "quantity": 90, "unit": "ft"},
                                                    {"order_item_id": 2, "part_number": "QO120", "name": "breaker", "quantity": 3, "unit": "ea"},
                                                    {"order_item_id": 3, "part_number": "NOPE99", "name": "gone", "quantity": 1, "unit": "ea"}]}
try:
    summary = asyncio.run(listener.fill_cart({"cooper_username": "u", "cooper_password": "p"}, command,
                                             lambda results, message, finished, saved: reports.append((results, message, finished, saved)), headless=True))
except Exception as error:  # noqa: BLE001
    if "xecutable" in str(error):
        skip("no browser for the listener"); done()
    raise
check(sorted(CART) == [("122NMB250", 1), ("QO120", 3)], "the wire goes in as 1 roll of 250 ft (for 90 ft) and 3 breakers from the product page")
results = {row["order_item_id"]: row for results, *_ in reports for row in results}
check(results[1]["status"] == "added" and results[1]["message"] == "1 x 250 ft for 90 ft" and results[2]["status"] == "added", "each is reported added")
check(results[3]["status"] == "error" and "no exact match" in results[3]["message"], "a part Cooper doesn't have is reported, and the rest carry on")
check(SAVED == ["Smith-MAT-1042"] and summary["saved"] and reports[-1][2] and reports[-1][3], "the cart is saved as Smith-MAT-1042, and the site is told")
check("Cart saved as Smith-MAT-1042" in reports[-1][1], "with a sentence for the order page")


async def no_save_button():
    async with async_playwright() as playwright:
        browser = await (playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch())
        page = await browser.new_page()
        await page.goto(listener.COOPER_HOME + "plain")
        asked = []
        listener.HEADLESS = False
        listener.PROMPT = lambda prompt: asked.append(prompt) or ""
        saved, how = await listener.save_cart(page, "Smith-MAT-1042")
        listener.HEADLESS = True
        await page.goto(listener.COOPER_HOME + "plain")
        unattended = await listener.save_cart(page, "Smith-MAT-1042")
        await browser.close()
        return saved, how, asked, unattended


saved, how, asked, unattended = asyncio.run(no_save_button())
check(saved and how == "saved by hand at the office" and len(asked) == 1, "no Save cart button: the listener asks the person to save it as Smith-MAT-1042, then carries on")
check(not unattended[0] and "Save it in Cooper as Smith-MAT-1042" in unattended[1] and list(listener.DEBUG_DIRECTORY.glob("cart-save.html")),
      "with nobody to ask it says so, and keeps a copy of the cart page")
check(tuple(int(x) for x in listener.LISTENER_VERSION.split(".")) >= (2, 9), "the listener is v2.9 or newer (the cart needs 2.9)")
done()
