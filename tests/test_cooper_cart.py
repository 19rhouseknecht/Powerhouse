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
CART, SAVED, STASHED, MODE = {}, [], {}, {"plain": False}
LISTING = {"122NMB250": ("(250 ft)", True), "QO120": ("/ea", False), "GF20W": ("/ea", True)}
# Like Cooper: the header has a "Saved Carts" link before the cart icon (the old listener could follow it by mistake), the
# quantity box only counts once its change event fires (a filled box that never fires it stays at 1), the product page's Add
# for QO120 always adds 1 (fixed later on the cart page), and the cart is stashed through the real dropdown form.


def page(body):
    count = sum(CART.values())
    return (f'<html><body><header><a href="/cart/stashed">Saved Carts</a> <a href="/cart"><span class="icon icon-cart"></span>'
            f'<span class="cartCount">{count}</span> </a></header>{body}</body></html>')


@shop.route("/")
def home():
    return page("Cooper")


@shop.route("/product/search")
def search():
    part = req.args.get("q", "")
    if part not in LISTING:
        return page('<div id="productSearchResults"><div class="product productCard" data-product-id="OTHER1"></div></div>')
    uom, has_button = LISTING[part]
    add = (f'<input type="number" name="qty" value="1" onchange="this.dataset.q = this.value"><button type="button" class="addToCart" '
           f'onclick="setTimeout(() => fetch(\'/cart/add?part={part}&qty=\' + (this.previousElementSibling.dataset.q || 1), {{method: \'POST\'}})'
           f'.then(() => document.querySelector(\'.cartCount\').textContent = \'changed\'), 400)">Add to Cart</button>') if has_button else ""
    return page(f'<div id="productSearchResults"><div class="product productCard" data-product-id="{part}"><a href="/product/{part}/detail">{part}</a>'
                f'<span class="pricerPerQty" data-analytics-price="10"></span><small class="priceUOM">{uom}</small>{add}</div></div>')


@shop.route("/product/<part>/detail")
def detail(part):
    return page(f'<h1>{part}</h1><label>Qty <input id="qty" type="number" value="1"></label>'
                f'<button onclick="fetch(\'/cart/add?part={part}&qty=1\', {{method: \'POST\'}})">Add</button>')


@shop.route("/cart/add", methods=["POST"])
def cart_add():
    CART[req.args["part"]] = CART.get(req.args["part"], 0) + int(req.args["qty"])
    return "ok"


@shop.route("/cart/update", methods=["POST"])
def cart_update():
    for part, qty in req.form.items():
        CART[part] = int(float(qty))
    return "ok"


@shop.route("/cart")
def cart_page():
    if MODE["plain"]:
        return "<html><body><p>Your cart is empty.</p><p>No way to save it here.</p></body></html>"
    rows = "".join(f'<tr><td>Item <b>{part}</b></td><td><input class="form-control qty" name="{part}" value="{qty}"></td></tr>' for part, qty in CART.items())
    body = f'<table>{rows}</table><button id="upd" onclick="fetch(\'/cart/update\', {{method: \'POST\', body: new FormData(document.getElementById(\'f\'))}})">Update Cart</button>' if CART else "<p>Your cart is empty.</p>"
    body = body.replace("<table>", '<form id="f" onsubmit="return false"><table>').replace("</table>", "</table></form>")
    return page(body + """<div id="stash-cart-container"><a href="#" id="saveCartDropdown" onclick="document.getElementById('menu').style.display='block'; return false"
      class="btn btn-default">Stash Cart for Later</a><div id="menu" class="dropdown-menu" style="display:none"><form action="/shoppingcart/savecart" method="post">
      <label>Name This Cart</label><input hidden type="number" name="Id" value="59214055"><input placeholder="Enter Your Cart Name" class="form-control" type="text" id="CartName" name="CartName" value="">
      <button id="saveCartBtn" type="submit" class="btn btn-primary">Save</button></form>
      <i><a href="/cart/stashed">Click Here</a> to access your previously stashed carts.</i></div></div>""")


@shop.route("/shoppingcart/savecart", methods=["POST"])
def cart_save():
    SAVED.append(req.form["CartName"]); STASHED.update(CART); CART.clear()
    return '<html><head><meta http-equiv="refresh" content="0; url=/cart"></head><body>Saved</body></html>'


@shop.route("/cart/stashed")
def stashed():
    return page("<h1>Stashed carts</h1>" + "".join(f"<p>{name}</p>" for name in SAVED))


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
                                                    {"order_item_id": 4, "part_number": "GF20W", "name": "GFCI", "quantity": 7, "unit": "ea"},
                                                    {"order_item_id": 3, "part_number": "NOPE99", "name": "gone", "quantity": 1, "unit": "ea"}]}
try:
    summary = asyncio.run(listener.fill_cart({"cooper_username": "u", "cooper_password": "p"}, command,
                                             lambda results, message, finished, saved: reports.append((results, message, finished, saved)), headless=True))
except Exception as error:  # noqa: BLE001
    if "xecutable" in str(error):
        skip("no browser for the listener"); done()
    raise
check(SAVED == ["Smith-MAT-1042"], "the cart is stashed through Cooper's own Stash Cart for Later form, as Smith-MAT-1042 (never the Saved Carts link)")
check(STASHED == {"122NMB250": 1, "QO120": 3, "GF20W": 7}, "every quantity is right: 7 GFCIs typed on the listing (the box's change fires), 1 roll of 250 ft for 90 ft, "
      "and the breakers the product page added as 1 are set to 3 on the cart page (%s)" % STASHED)
check(any("QO120: was 1 in the cart, set to 3" in message for _, message, _, _ in reports), "the cart check says what it fixed")
results = {row["order_item_id"]: row for results, *_ in reports for row in results}
check(results[1]["status"] == "added" and results[1]["message"] == "1 x 250 ft for 90 ft" and results[2]["status"] == "added", "each is reported added")
check(results[3]["status"] == "error" and "no exact match" in results[3]["message"], "a part Cooper doesn't have is reported, and the rest carry on")
check(summary["saved"] and reports[-1][2] and reports[-1][3] and "stashed by the listener" in reports[-1][1], "the site is told it was stashed")
check("Cart saved as Smith-MAT-1042" in reports[-1][1], "with a sentence for the order page")


async def no_save_button():
    async with async_playwright() as playwright:
        browser = await (playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch())
        page = await browser.new_page()
        MODE["plain"] = True
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
check(tuple(int(x) for x in listener.LISTENER_VERSION.split(".")) >= (2, 10), "the listener is v2.10 or newer")
done()
