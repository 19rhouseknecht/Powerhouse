"""Add all items from Cooper: the website side (Unconfirmed items, resuming, the Pricing Sheet staying usable, Caden seeing
only relevant Unconfirmed items and stopping on its own if a quote gets too big) and the listener walking a pretend
Cooper catalog (browser part skipped without a browser)."""
import importlib.util
import json
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
    panel = m.PriceItem(name="200A panel", lookup_key="200a panel", unit="ea", price=410, cooper_part_number="QO130L200PG")
    nuts = m.PriceItem(name="Wire nuts", lookup_key="wire nuts", unit="box", price=9)
    nuts.supplier_parts.append(m.SupplierPartNumber(supplier="Grainger", part_number="WN-100"))
    db.session.add_all([panel, nuts])
    from werkzeug.security import generate_password_hash
    db.session.add(m.CompanySetting(key=m.LOCAL_COOPER_TOKEN_KEY, value=generate_password_hash("tok")))
    db.session.commit()
    panel_id = panel.id
client = sign_in(m)
api = {"Authorization": "Bearer tok", "User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.1"}

r = client.post("/quoting/pricing/cooper-catalog", data=CSRF, headers={"Accept": "application/json"})
check(r.status_code == 200 and r.json["batch_id"], "Add all items from Cooper queues a catalog run")
check(client.post("/quoting/pricing/cooper-catalog", data=CSRF).status_code == 409, "only one Cooper job at a time")
old = client.get("/api/local-cooper/commands/next", headers=dict(api, **{"User-Agent": "PowerHouseFlow-LocalCooperUpdater/2.0"}))
check(old.json["command"] is None, "a listener too old for the catalog is refused, and the run says why")
with app.app_context():
    run = m.PriceSyncRun.query.filter_by(mode="catalog").first()
    check(run.status == "failed" and "v2.1" in run.error_message, "(download v2.1)")
r = client.post("/quoting/pricing/cooper-catalog", data=CSRF, headers={"Accept": "application/json"})
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
batch = cmd["id"]
check(cmd["mode"] == "catalog" and "qo130l200pg" in cmd["known_parts"] and "wn100" in cmd["known_parts"], "the listener is told every part number already on the sheet")

products = [
    {"part_number": "QO130L200PG", "name": "Square D panel", "source_price": 389, "source_quantity": 1, "source_unit": "ea", "category": "panels"},
    {"part_number": "wn-100", "name": "Wire nut", "source_price": 9, "source_quantity": 1, "source_unit": "ea"},
    {"part_number": "122NMB250", "name": "Romex 12/2 NM-B 250 ft", "source_price": 129.40, "source_quantity": 250, "source_unit": "ft", "category": "building wire"},
    {"part_number": "GF20W", "name": "200A panel", "source_price": 21.15, "source_quantity": 1, "source_unit": "ea", "category": "receptacles"},
    {"part_number": "BAD1", "name": "No price", "source_price": "n/a", "source_quantity": 1, "source_unit": "ea"},
]
r = client.post("/api/local-cooper/commands/%d/catalog" % batch, json={"products": products, "categories_done": ["https://cooper/cat/a"], "categories_total": 4, "message": "1 of 4 categories read."}, headers=api)
check(r.status_code == 201 and r.json["added"] == 2, "only new part numbers with a usable price are added (2 of 5)")
with app.app_context():
    romex = m.PriceItem.query.filter_by(cooper_part_number="122NMB250").one()
    check(romex.category == "Unconfirmed" and romex.unit == "ft" and abs(romex.price - 0.5176) < 1e-6, "as Unconfirmed, priced per unit ($129.40 / 250 ft)")
    check(romex.supplier_parts[0].supplier == "Cooper Electric" and "Cooper's catalog" in romex.notes, "with Cooper as the supplier and a note")
    clash = m.PriceItem.query.filter_by(cooper_part_number="GF20W").one()
    check(clash.name == "200A panel (GF20W)", "a name already on the sheet gets the part number added, never overwriting")
    check(m.db.session.get(m.PriceItem, panel_id).price == 410, "nothing already on the sheet changes")
r = client.post("/api/local-cooper/commands/%d/catalog" % batch, json={"products": products, "categories_done": ["https://cooper/cat/b"], "finished": True}, headers=api)
check(r.json["added"] == 0, "sending the same products again adds nothing")
with app.app_context():
    check(m.catalog_done() == ["https://cooper/cat/a", "https://cooper/cat/b"] and m.PriceSyncRun.query.filter_by(batch_id=batch).one().status == "complete", "finished categories are remembered and the run completes")
