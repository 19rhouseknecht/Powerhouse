"""Add all items from Cooper in batches (5,000 new items a run unless changed; never a part number already on the sheet), the
listener pausing for a person when Cooper asks "are you human" (until Enter is pressed), and the live status window's review
button (hidden until there is something to review, then going somewhere)."""
import asyncio
import importlib.util
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, ROOT, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    db.session.add(m.PriceItem(name="200A panel", lookup_key="200a panel", unit="ea", price=410, cooper_part_number="QO130L200PG"))
    from werkzeug.security import generate_password_hash
    db.session.add(m.CompanySetting(key=m.LOCAL_COOPER_TOKEN_KEY, value=generate_password_hash("tok")))
    db.session.commit()
client = sign_in(m)
api = {"Authorization": "Bearer tok", "User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.3"}
JSON = {"Accept": "application/json"}


def product(n):
    return {"part_number": f"NEW{n:03d}", "name": f"Part {n}", "source_price": 2.5, "source_quantity": 1, "source_unit": "ea"}


# 1. the batch: 200 by default, the size typed, never past it
page = client.get("/quoting/pricing").get_data(as_text=True)
check('name="batch" value="5000"' in page, "the catalog button offers 5,000 new items a run")
client.post("/quoting/pricing/cooper-catalog", data=dict(CSRF, batch="3"), headers=JSON)
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
check(cmd["limit"] == 3 and "qo130l200pg" in cmd["known_parts"], "the listener is told the batch size and every part number already there")
known_again = {"part_number": "QO130L200PG", "name": "panel", "source_price": 389, "source_quantity": 1, "source_unit": "ea"}
r = client.post("/api/local-cooper/commands/%d/catalog" % cmd["id"], json={"products": [known_again, product(1), product(2)]}, headers=api)
check(r.json["added"] == 2 and not r.json["stop"], "a part number already on the sheet is never added; two new ones are")
r = client.post("/api/local-cooper/commands/%d/catalog" % cmd["id"], json={"products": [product(3), product(4), product(5)]}, headers=api)
check(r.json["added"] == 1 and r.json["stop"] is True and r.json["total_added"] == 3, "the batch fills at 3: one more is added and the listener is told to stop")
r = client.post("/api/local-cooper/commands/%d/catalog" % cmd["id"], json={"products": [product(6)], "finished": True}, headers=api)
check(r.status_code == 200 and r.json["added"] == 0 and r.json["stop"], "anything sent after the batch is full is not added")
status = client.get("/quoting/pricing/cooper-sync/%d/status" % cmd["id"]).json
check(status["status"] == "complete" and any("batch is full" in line for line in status["logs"]), "the run ends saying the batch is full and how to get the next one")
check(status["preview_url"].endswith("category=Unconfirmed") and status["preview_label"] == "See the items added", "its button goes to the items added")
with app.app_context():
    check(m.PriceItem.query.filter(m.PriceItem.cooper_part_number.like("NEW%")).count() == 3, "exactly 3 were added")
client.post("/quoting/pricing/cooper-catalog", data=CSRF, headers=JSON)
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
check(cmd["limit"] == 5000 and "new003" in cmd["known_parts"], "the next run is another 5,000, and knows the ones just added")
r = client.post("/api/local-cooper/commands/%d/catalog" % cmd["id"], json={"products": [product(n) for n in range(1, 8)], "finished": True}, headers=api)
check(r.json["added"] == 4, "it carries on with only the new ones (4 of 7)")
client.post("/quoting/pricing/cooper-catalog", data=dict(CSRF, batch="99999"), headers=JSON)
check(client.get("/api/local-cooper/commands/next", headers=api).json["command"]["limit"] == m.CATALOG_BATCH_MAX, "a batch is never more than the maximum")
with app.app_context():
    m.PriceSyncRun.query.update({"status": "cancelled"}); db.session.commit()

# 2. a price check's button
client.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="all"), headers=JSON)
with app.app_context():
    run = m.PriceSyncRun.query.order_by(m.PriceSyncRun.id.desc()).first(); batch_id = run.batch_id
status = client.get("/quoting/pricing/cooper-sync/%d/status" % batch_id).json
check(status["preview_label"] == "Review proposed prices" and status["preview_url"].endswith("/cooper-sync/%d" % batch_id), "a price check's button reviews its prices")
check("[hidden] { display: none !important; }" in open(os.path.join(ROOT, "static", "css", "style.css")).read(), "a hidden button stays hidden whatever its style")

