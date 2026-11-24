"""Every tier costs more than the one below it (Economy the least, Platinum the most), judged by installed cost: a lighting
layout of cheap lights that needs many fixtures can cost more than better lights that need few. Saved options are put in
price order once priced; the draft warns and fixes it in one click. Settings -> AI models says which model builds quotes and
which one answered the last one. Nothing calls OpenAI."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db = m.app, m.db
L = m.lighting

with app.app_context():
    m.set_company_setting("labor_rate", 90)
    cheap = m.PriceItem(name="Budget LED disc 300 lm", lookup_key="disc", unit="ea", price=5, labor_hours_per_unit=0.5)
    better = m.PriceItem(name="RAB 6in LED wafer 2000 lm", lookup_key="rab6", unit="ea", price=40, labor_hours_per_unit=0.5)
    db.session.add_all([cheap, better]); db.session.commit()
    room = L.read_room({"type": "kitchen", "length": 14, "width": 12, "height": 9})
    plans = m.add_installed_costs(L.plans(room, m.lighting_fixtures()), room)
    by = {p["name"]: p for p in plans}
    a, b = by["Budget LED disc 300 lm"], by["RAB 6in LED wafer 2000 lm"]
    check(a["material"] < b["material"] and a["count"] > b["count"], "the cheap light costs less per fixture but needs more of them (%d vs %d)" % (a["count"], b["count"]))
    check(a["installed"] > b["installed"], "installed (lights + labor + cable + connectors) the cheap light costs more: $%.2f vs $%.2f" % (a["installed"], b["installed"]))
    check(m.lighting_picks(plans, 3)[0]["name"] == b["name"], "so the lowest tier gets the light that costs least installed, not the cheapest fixture")

    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Lights", job_number=901, address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    db.session.commit(); job_id = job.id; ids = {"cheap": cheap.id, "better": better.id}

# Caden's draft: a tier that does not cost more than the one below is a finding to fix
draft = [{"tier": "Economy", "items": [{"price_item_id": ids["better"], "quantity": 10}], "services": [], "missing_services": []},
         {"tier": "Gold", "items": [{"price_item_id": ids["cheap"], "quantity": 10}], "services": [], "missing_services": []},
         {"tier": "Platinum", "items": [{"price_item_id": ids["better"], "quantity": 20}], "services": [], "missing_services": []}]
with app.app_context():
    found = m.tier_order_findings(draft)
    check(len(found) == 1 and found[0]["tier"] == "Gold" and found[0]["severity"] == "fix" and "Economy" in found[0]["problem"],
          "Gold costing less than Economy is found before anyone sees it: %s" % found)
    check(m.draft_estimate(draft[0]) == 10 * (40 + 0.5 * 90), "estimated from the Pricing Sheet: materials plus labor")

# saved options are put in price order
m.calculate_estimated_drive_time = lambda a: (20, 10)
owner = sign_in(m)
url = "/quoting/generate/%d" % job_id
owner.get(url)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3; db.session.commit(); quote_id = q.id
    tiers = m.quote_tiers(3)
    m.apply_ai_quote_options(q, [dict(o, tier=t, title=title, description="x", bullets=["a", "b"], direct_priced_items=[])
                                 for o, t, title in zip(draft, tiers, ("Better lights", "Budget lights", "Lots of better lights"))])
    db.session.commit()
    opts = sorted(q.options, key=lambda o: o.position)
    check([o.tier for o in opts] == tiers and opts[0].total < opts[1].total < opts[2].total and opts[0].title == "Budget lights",
          "once priced, the options are in price order: the cheaper scope carries the lower tier (%s)" % [(o.tier, o.title, round(o.total)) for o in opts])
    # a later change puts them out of order: the draft warns
    opts[0].lines[0].multiplier = 500
    db.session.commit()
page = owner.get(url).get_data(as_text=True)
check('id="tier-order"' in page and "Put the tiers in price order" in page, "the draft warns when a tier doesn't cost more than the one below")
owner.post("/quotes/%d/price-order" % quote_id, data=CSRF)
with app.app_context():
    opts = sorted(db.session.get(m.Quote, quote_id).options, key=lambda o: o.position)
    check(all(a.total < b.total for a, b in zip(opts, opts[1:])) and [o.tier for o in opts] == tiers, "one click puts them back in price order")
check('id="tier-order"' not in owner.get(url).get_data(as_text=True), "and the warning is gone")

# Settings -> AI models
os.environ.pop("OPENAI_QUOTE_MODEL", None)
page = owner.get("/settings").get_data(as_text=True)
check('id="ai-models"' in page and 'data-quote-model>gpt-5<' in page and "(set in the default)" in page and "No quote built since" in page,
      "Settings shows which model builds quotes")
owner.post("/settings/ai-model", data=dict(CSRF, quote_model="gpt-5.1"))
with app.app_context():
    check(m.quote_model() == "gpt-5.1", "and it can be changed there")
    m.note_model_used("gpt-5-mini", "gpt-5.1"); m.save_model_used(); db.session.commit()
page = owner.get("/settings").get_data(as_text=True)
check('data-model-used>gpt-5-mini<' in page and "wasn't available to your OpenAI account" in page, "it says which model answered the last quote, and when it fell back")
worker = sign_in(m, "worker", admin=False)
r = worker.post("/settings/ai-model", data=dict(CSRF, quote_model="gpt-4o-mini"))
with app.app_context():
    check(m.quote_model() == "gpt-5.1", "an employee cannot change it")
done()