r = client.post("/quoting/pricing/cooper-catalog", data=CSRF, headers={"Accept": "application/json"})
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
check(cmd["skip_categories"] == ["https://cooper/cat/a", "https://cooper/cat/b"], "the next run carries on where the last one stopped")
client.post("/api/local-cooper/commands/%d/catalog" % cmd["id"], json={"products": [], "finished": True}, headers=api)
r = client.post("/quoting/pricing/cooper-catalog", data=dict(CSRF, restart="1"), headers={"Accept": "application/json"})
cmd = client.get("/api/local-cooper/commands/next", headers=api).json["command"]
check(cmd["skip_categories"] == [], "Start over reads every category again")
client.post("/api/local-cooper/commands/%d/catalog" % cmd["id"], json={"products": [], "finished": True}, headers=api)

# the Pricing Sheet stays usable with thousands of catalog items
with app.app_context():
    for i in range(300):
        name = f"Catalog fitting {i:03d}"
        db.session.add(m.PriceItem(name=name, lookup_key=name.lower(), category="Unconfirmed", unit="ea", price=1, cooper_part_number=f"FIT{i:03d}"))
    db.session.commit()
page = client.get("/quoting/pricing").get_data(as_text=True)
import re as _re
_note = _re.search(r'<p class="unconfirmed-note">(.*?)</p>', page)
check("Catalog fitting" not in page and _note is not None and _note.group(1).startswith("302 Unconfirmed items from Cooper's catalog are hidden here."), "Unconfirmed items are hidden from the sheet by default, with a link to them: %s" % (_note.group(1)[:90] if _note else "no note"))
page = client.get("/quoting/pricing?category=Unconfirmed").get_data(as_text=True)
check(page.count("data-select-item") == 250 and "page 1 of 2" in page, "viewing them shows 250 at a time")
page = client.get("/quoting/pricing?category=Unconfirmed&page=2").get_data(as_text=True)
check(page.count("data-select-item") == 52, "page 2 has the rest")
check("Romex 12/2" in client.get("/quoting/pricing?q=romex").get_data(as_text=True), "a search finds Unconfirmed items too")
with app.app_context():
    romex_id = m.PriceItem.query.filter_by(cooper_part_number="122NMB250").one().id
client.post("/quoting/pricing/move-selected", data=dict(CSRF, item_ids=str(romex_id), category="Wire"))
with app.app_context():
    check(db.session.get(m.PriceItem, romex_id).category == "Wire", "Move to category confirms items in bulk")

# Caden: confirmed items always, Unconfirmed only when they match the job; stops by itself if a quote gets too big
os.environ["OPENAI_API_KEY"] = "test"
SENT, FAIL_ONCE = [], [False]


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        SENT.append(kwargs["instructions"])
        if FAIL_ONCE[0] and '"unconfirmed": true' in kwargs["instructions"]:
            FAIL_ONCE[0] = False
            raise RuntimeError("This model's maximum context length is 400000 tokens")
        return type("R", (), {"output_text": json.dumps({"ready": False, "message": "One question.", "questions": ["Which room?"], "quote_title": "", "introduction": "",
                                                         "mike_question": "", "options": [], "pricing_needed": []})})()


