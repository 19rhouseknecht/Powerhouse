"""Every quote price comes from the Pricing Sheet (Unconfirmed items included) or a Service: an item Caden names without
linking is found on the sheet and priced from it when the match is clear, offered with "Use this" when close, and Caden's
own figure is only a hint that is never used unless typed. Each line says where its price comes from, and an Unconfirmed
item used on a quote can be confirmed from the line. Nothing calls OpenAI."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
app, db = m.app, m.db
REPLY = {}


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        return SimpleNamespace(output_text=json.dumps(REPLY))


m.OpenAI = FakeOpenAI
with app.app_context():
    person = m.Client(first_name="Sam", last_name="Hale"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen receptacles", job_number=777, description="Update the kitchen receptacles.")
    db.session.add(job)
    gfci20 = m.PriceItem(name="20A GFCI receptacle, white", lookup_key="20a gfci receptacle, white", category="Devices", unit="ea", price=21.0, labor_hours_per_unit=0.4)
    tr15 = m.PriceItem(name="Leviton 15A tamper-resistant duplex receptacle", lookup_key="leviton tr15", category="Unconfirmed", unit="ea", price=1.85, cooper_part_number="TR15W")
    gfci15 = m.PriceItem(name="GFCI receptacle 15A white weather resistant", lookup_key="gfci15", category="Unconfirmed", unit="ea", price=18.0)
    db.session.add_all([gfci20, tr15, gfci15, m.PriceItem(name="Breaker", lookup_key="breaker", category="Panels", unit="ea", price=9)])
    db.session.commit()
    job_id, gfci20_id, tr15_id, gfci15_id = job.id, gfci20.id, tr15.id, gfci15.id
owner = sign_in(m)
owner.get("/quoting/generate/%d" % job_id)
with app.app_context():
    quote = m.Quote.query.filter_by(job_id=job_id).first(); quote.option_count = 3; db.session.commit(); quote_id = quote.id
    tiers = m.quote_tiers(3)


def option(tier, missing):
    return {"tier": tier, "title": "Kitchen receptacles", "description": "Safer outlets.", "bullets": ["Replacement of kitchen receptacles"],
            "services": [], "items": [], "direct_priced_items": [], "missing_services": missing}


missing = [{"name": "20A GFCI receptacle", "quantity": 4, "unit": "ea"}, {"name": "TR15W tamper-resistant receptacle", "quantity": 6, "unit": "ea"},
           {"name": "15A GFCI outlet", "quantity": 2, "unit": "ea"}, {"name": "Whole-home surge protector", "quantity": 1, "unit": "ea"}]
REPLY.update({"ready": True, "message": "Built.", "questions": [], "quote_title": "Kitchen", "introduction": "Hi.", "mike_question": "",
              "options": [option(t, missing) for t in tiers],
              "pricing_needed": [{"name": "Whole-home surge protector", "unit": "ea", "suggested_material_price": 250, "suggested_labor_hours": 1.5, "reason": "Typical price."},
                                 {"name": "20A GFCI receptacle", "unit": "ea", "suggested_material_price": 99, "suggested_labor_hours": 0, "reason": "Guess."}]})
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Update the kitchen receptacles."))


def lines():
    with app.app_context():
        q = db.session.get(m.Quote, quote_id)
        return [(l.service_name, l.price_item_id, l.multiplier, l.material_unit_price, l.source_label, l.is_missing) for l in q.options[0].lines]


first = lines()
by_item = {row[1]: row for row in first if row[1]}
check(gfci20_id in by_item and by_item[gfci20_id][3] == 21.0 and by_item[gfci20_id][2] == 4, "a clear match is priced from the Pricing Sheet (20A GFCI at $21, qty 4), not Caden's $99 guess")
check(by_item[gfci20_id][4] == "Pricing Sheet", "and the line says Pricing Sheet")
check(tr15_id in by_item and by_item[tr15_id][4] == "Pricing Sheet (Unconfirmed)", "an Unconfirmed item is used too (found by its Cooper part number TR15W), and says so")
pending = [row[0] for row in first if row[5]]
check("15A GFCI outlet" in pending and "Whole-home surge protector" in pending and len(pending) == 2, "a close-but-not-certain match and something not on the sheet stay to price")
with app.app_context():
    msgs = [msg.content for msg in db.session.get(m.Quote, quote_id).messages if msg.role == "assistant"]
check(any("Found on the Pricing Sheet and priced from it" in msg and "Unconfirmed" in msg for msg in msgs), "Caden's message says what came from the Pricing Sheet and which are Unconfirmed")
check(any("not priced from the Pricing Sheet yet" in msg and "only my guess" in msg for msg in msgs), "and that his own figures are only guesses")

page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check("2 items need a price from the Pricing Sheet" in page, "the panel counts only what is not priced from the sheet")
check("GFCI receptacle 15A white weather resistant" in page and "Use this" in page, "the close match is offered with Use this")
check("Caden's guess: $250.00" in page and 'value="250' not in page, "Caden's guess is a hint, never filled in")
check("20A GFCI receptacle, white" not in page.split("needs-price-list")[1].split("</form>")[0], "the item already priced from the sheet is not in the panel")
check(page.count('class="price-source src-unconfirmed"') >= 3 and "Confirm it" in page, "each line shows its source; Unconfirmed lines have Confirm it")

# Use this: only that item is priced, and only from the sheet
form = dict(CSRF, count="2")
rows = page.split('name="key_')
for index in range(2):
    key = page.split(f'name="key_{index}" value="')[1].split('"')[0].replace("&#39;", "'").replace("&amp;", "&")
    form[f"key_{index}"] = key
    form[f"price_{index}"] = ""
pick_index = [i for i in range(2) if "15a gfci" in form[f"key_{i}"]][0]
form["pick"] = f"{pick_index}:{gfci15_id}"
r = owner.post("/quotes/%d/use-sheet-item" % quote_id, data=form)
check(r.status_code == 302, "Use this saves")
after = lines()
check(any(row[1] == gfci15_id and row[3] == 18.0 for row in after), "the 15A GFCI is priced from the Pricing Sheet item ($18)")
check([row[0] for row in after if row[5]] == ["Whole-home surge protector"], "nothing else changed: the surge protector still needs a price (Caden's $250 was not used)")

# Confirm an Unconfirmed item from the quote
r = owner.post("/quoting/pricing/items/%d/confirm" % tr15_id, data=dict(CSRF, category="Devices", next="/quoting/generate/%d" % job_id))
check(r.status_code == 302 and r.headers["Location"].endswith("/quoting/generate/%d" % job_id), "Confirm it returns to the quote")
with app.app_context():
    item = db.session.get(m.PriceItem, tr15_id)
    check(item.category == "Devices" and "Confirmed by owner" in item.notes and item.price == 1.85, "the item is now a normal Pricing Sheet item in Devices, price unchanged")
check(any(row[1] == tr15_id and row[4] == "Pricing Sheet" for row in lines()), "and its quote line now says Pricing Sheet")
check(owner.post("/quoting/pricing/items/%d/confirm" % tr15_id, data=dict(CSRF, next="//evil.example")).headers["Location"].endswith("/quoting/pricing"), "a link off the site is never followed")

# a size that differs is never auto-matched; part numbers always are
with app.app_context():
    with app.test_request_context():
        best = m.sheet_matches("15A GFCI receptacle, white")
        check(not best or best[0][1].id != gfci20_id or best[0][0] < m.SHEET_AUTO_SCORE, "a 15A item never takes the 20A item's price automatically")
        check(m.sheet_matches("Cooper TR15W")[0] == (1.0, db.session.get(m.PriceItem, tr15_id)), "a Cooper part number matches its item exactly")

crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_pricing = False; role.view_quoting = True; db.session.commit()
check(crew.post("/quoting/pricing/items/%d/confirm" % gfci15_id, data=dict(CSRF, category="Devices")).status_code == 403, "confirming needs Pricing Sheet access")

# in a browser: search the Pricing Sheet from the panel and Use this
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
import threading  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
with app.app_context():
    db.session.add(m.PriceItem(name="Eaton whole-home surge protector CHSPT2ULTRA", lookup_key="spd", category="Panels", unit="ea", price=139, cooper_part_number="CHSPT2ULTRA"))
    db.session.commit()
app.config["TESTING"] = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page()
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/quoting/generate/%d" % job_id)
    card = page.locator(".needs-price-card", has_text="Whole-home surge protector")
    card.locator("[data-sheet-search] input").fill("CHSPT2")
    card.locator("[data-results] button", has_text="Use this").first.click()
    page.wait_for_load_state()
    check("Whole-home surge protector is priced from the Pricing Sheet" in page.content() or "is priced from the Pricing Sheet (Eaton" in page.content(),
          "searching by part number and pressing Use this prices it from the Pricing Sheet")
    browser.close()
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(not q.has_missing_pricing, "and nothing is left to price")
done()
