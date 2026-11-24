"""While Caden (and Caleb, and Mike) wait on the AI, nothing holds the database: every other page can still save. SQLite has
one writer at a time, so a build that leaves a write open while the AI thinks makes the quote page (and every page that
saves) hang until it is done, then time out. Here every AI call checks that another connection can write at that moment.
A quick quote is built in the background too. Nothing calls OpenAI."""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
app, db = m.app, m.db
DB_FILE = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
LOCKED, CALLS = [], []


def other_page_can_write(step):
    """What a page opened at this moment does: a quick write, refused if Caden's connection holds the lock."""
    try:
        con = sqlite3.connect(DB_FILE, timeout=0.5)
        con.execute("INSERT OR REPLACE INTO company_setting (key, value) VALUES ('lock_probe', ?)", (step,))
        con.commit(); con.close()
    except sqlite3.OperationalError as error:
        LOCKED.append("%s: %s" % (step, error))


with app.app_context():
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen lights", job_number=991, description="Add 6 recessed lights.",
                address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    wafer = m.PriceItem(name="6in LED wafer light", lookup_key="wafer", unit="ea", price=24, labor_hours_per_unit=0)
    db.session.add(wafer); db.session.commit()
    job_id, wafer_id = job.id, wafer.id


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        name = kwargs.get("text", {}).get("format", {}).get("name")
        CALLS.append(name)
        other_page_can_write(name)
        if name == "powerhouse_job_scope":
            out = {"site": "Kitchen.", "assumptions": [], "tiers": [{"tier": t, "intent": "Lights.", "components": [
                {"name": "Light", "spec": "6in LED wafer light", "quantity": 6, "unit": "ea", "why": "Asked.", "search": "6in LED wafer"}]} for t in m.quote_tiers(3)]}
        elif name == "powerhouse_quote_plan":
            tiers = json.loads(kwargs["instructions"].split("REQUIRED_TIERS_IN_ORDER: ")[1].split("\n")[0])
            out = {"ready": True, "message": "Built.", "questions": [], "quote_title": "Lights", "introduction": "Hi.", "mike_question": "", "pricing_needed": [],
                   "options": [{"tier": t, "title": "Lights %d" % n, "description": "x", "bullets": ["Lights", "More %d" % n], "services": [],
                                "items": [{"price_item_id": wafer_id, "quantity": 6 + 2 * n}], "direct_priced_items": [], "missing_services": []} for n, t in enumerate(tiers)]}
        elif name == "powerhouse_peer_review":
            out = {"approved": True, "summary": "Fine.", "findings": []}
        elif name == "labor_suggestions":
            out = {"suggestions": [{"price_item_id": wafer_id, "hours_per_unit": 0.5, "reason": "Typical."}]}
        else:
            out = {}
        return type("Reply", (), {"output_text": json.dumps(out), "model": kwargs.get("model")})()


m.OpenAI = FakeOpenAI
owner = sign_in(m)
url = "/quoting/generate/%d" % job_id
owner.get(url)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3; db.session.commit(); quote_id = q.id
owner.post(url, data=dict(CSRF, message="Add 6 recessed lights."))
check({"powerhouse_job_scope", "powerhouse_quote_plan", "powerhouse_peer_review", "labor_suggestions"} <= set(CALLS),
      "the build ran every step (%s)" % CALLS)
check(not LOCKED, "while every AI call was thinking, another page could still save" + ("" if not LOCKED else ": " + "; ".join(LOCKED)))
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    used = json.loads(db.session.get(m.CompanySetting, "quote_model_used").value)
    check(len(q.options) == 3 and q.labor_suggestions.get(str(wafer_id), {}).get("hours") == 0.5 and used["model"] == "gpt-5",
          "and the quote, Mike's times and which model answered are all saved")

# a quick quote: the page answers at once and the options are built in the background
CALLS.clear(); LOCKED.clear()
r = owner.post("/quoting/quick", data=dict(CSRF, description="Add 6 recessed lights in the kitchen", client_name="Lee", option_count="3"))
with app.app_context():
    lead = m.QuickQuote.query.order_by(m.QuickQuote.id.desc()).first()
    check(r.status_code == 302 and lead.ai_result and lead.ai_result.get("ready") and len(lead.ai_result["options"]) == 3,
          "a quick quote is built (in the background) with its options ready for assignment")
check(not LOCKED, "and nothing was locked while it was built" + ("" if not LOCKED else ": " + "; ".join(LOCKED)))
done()