m.OpenAI = FakeOpenAI
with app.app_context():
    person = m.Client(first_name="A", last_name="B"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen fitting repair", job_number=900, description="Replace a catalog fitting under the sink")
    db.session.add(job); db.session.flush()
    quote = m.Quote(job=job, option_count=3); db.session.add(quote); db.session.commit()
    m.build_ai_quote_response(job, quote)
    sent = SENT[-1]
    check('"name": "200A panel"' in sent and sent.count('"unconfirmed": true') == 60, "Caden sees every confirmed item and only 60 matching Unconfirmed ones")
    FAIL_ONCE[0] = True
    result = m.build_ai_quote_response(job, quote)
    check('"unconfirmed": true' not in SENT[-1] and not m.caden_uses_unconfirmed(), "a quote too big with Unconfirmed items is rebuilt without them, and they are switched off")
    check("switched Unconfirmed items off" in result["message"], "and Caden says so")
page = client.get("/quoting/pricing").get_data(as_text=True)
check("Caden ignores Unconfirmed" in page and "Let Caden use them" in page, "the Pricing Sheet shows the switch is off, with a way back on")
client.post("/quoting/pricing/unconfirmed-switch", data=dict(CSRF, on="1"))
with app.app_context():
    check(m.caden_uses_unconfirmed(), "and it can be switched back on")

# ---------------------------------------------------------------- the listener walking a pretend catalog
try:
    import playwright.async_api  # noqa: F401
except ImportError:
    skip("Playwright is not installed: the catalog crawl was not driven"); done()
from flask import Flask, request, make_response, redirect  # noqa: E402
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
cooper = Flask("pretend_cooper_catalog")
CATEGORIES = {"US_200000000000/wire": None, "US_200100000000/building-wire": [("122NMB250", "Romex 12/2 250 ft", "129.40", "(250 ft)"), ("142NMB250", "Romex 14/2 250 ft", "99.10", "(250 ft)"),
                                                                         ("QO130L200PG", "Square D panel", "389.00", "/ea")],
              "US_300100000000/receptacles": [("GF20W", "20A GFCI receptacle", "21.15", "/ea"), ("TR15W", "15A tamper-resistant receptacle", "1.85", "/ea"),
                                              ("WR20W", "20A weather-resistant receptacle", "6.40", "/ea"), ("USB20", "USB receptacle", "24.00", "/ea")]}
PAGE = 2
SEEN_PAGES = []


@cooper.route("/")
def home():
    links = "".join(f'<a href="/product/category/{c}">{c}</a>' for c in CATEGORIES)
    return f'<html><body><button onclick="this.remove()">Accept All</button><a href="/logout">Log Out</a>{links}</body></html>'


@cooper.route("/product/category/<code>/<slug>")
def category(code, slug):
    key = f"{code}/{slug}"
    SEEN_PAGES.append(key)
    rows = CATEGORIES.get(key) or []
    page_number = int(request.args.get("page", 1))
    chunk = rows[(page_number - 1) * PAGE: page_number * PAGE]
    cards = "".join(f'<div class="product productCard" data-product-id="{p}"><span data-analytics-description="{n}"></span><span class="pricerPerQty" data-price="{pr}"></span><small class="priceUOM">{u}</small></div>' for p, n, pr, u in chunk)
    nxt = f'<ul class="pagination"><li class="next"><a href="?page={page_number + 1}">Next</a></li></ul>' if page_number * PAGE < len(rows) else ""
    return f"""<html><body><div id="productSearchResults">{cards}</div>{nxt}
    <script>setTimeout(() => document.querySelectorAll('.pricerPerQty').forEach(e => e.setAttribute('data-analytics-price', e.dataset.price)), 300)</script></body></html>"""


server = make_server("127.0.0.1", 0, cooper, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["COOPER_HOME"] = "http://127.0.0.1:%d/" % server.server_port
os.environ["COOPER_BROWSER_CHANNEL"] = ""
if os.environ.get("PW_CHROMIUM"):
    os.environ["COOPER_BROWSER_PATH"] = os.environ["PW_CHROMIUM"]
spec = importlib.util.spec_from_file_location("listener_catalog", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
listener.PROFILE_DIRECTORY = __import__("pathlib").Path(tempfile.mkdtemp(prefix="catalog-profile-"))
check(tuple(int(x) for x in listener.LISTENER_VERSION.split(".")) >= (2, 1), "the listener is v2.1 or newer (the catalog needs 2.1)")
check(listener.leaf_categories(["https://c/product/category/US_200000000000/wire", "https://c/product/category/US_200100000000/building-wire"]) ==
      ["https://c/product/category/US_200100000000/building-wire"], "only the most specific categories are read (a parent repeats its children)")
batches = []
import asyncio  # noqa: E402
try:
    totals = asyncio.run(listener.crawl_catalog({"cooper_username": "u", "cooper_password": "p"}, {"QO130L200PG"}, set(),
                                                lambda products, done_, total, message, finished: batches.append((products, done_, total, message, finished)),
                                                tabs=2, headless=True))
except Exception as error:  # noqa: BLE001
    if "xecutable" in str(error):
        skip("no browser for the catalog crawl"); done()
    raise
sent = [p for products, *_ in batches for p in products]
check(sorted(p["part_number"] for p in sent) == ["122NMB250", "142NMB250", "GF20W", "TR15W", "USB20", "WR20W"], "every unknown product is sent, across every page of every category")
check(not any(p["part_number"] == "QO130L200PG" for p in sent), "a part number already on the sheet is not sent")
check(next(p for p in sent if p["part_number"] == "122NMB250")["source_quantity"] == 250, "package sizes are read")
check(SEEN_PAGES.count("US_300100000000/receptacles") == 2 and "US_200000000000/wire" not in SEEN_PAGES, "it follows Next to page 2, and skips the parent category")
check(batches[-1][4] is True and sorted(u.split("category/")[1] for products, done_, *_ in batches for u in done_) == ["US_200100000000/building-wire", "US_300100000000/receptacles"],
      "each finished category is reported, and the last batch says the run is finished")
SEEN_PAGES.clear(); batches.clear()
asyncio.run(listener.crawl_catalog({"cooper_username": "u", "cooper_password": "p"}, set(), {"http://127.0.0.1:%d/product/category/US_300100000000/receptacles" % server.server_port},
                                   lambda *a: batches.append(a), tabs=1, headless=True))
check("US_300100000000/receptacles" not in SEEN_PAGES and SEEN_PAGES, "a finished category is skipped when the run carries on")
server.shutdown()
done()