try:
    from playwright.sync_api import sync_playwright
    from playwright.async_api import async_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()

import logging  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
exe = os.environ.get("PW_CHROMIUM") or None

# 3. the live window: the review button hidden while running, then it goes to the review
app.config["TESTING"] = False
with app.app_context():
    user = m.User(username="browser", role=m.Role.query.filter_by(is_admin=True).first()); user.set_password("a-long-password-1")
    db.session.add(user); db.session.commit()
site = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=site.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % site.server_port
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page()
    page.goto(BASE + "/login"); page.fill("input[name=username]", "browser"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(BASE + "/quoting/pricing")
    page.wait_for_timeout(1500)
    review = page.locator("[data-bot-preview]")
    check(page.locator("[data-cooper-bot]").is_visible() and not review.is_visible(), "while the check runs, the review button is not shown")
    with app.app_context():
        r = db.session.get(m.PriceSyncRun, run.id); r.status = "complete"; db.session.commit()
    review.wait_for(state="visible", timeout=5000)
    check(review.get_attribute("href").endswith("/cooper-sync/%d" % batch_id), "when it finishes, the button appears and has somewhere to go")
    review.click(); page.wait_for_load_state()
    check("/cooper-sync/%d" % batch_id in page.url, "and clicking it opens the review")
    browser.close()

# 4. "are you human": the listener pauses until a person finishes it and presses Enter
cooper = Flask("pretend_cooper_check")
SOLVED = {"yes": False}


@cooper.route("/check")
def check_page():
    if SOLVED["yes"]:
        return "<html><body><div class='product'>Products</div></body></html>"
    return """<html><body><h1>Verify you are human</h1><p>Complete the security check to continue.</p>
    <script>setInterval(() => fetch('/solved').then(r => r.text()).then(t => { if (t === 'yes') location.reload(); }), 200)</script></body></html>"""


@cooper.route("/framed")
def framed():
    return "<html><body><p>Cooper Electric</p><iframe src='/check'></iframe></body></html>"


@cooper.route("/solved")
def solved():
    return "yes" if SOLVED["yes"] else "no"


pretend = make_server("127.0.0.1", 0, cooper, threaded=True); threading.Thread(target=pretend.serve_forever, daemon=True).start()
spec = importlib.util.spec_from_file_location("listener_human", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
listener.SELF_CLEAR_SECONDS = 1
listener.log = lambda message: None
told, asked = [], []
listener.NOTIFY = told.append


def person(prompt):                        # the person finishes the check in Chrome, then presses Enter
    asked.append(prompt)
    time.sleep(0.5)
    SOLVED["yes"] = True
    time.sleep(0.6)
    return ""


async def run_check(prompt, headless=False):
    listener.PROMPT = prompt
    async with async_playwright() as playwright:
        browser = await (playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch())
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:%d/check" % pretend.server_port)
        started = time.monotonic()
        try:
            await listener.wait_for_verification(page, headless=headless)
            return time.monotonic() - started, await page.content()
        finally:
            await browser.close()


took, html = asyncio.run(run_check(person))
check(len(asked) == 1 and "press enter" in asked[0].lower(), "the listener stops and asks for Enter")
check("Products" in html, "and carries on once the check is done")
check(any("Paused" in line and "press Enter" in line for line in told) and any("carries on" in line for line in told), "the website's live status says it is paused, then carrying on")

SOLVED["yes"], asked[:] = False, []
tries = {"n": 0}


def impatient(prompt):                     # Enter pressed before finishing the check: it asks again
    tries["n"] += 1
    asked.append(prompt)
    if tries["n"] == 2:
        SOLVED["yes"] = True
        time.sleep(0.6)
    return ""


asyncio.run(run_check(impatient))
check(tries["n"] == 2, "Enter pressed too early: it asks again until the check is really done")

SOLVED["yes"] = False


def no_keyboard(prompt):                   # running with no keyboard: it watches the page instead
    threading.Timer(1.0, lambda: SOLVED.update(yes=True)).start()
    raise EOFError


took, html = asyncio.run(run_check(no_keyboard))
check("Products" in html, "with no keyboard it waits for the check to clear on the page")

SOLVED["yes"] = False
try:
    asyncio.run(run_check(person, headless=True))
    check(False, "headless cannot be answered")
except RuntimeError as error:
    check("without --headless" in str(error), "running headless, it says to run without --headless (a person can't answer a hidden window)")
SOLVED["yes"] = True
took, html = asyncio.run(run_check(person))
check(took < 1.5, "no check showing: no pause at all")

# the person pauses the scan themselves (Enter during a scan), and the check inside a frame is seen
SOLVED["yes"], asked[:] = True, []
listener.PAUSE_REQUESTED.set()
took, html = asyncio.run(run_check(person))
check(len(asked) == 1 and not listener.PAUSE_REQUESTED.is_set() and any("Paused at the office" in line for line in told), "Enter during a scan pauses it until Enter again")
check("SELF_CLEAR_SECONDS = 3 " in open(os.path.join(ROOT, "tools", "cooper_price_listener.py")).read(), "a check still showing after 3 seconds pauses for a person")


async def framed_check():
    async with async_playwright() as playwright:
        browser = await (playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch())
        page = await browser.new_page()
        SOLVED["yes"] = False
        await page.goto("http://127.0.0.1:%d/framed" % pretend.server_port)
        await page.wait_for_timeout(300)
        seen = await listener.verification_page_is_present(page)
        await browser.close()
        return seen


check(asyncio.run(framed_check()), "'Verify you are human' inside a frame is recognised")
SOLVED["yes"] = True

# 4b. the real keyboard: Enter during a scan asks for a pause; Enter while waiting carries on; no keyboard is EOFError
import subprocess  # noqa: E402
probe = f"""
import importlib.util, time
spec = importlib.util.spec_from_file_location("kb", {os.path.join(ROOT, "tools", "cooper_price_listener.py")!r})
kb = importlib.util.module_from_spec(spec); spec.loader.exec_module(kb)
kb.log = lambda message: None
kb.start_keyboard()
time.sleep(0.6)
print("PAUSE", kb.PAUSE_REQUESTED.is_set())
print("LINE", repr(kb.wait_for_enter("go? ")))
try:
    kb.wait_for_enter("again? ")
except EOFError:
    print("EOF")
"""
proc = subprocess.Popen([sys.executable, "-c", probe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
proc.stdin.write("\n"); proc.stdin.flush(); time.sleep(1.2)
proc.stdin.write("\n"); proc.stdin.flush(); time.sleep(0.3); proc.stdin.close()
out = proc.communicate(timeout=20)[0]
check("PAUSE True" in out, "pressing Enter during a scan asks for a pause")
check("LINE '\\n'" in out, "pressing Enter while it waits carries on")
check("EOF" in out, "with the keyboard gone it never hangs (it watches the page instead)")

# 5. the catalog walk stops when the site says the batch is full
spec = importlib.util.spec_from_file_location("listener_walk", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
walker = importlib.util.module_from_spec(spec); spec.loader.exec_module(walker)
walker.log = lambda message: None
walker.PROFILE_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="batch-profile-"))
shop = Flask("pretend_catalog_batch")
CATS = {f"US_3{i:02d}000000000/cat-{i}": [(f"P{i}{j}", f"Part {i}{j}", "1.00", "/ea") for j in range(3)] for i in range(1, 7)}
VISITED = []


@shop.route("/")
def shop_home():
    return "<html><body>" + "".join(f'<a href="/product/category/{c}">{c}</a>' for c in CATS) + "</body></html>"


@shop.route("/product/category/<code>/<slug>")
def shop_category(code, slug):
    VISITED.append(slug)
    cards = "".join(f'<div class="product productCard" data-product-id="{p}"><span data-analytics-description="{n}"></span>'
                    f'<span class="pricerPerQty" data-price="{pr}" data-analytics-price="{pr}"></span><small class="priceUOM">{u}</small></div>'
                    for p, n, pr, u in CATS[f"{code}/{slug}"])
    return f'<html><body><div id="productSearchResults">{cards}</div></body></html>'


shop_server = make_server("127.0.0.1", 0, shop, threaded=True); threading.Thread(target=shop_server.serve_forever, daemon=True).start()
walker.COOPER_HOME = "http://127.0.0.1:%d/" % shop_server.server_port
walker.BROWSER_CHANNEL = ""
if exe:
    os.environ["COOPER_BROWSER_PATH"] = exe
received = []


def site_reply(products, done_, total, message, finished):
    received.extend(products)
    return {"added": len(products), "total_added": len(received), "stop": len(received) >= 3}


walker.login_to_cooper = lambda page, config: asyncio.sleep(0)
asyncio.run(walker.crawl_catalog({"cooper_username": "u", "cooper_password": "p"}, set(), set(), site_reply, tabs=1, headless=True))
check(len(VISITED) < len(CATS), "when the site says the batch is full, the walk stops (it read %d of %d categories)" % (len(VISITED), len(CATS)))
done()
