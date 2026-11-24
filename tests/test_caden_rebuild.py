"""Caden, rebuilt (1.43.0): a quote is worked out in steps. The estimating electrician lists every part each tier needs
(wiring method from the location, a connector at every box); the app finds those parts on the Pricing Sheet; Caden prices
from the matches (not a dump of the whole sheet); every client line that reads wrong is rewritten by the model; Mike's labor
times are filled in right after. A lighting room picks NM, MC or UF and adds a connector at every light. Nothing calls OpenAI."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ.pop("OPENAI_QUOTE_MODEL", None)
app, db = m.app, m.db
L = m.lighting

# 1. client wording never comes out mangled
b = m.professionalize_scope_bullet
for raw, want in [("Add and install new lights", "Turnkey installation of new lights"),
                  ("Turnkey installation of and install lights", "Turnkey installation of lights"),
                  ("Supply, install and wire dimmer switch", "Turnkey installation of dimmer switch"),
                  ("Install and wire 12 recessed lights", "Turnkey installation of 12 recessed lights"),
                  ("Install lights", "Turnkey installation of lights")]:
    got = b(raw)
    check(got == want and not m.wording_problem(got), "%r reads %r (got %r)" % (raw, want, got))
check(m.wording_problem("Turnkey installation of and install lights") and m.wording_problem("Kitchen kitchen lights") and not m.wording_problem("New dimmer for the kitchen lights"),
      "a line that reads wrong is caught (\"of and\", doubled words), a good one is not")

# 2. the wiring method follows the room
check(m.lighting_wiring(L.read_room({"type": "kitchen", "length": 14, "width": 12, "height": 9}))["method"] == "NM", "a home's kitchen: NM-B (Romex)")
check(m.lighting_wiring(L.read_room({"type": "exterior", "length": 20, "width": 10, "height": 9}))["method"] == "UF", "a porch or outdoors: UF-B, never Romex")
check(m.lighting_wiring(L.read_room({"type": "commercial_office", "length": 30, "width": 20, "height": 9}))["method"] == "MC", "a commercial office: MC cable")
check("UF-B" in m.ELECTRICAL_KNOWLEDGE and "Romex connector" in m.ELECTRICAL_KNOWLEDGE and "MC connector" in m.ELECTRICAL_KNOWLEDGE,
      "the estimator knows wiring methods and the connector at every box")

with app.app_context():
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen lights", job_number=990, description="Add 6 recessed lights in the kitchen on a dimmer.",
                address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    items = {
        "wafer": m.PriceItem(name="6in LED wafer light", lookup_key="wafer", unit="ea", price=24, labor_hours_per_unit=0.5),
        "romex": m.PriceItem(name="14/2 NM-B Romex", lookup_key="romex", unit="ft", price=0.6, labor_hours_per_unit=0.02),
        "conn": m.PriceItem(name="3/8 NM cable connector (Romex connector)", lookup_key="conn", unit="ea", price=0.4),
        "dimmer": m.PriceItem(name="Single pole LED dimmer", lookup_key="dim", unit="ea", price=28, labor_hours_per_unit=0.4),
        "uf": m.PriceItem(name="14/2 UF-B cable", lookup_key="uf", unit="ft", price=0.9),
        "ufconn": m.PriceItem(name="1/2 watertight UF connector", lookup_key="ufconn", unit="ea", price=2.5),
        "surge": m.PriceItem(name="Whole home surge protector", lookup_key="surge", unit="ea", price=180),
    }
    db.session.add_all(items.values()); db.session.commit()
    ids = {k: v.id for k, v in items.items()}
    job_id = job.id

# 3. the steps, with a scripted model
CALLS = []
REVIEWS = [{"approved": False, "summary": "Gold was missing the Romex connectors.", "findings": [
    {"tier": "Gold", "severity": "fix", "problem": "No cable connectors at the lights.", "fix": "Add 3/8 NM cable connectors, one per light and the switch."}]},
    {"approved": True, "summary": "Complete and to code now.", "findings": []}]
SCOPE = {"site": "Finished single-family kitchen, attic above, dry interior.", "assumptions": ["Attic access over the kitchen."],
         "tiers": [{"tier": t, "intent": "Six dimmable recessed lights.", "components": [
             {"name": "LED wafer light", "spec": "6in LED wafer light", "quantity": 6, "unit": "ea", "why": "The lights asked for.", "search": "6in LED wafer"},
             {"name": "Cable", "spec": "14/2 NM-B", "quantity": 60, "unit": "ft", "why": "Dry interior of a home: Romex.", "search": "14/2 NM-B"},
             {"name": "Romex connector", "spec": "3/8 NM cable connector", "quantity": 7, "unit": "ea", "why": "One at every light and the switch box.", "search": "NM cable connector"},
             {"name": "Dimmer", "spec": "single pole LED dimmer", "quantity": 1, "unit": "ea", "why": "Asked for.", "search": "LED dimmer"}]}
             for t in m.quote_tiers(3)]}


def plan_reply(tiers):
    opts = []
    for n, t in enumerate(tiers):
        opts.append({"tier": t, "title": "Kitchen lighting" if n == 0 else "Kitchen lighting plus %d" % n, "description": "Bright, dimmable light.",
                     "bullets": ["Wire up the lights of and switch", "Dimmer for the new lights"] + (["Extra light %d" % n] if n else []),
                     "services": [], "items": [{"price_item_id": ids["wafer"], "quantity": 6 + n}, {"price_item_id": ids["romex"], "quantity": 60},
                                               {"price_item_id": ids["conn"], "quantity": 7 + n}, {"price_item_id": ids["dimmer"], "quantity": 1}],
                     "direct_priced_items": [], "missing_services": []})
    return {"ready": True, "message": "Built from the scope.", "questions": [], "quote_title": "Kitchen lighting", "introduction": "Hi.",
            "mike_question": "", "options": opts, "pricing_needed": []}


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        CALLS.append(kwargs)
        name = kwargs.get("text", {}).get("format", {}).get("name")
        if kwargs.get("model") == "gpt-5" and os.environ.get("NO_GPT5"):
            raise RuntimeError("The model `gpt-5` does not exist or you do not have access to it.")
        if name == "powerhouse_job_scope":
            out = SCOPE
        elif name == "powerhouse_quote_plan":
            out = plan_reply(json.loads(kwargs["instructions"].split("REQUIRED_TIERS_IN_ORDER: ")[1].split("\n")[0]))
            if "PEER REVIEW FROM CALEB" in json.dumps(kwargs.get("input")):
                out["options"][1]["bullets"].append("Cable connectors at every light")
        elif name == "powerhouse_peer_review":
            out = REVIEWS.pop(0) if REVIEWS else {"approved": True, "summary": "Looks right.", "findings": []}
        elif name == "powerhouse_client_wording":
            opts = json.loads(kwargs["input"])
            out = {"options": [dict(o, bullets=["New wiring for the lights and switch" if "of and" in x else x for x in o["bullets"]]) for o in opts]}
        elif name == "labor_suggestions":
            out = {"suggestions": [{"price_item_id": ids["conn"], "hours_per_unit": 0.05, "reason": "A connector takes a few minutes."}]}
        else:
            out = {}
        return type("Reply", (), {"output_text": json.dumps(out)})()


m.OpenAI = FakeOpenAI
owner = sign_in(m)
url = "/quoting/generate/%d" % job_id
owner.get(url)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3; db.session.commit(); quote_id = q.id
owner.post(url, data=dict(CSRF, message="Add 6 recessed lights in the kitchen on a dimmer."))
names = [c.get("text", {}).get("format", {}).get("name") for c in CALLS]
check(names[0] == "powerhouse_job_scope" and "powerhouse_quote_plan" in names and names.index("powerhouse_quote_plan") > 0,
      "the scope is worked out first, then priced (%s)" % names)
scope_call = CALLS[0]
check(scope_call["model"] == "gpt-5" and scope_call.get("reasoning", {}).get("effort") == "medium", "quotes are built by a reasoning model")
check("Romex connector" in scope_call["instructions"] and "UF-B" in scope_call["instructions"], "the scope step is given the electrical knowledge")
plan = next(c for c in CALLS if c.get("text", {}).get("format", {}).get("name") == "powerhouse_quote_plan")
check("JOB_SCOPE" in plan["instructions"] and "CATALOG_MATCHES" in plan["instructions"] and '"price_item_id": %d' % ids["conn"] in plan["instructions"],
      "Caden prices from the scope and the Pricing Sheet items that match each part")
library = plan["instructions"].split("PRICE_LIBRARY")[1].split("MIKE_LABOR_ADVISOR")[0]
check("Whole home surge protector" not in library and "6in LED wafer light" in library, "not a dump of the whole Pricing Sheet: only what matched the scope")
check("powerhouse_client_wording" in names, "a line that read wrong was sent back to be rewritten")
review_calls = [c for c in CALLS if c.get("text", {}).get("format", {}).get("name") == "powerhouse_peer_review"]
check(len(review_calls) == 2 and "Caden" in review_calls[0]["instructions"] and "Romex" in review_calls[0]["instructions"]
      and "6in LED wafer light" in review_calls[0]["input"], "Caleb peer-reviews every priced line of the draft before anyone sees it")
plans = [c for c in CALLS if c.get("text", {}).get("format", {}).get("name") == "powerhouse_quote_plan"]
check(any("No cable connectors at the lights." in json.dumps(c.get("input")) for c in plans), "Caden gets Caleb's findings and rebuilds")
check(names.index("powerhouse_peer_review") < names.index("powerhouse_client_wording"), "the review comes before the wording pass")
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    bullets = [x for o in q.options for x in o.bullets]
    check(len(q.options) == 3 and "New wiring for the lights and switch" in bullets and not any("of and" in x for x in bullets),
          "and the quote has the rewritten line, never the mangled one")
    check(q.scope and q.scope["site"].startswith("Finished single-family kitchen") and len(q.scope["tiers"][0]["components"]) == 4,
          "how Caden worked it out is kept on the quote")
    check(q.labor_suggestions.get(str(ids["conn"]), {}).get("hours") == 0.05, "Mike's time for the item with 0 hr is filled in right after the build")
    check(q.review and q.review["approved"] and q.review["fixed"] == ["Gold: No cable connectors at the lights."] and q.review["rounds"] == 2,
          "the review is kept: approved after Caden fixed what Caleb found")
    check("Cable connectors at every light" in q.options[1].bullets, "and the quote is the revised one")
    check("Peer review (Caleb): Caleb reviewed the draft and Caden fixed 1 point(s)" in q.messages[-1].content or any("Peer review (Caleb)" in x.content for x in q.messages if x.role == "assistant"),
          "Caden's message says Caleb reviewed it")
page = owner.get(url).get_data(as_text=True)
check("✓ Caleb reviewed it" in page and "Fixed before you saw it (1)" in page and "Complete and to code now." in page, "the draft shows Caleb's peer review")
check("How Caden worked it out" in page and "One at every light and the switch box." in page and "What Caden assumed about the site" in page,
      "the draft shows the parts list and why")
check('value="0.05"' in page and "A connector takes a few minutes." in page, "and Mike's time is ready to Accept")

# 4. an account without the reasoning model still builds (on OPENAI_MODEL)
CALLS.clear(); os.environ["NO_GPT5"] = "1"
owner.post(url, data=dict(CSRF, message="Make it 8 lights instead."))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(any(c.get("model") == os.environ.get("OPENAI_MODEL", "gpt-5-mini") and "reasoning" not in c for c in CALLS) and len(q.options) == 3,
          "when the reasoning model is not available, the same steps run on OPENAI_MODEL")
os.environ.pop("NO_GPT5")

# 5. a scope step that fails never stops a quote
broken = FakeOpenAI.create
FakeOpenAI.create = lambda self, **kw: (_ for _ in ()).throw(RuntimeError("scope down")) if kw.get("text", {}).get("format", {}).get("name") == "powerhouse_job_scope" else broken(self, **kw)
CALLS.clear()
owner.post(url, data=dict(CSRF, message="Rebuild please."))
FakeOpenAI.create = broken
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    plan = next(c for c in CALLS if c.get("text", {}).get("format", {}).get("name") == "powerhouse_quote_plan")
    check(len(q.options) == 3 and "JOB_SCOPE" not in plan["instructions"] and "Whole home surge protector" in plan["instructions"],
          "if the scope step fails, Caden builds from the whole Pricing Sheet as before")

# 6. lighting: UF outdoors with its connectors; Romex outdoors is called out
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    option = q.options[0]
    for line in list(option.lines):
        option.lines.remove(line)
    porch = L.read_room({"type": "exterior", "length": 20, "width": 10, "height": 9})
    wiring = m.lighting_wiring(porch)
    notes = m.wire_for_lighting(option, 80, 90, wiring) + m.connectors_for_lighting(option, 4, wiring, 90)
    got = {line.price_item.id if line.price_item else line.service_name: line.multiplier for line in option.lines}
    check(got.get(ids["uf"]) == 80 and got.get(ids["ufconn"]) == 5, "outdoor lights get UF-B by the foot and a watertight connector at each light and the switch (%s)" % got)
    for line in list(option.lines):
        option.lines.remove(line)
    option.lines.append(m.QuoteOptionService(service_name="14/2 Romex", example_quantity="1 ft", multiplier=50, unit_price=0.6, position=0))
    said = m.wire_for_lighting(option, 80, 90, wiring)
    check(said and "calls for" in said[0] and "UF-B" in said[0], "Romex on an outdoor layout is called out: %s" % said)
    db.session.rollback()
done()
