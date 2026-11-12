"""A quote is always built. Whatever Caden sends back (a price nobody typed, a service that is gone, the wrong number
of tiers, two identical tiers, "not ready" with nothing to ask) the options are saved, and anything unpriced is listed at
the top of the draft with Caden's suggested price for the office to confirm. Only a question or an unreachable AI service
stops a build. Caden is replaced by a script here; nothing calls OpenAI."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
os.environ["OPENAI_API_KEY"] = "test-key-not-used"      # after make_site, which clears it; Caden is FakeOpenAI below
app, db = m.app, m.db
SCRIPT, SEEN = [], []


class FakeOpenAI:
    """Each call answers with the next scripted reply (a dict, or an exception to raise)."""
    def __init__(self, *args, **kwargs):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(kwargs)
        reply = SCRIPT.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return type("Reply", (), {"output_text": json.dumps(reply)})()


m.OpenAI = FakeOpenAI


def option(tier, title, bullets, items=(), services=(), direct=(), missing=()):
    return {"tier": tier, "title": title, "description": "What the homeowner gets.", "bullets": list(bullets),
            "services": list(services), "items": list(items), "direct_priced_items": list(direct), "missing_services": list(missing)}


def reply(options, ready=True, pricing=(), questions=(), message="Here are the options."):
    return {"ready": ready, "message": message, "questions": list(questions), "quote_title": "Lighting update — Service Options",
            "introduction": "A few ways to brighten the shop.", "mike_question": "", "options": options, "pricing_needed": list(pricing)}


with app.app_context():
    person = m.Client(first_name="Ann", last_name="Lee")
    db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Shop lighting", job_number=700, description="Replace old fluorescent fixtures in the shop.",
                address_line1="1 Main St", city="Lancaster", state="PA", postal_code="17601")
    ballast = m.PriceItem(name="LED retrofit kit", lookup_key="led retrofit kit", unit="ea", price=40, labor_hours_per_unit=.5)
    db.session.add_all([job, ballast]); db.session.commit()
    job_id, kit_id = job.id, ballast.id

client = sign_in(m)
client.get("/quoting/generate/%d" % job_id)
with app.app_context():
    quote = m.Quote.query.filter_by(job_id=job_id).first()
    quote.option_count = 3
    db.session.commit()
    quote_id = quote.id
    tiers = m.quote_tiers(3)


def send(text):
    return client.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message=text), follow_redirects=True).get_data(as_text=True)


def state():
    with app.app_context():
        q = db.session.get(m.Quote, quote_id)
        return {"options": [(o.tier, [(l.service_name, l.multiplier, l.is_missing) for l in o.lines]) for o in q.options],
                "messages": [msg.content for msg in q.messages], "missing": q.has_missing_pricing, "needed": q.pricing_needed}


# 1. the reported case: a "direct" price Caden made up (not typed by anyone) used to throw the whole quote away
tube = {"name": "Fluorescent/LED tube set (direct-priced)", "quantity": 6, "unit": "ea", "material_unit_price": 18.5,
        "labor_hours_per_unit": .25, "create_in_price_sheet": False, "source_message": "Replace old fluorescent fixtures in the shop."}
SCRIPT.append(reply([
    option(tiers[0], "Tube swap", ["Replace tubes"], direct=[tube]),
    option(tiers[1], "LED retrofit", ["Install LED retrofit kits"], items=[{"price_item_id": kit_id, "quantity": 6}]),
    option(tiers[2], "New fixtures", ["Install new LED fixtures"], items=[{"price_item_id": kit_id, "quantity": 6}],
           missing=[{"name": "4 ft LED wraparound fixture", "quantity": 6, "unit": "ea"}])],
    pricing=[{"name": "4 ft LED wraparound fixture", "unit": "ea", "suggested_material_price": 64, "suggested_labor_hours": .75, "reason": "Typical supply-house price."}]))
page = send("Quote the shop lighting")
st = state()
check(len(st["options"]) == 3, "a made-up price no longer stops the quote: all 3 options are built")
check(not any("couldn" in msg.lower() for msg in st["messages"]), "and Caden never says he couldn't")
check(("Fluorescent/LED tube set", 6.0, True) in st["options"][0][1], "the made-up price is not in the totals: the item is marked to price, qty 6 (and Caden's \"(direct-priced)\" label is gone)")
check(st["missing"], "so the draft cannot be confirmed until it is priced")
check(any("not priced from the Pricing Sheet yet" in msg and "only my guess" in msg for msg in st["messages"]), "Caden says which items are not priced from the Pricing Sheet, and that his figures are guesses")
check("not priced from the Pricing Sheet yet" in page, "the draft shows the Needs a price panel at the top")
check("Caden's guess: $18.50" in page and "Caden's guess: $64.00" in page and 'value="18.50"' not in page, "Caden's guesses are shown as guesses (18.50 and 64.00), never filled in")
check("Typical supply-house price." in page, "and his reason")

# 2. pricing them from the panel: only by matching to the Pricing Sheet. Typed prices and "Save" no longer do anything.
with app.app_context():
    rows = m.pricing_needed_rows(db.session.get(m.Quote, quote_id))
form = dict(CSRF, count=str(len(rows)))
for i, row in enumerate(rows):
    form.update({"key_%d" % i: row["key"], "name_%d" % i: row["name"], "price_%d" % i: "%.2f" % (row["material"] or 0), "labor_%d" % i: "%g" % (row["labor"] or 0),
                 "unit_%d" % i: row["unit"], "save_%d" % i: "1"})
client.post("/quotes/%d/use-prices" % quote_id, data=form)
st = state()
with app.app_context():
    check(st["missing"] and m.PriceItem.query.filter(m.PriceItem.category.in_(["Quote supplied", "Quote repair"])).count() == 0,
          "a typed price never prices an item, and never creates a Pricing Sheet item")
page = client.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check('name="price_0"' not in page and "Use these prices" not in page and "Find it on the Pricing Sheet" in page and "Add it there" in page,
      "the panel has no price boxes: match it (likely matches or a search), or add it on the Pricing Sheet")
wrap = next(i for i, row in enumerate(rows) if "wraparound" in row["name"])
r = client.post("/quoting/pricing/new", data=dict(CSRF, name="4 ft LED wraparound fixture", category="Lighting", unit="ea", price="64", labor_hours_per_unit=".75",
                                                   next="/quoting/generate/%d#needs-price" % job_id))
check(r.headers["Location"].endswith("/quoting/generate/%d#needs-price" % job_id), "adding it on the Pricing Sheet comes back to the quote")
with app.app_context():
    new_id = m.PriceItem.query.filter_by(name="4 ft LED wraparound fixture").one().id
found = client.get("/quoting/pricing/find.json?q=wraparound").get_json()["results"]
check(found and found[0]["id"] == str(new_id) and found[0]["kind"] == "Pricing Sheet", "the search finds it on the Pricing Sheet")
for i, row in enumerate(rows):
    form = dict(CSRF, **{"key_%d" % i: row["key"]}, pick="%d:%s" % (i, new_id if i == wrap else kit_id))
    client.post("/quotes/%d/use-sheet-item" % quote_id, data=form)
page = client.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
st = state()
check(not st["missing"] and "Confirm &amp; save" in page, "matched to Pricing Sheet items, every item is priced and the quote can be confirmed")
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    wrap_line = next(l for o in q.options for l in o.lines if l.price_item_id == new_id)
    check(wrap_line.material_unit_price == 64 and wrap_line.source_label == "Pricing Sheet", "at the Pricing Sheet's price, and the line says so")

# 3. a service that is gone, a missing tier, two identical tiers: built anyway, with notes
broken = reply([
    option(tiers[0], "Basic", ["Replace tubes"], services=[{"service_id": 9999, "multiplier": 1}]),
    option(tiers[0], "Basic again", ["Replace tubes"], services=[{"service_id": 9999, "multiplier": 1}])])
SCRIPT.extend([broken, broken])          # the app asks Caden to fix it once; he sends the same thing back
send("Try again with fewer options")
st = state()
check(len(st["options"]) == 3 and [t for t, _ in st["options"]] == tiers, "the wrong number of tiers still gives all 3, in order")
check(any("not in the library" in msg for msg in st["messages"]), "a service that is gone is marked to price, and said")
check(any("was not finished" in msg for msg in st["messages"]), "a tier Caden left out is a placeholder to finish, and said")
check(any("duplicates" in msg for msg in st["messages"]), "two identical tiers are kept and noted")

# 4. "not ready" with nothing to ask: Caden is told to build it, and does
SCRIPT.append(reply([], ready=False))
SCRIPT.append(reply([option(tiers[0], "A", ["Replace tubes"], items=[{"price_item_id": kit_id, "quantity": 1}]),
                     option(tiers[1], "B", ["Install LED retrofit kits"], items=[{"price_item_id": kit_id, "quantity": 3}]),
                     option(tiers[2], "C", ["Install LED retrofit kits throughout"], items=[{"price_item_id": kit_id, "quantity": 6}])]))
send("Just do it")
st = state()
check(len(SEEN[-1]["input"]) and "Build all the options now" in SEEN[-1]["input"][-1]["content"], "a dead-end answer is followed by 'build it now'")
check(len(st["options"]) == 3 and not st["missing"], "and the options are built")

# 5. a question is still allowed
SCRIPT.append(reply([], ready=False, questions=["Are the fixtures 4 ft or 8 ft?"], message="One quick question first."))
send("Rebuild for the back room")
st = state()
check(st["messages"][-1].endswith("1. Are the fixtures 4 ft or 8 ft?") and len(st["options"]) == 3, "Caden can still ask one question; the last draft stays")

# 6. a price typed in the chat does not price anything: the item is listed to match to the Pricing Sheet
typed = "Use 8 ft LED strips at $52.00 each with 0.5 hours each"
SCRIPT.append(reply([
    option(tiers[0], "A", ["Install LED strips"], direct=[{"name": "8 ft LED strip", "quantity": 4, "unit": "ea", "material_unit_price": 52, "labor_hours_per_unit": .5, "create_in_price_sheet": True, "source_message": typed}]),
    option(tiers[1], "B", ["Install LED kits"], items=[{"price_item_id": kit_id, "quantity": 2}]),
    option(tiers[2], "C", ["Install LED kits throughout"], items=[{"price_item_id": kit_id, "quantity": 8}])]))
send(typed)
st = state()
with app.app_context():
    check(("8 ft LED strip", 4.0, True) in st["options"][0][1] and m.PriceItem.query.filter(m.PriceItem.name.like("8 ft LED strip%")).count() == 0,
          "a price typed in the chat is not used: the item is listed to match, and no Pricing Sheet item is made")
    check("PRICES ONLY FROM THE PRICING SHEET:" in m.AiAgent.query.filter_by(name="Caden").one().prompt and "leave direct_priced_items empty" in SEEN[-1]["instructions"],
          "Caden is told every price comes from the Pricing Sheet")

# 6b. a restatement of something the option already has is not listed as needing a price;
#     an item the quote already covers can be marked "Not needed" and leaves the quote
SCRIPT.append(reply([
    option(tiers[0], "Dimmers", ["Install dimmers"], missing=[{"name": "LED-compatible dimmer switch (kitchen)", "quantity": 1, "unit": "ea"},
                                                              {"name": "Supply & install LED-compatible dimmer switches", "quantity": 2, "unit": "ea"},
                                                              {"name": "Ceiling access panel", "quantity": 1, "unit": "ea"}]),
    option(tiers[1], "B", ["Install LED kits"], items=[{"price_item_id": kit_id, "quantity": 4}]),
    option(tiers[2], "C", ["Install LED kits throughout"], items=[{"price_item_id": kit_id, "quantity": 9}])],
    pricing=[{"name": "LED-compatible dimmer switch (kitchen)", "unit": "ea", "suggested_material_price": 55, "suggested_labor_hours": .25, "reason": "Typical."}]))
send("Add dimmers")
with app.app_context():
    rows = m.pricing_needed_rows(db.session.get(m.Quote, quote_id))
names = [row["name"] for row in rows]
check(sorted(names) == ["Ceiling access panel", "LED-compatible dimmer switch (kitchen)"], "a restatement is not listed twice: %s" % names)
dimmer = names.index("LED-compatible dimmer switch (kitchen)")
client.post("/quotes/%d/use-sheet-item" % quote_id, data=dict(CSRF, **{"key_%d" % dimmer: rows[dimmer]["key"]}, pick="%d:%s" % (dimmer, kit_id)))
panel = 1 - dimmer
client.post("/quotes/%d/use-prices" % quote_id, data=dict(CSRF, count="2", **{"key_%d" % panel: rows[panel]["key"], "name_%d" % panel: rows[panel]["name"], "skip_%d" % panel: "1"}))
st = state()
check(not st["missing"] and not any(name == "Ceiling access panel" for _, lines in st["options"] for name, *_ in lines), "Not needed removes an item the quote already covers; the quote is ready")

# 7. only an unreachable AI service stops a build, and it says so plainly
SCRIPT.append(ConnectionError("network down"))
send("One more change")
st = state()
check("couldn’t reach the AI service" in st["messages"][-1] and len(st["options"]) == 3, "an AI outage says so; the draft is untouched")
done()
