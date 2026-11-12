"""Generators -> Upgrading: the wiring and gas line for the generator that is there and the one going in, from the makers'
spec sheets and the NEC / fuel-gas tables; whether what is there can stay; a priced estimate from the Pricing Sheet; saving
to a job; Caden building the quote from it; Caleb answering with the upgrade attached. And the sidebar's Website section."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db, gen = m.app, m.db, m.gen

# 1. wiring: NEC 310.16 (75 C), 240.4(B) next size up, 250.122 grounds, voltage drop
w = lambda key, material="copper", feet=30: gen.wiring(gen.MODEL_BY_KEY[key], material, feet, "PVC 40")
check(w("generac-22")["size"] == "3" and w("generac-22")["ground"] == "8", "Generac 22 kW (100 A breaker): 3 AWG copper, 8 AWG ground")
check(w("generac-20-7039")["size"] == "4", "Generac 20 kW on a 90 A breaker: 4 AWG copper (85 A, next size up, 240.4(B))")
check(w("kohler-26rca")["size"] == "2" and w("kohler-26rca")["ground"] == "6", "Kohler 26RCA (125 A): 2 AWG copper (115 A, next size up), 6 AWG ground")
check(w("generac-22", "aluminum")["size"] == "1", "aluminum on 100 A: 1 AWG")
long_run = w("generac-26", feet=250)
check(long_run["upsized"] and long_run["drop_pct"] <= 3 and long_run["ground"] != "6", "a long run is upsized for voltage drop, and the ground with it")
check(gen.wire_ok("2", "copper", 125) and not gen.wire_ok("3", "copper", 110) and not gen.wire_ok("4", "copper", 100), "the next-size-up rule only where it applies")

# 2. gas: the pipe tables give Kohler's own spec-sheet recommendations
pipe = lambda key, feet, fuel="ng": gen.gas(gen.MODEL_BY_KEY[key], fuel, feet)["size"]
check([pipe("kohler-26rca", f) for f in (25, 50, 100, 150, 200)] == ["1", "1-1/4", "1-1/4", "1-1/4", "1-1/2"], "26RCA natural gas: same sizes as Kohler's table")
check([pipe("kohler-14rca", f) for f in (25, 50, 100, 150, 200)] == ["3/4", "1", "1", "1-1/4", "1-1/4"], "14RCA natural gas: same sizes as Kohler's table")
check([pipe("kohler-20rca", f) for f in (25, 50, 100, 150)] == ["1", "1", "1-1/4", "1-1/4"], "20RCA natural gas: same sizes as Kohler's table")
check([pipe("kohler-14rca", f, "lp") for f in (25, 50)] == ["3/4", "3/4"] and pipe("kohler-26rca", 50, "lp") == "1", "propane sizes too")
check(gen.gas(gen.MODEL_BY_KEY["generac-26"], "lp", 30)["btu"] == 144 * 2500, "LP Btu from cfh x 2,500 (Generac's own factor)")

# 3. the reported case: 3/4 in line, moving up to a bigger unit
r = gen.upgrade({"old": "generac-20-7039", "new": "kohler-26rca", "fuel": "ng", "gas_feet": "40", "existing_pipe": "3/4", "existing_wire": "2", "meter_cfh": "250"})
check(not r["keep"]["pipe"]["ok"] and 50 < r["keep"]["pipe"]["share"] < 65, "a 3/4 in line over 40 ft carries well under what a 26RCA burns")
check(any("Keeping the 3/4 in line" in t and "underspeed" in t for _, t in r["notes"]), "and the drawbacks of keeping it are spelled out")
check(r["keep"]["wire"]["ok"], "existing 2 AWG copper can stay on the 125 A breaker")
check(any("Brand change" in t for _, t in r["notes"]) and r["needs_new_switch"], "a brand change calls for a new transfer switch")
check(any("over the 250 cfh meter" in t for _, t in r["notes"]), "the gas meter is checked against full-load use")
lp = gen.upgrade({"old": "kohler-14rca", "new": "generac-26", "fuel": "lp", "tank_gallons": "250"})
check(any("may not vaporize" in t for _, t in lp["notes"]) and any("days" in t for _, t in lp["notes"]), "propane: tank runtime and the cold-weather warning")
check(any("liquid-cooled" in t for _, t in gen.upgrade({"old": "kohler-20rca", "new": "kohler-24rcla"})["notes"]), "a move to the liquid-cooled 24RCLA flags the pad")
check(gen.read_upgrade({"old": "nope", "wire_feet": "-4", "fuel": "x"})["old"] == "generac-20-7039", "odd input falls back to defaults")
check(all(row[0] != "24RCLA" for row in gen.KOHLER_MODELS), "the Sizing page's air-cooled list no longer holds the liquid-cooled 24RCLA")

# 4. the page, the sidebar
with app.app_context():
    person = m.Client(first_name="Pat", last_name="Reyes"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Generator swap", job_number=901); db.session.add(job)
    db.session.add_all([m.PriceItem(name="2 AWG THHN copper wire", lookup_key="2awg", unit="ft", price=3.10, labor_hours_per_unit=0),
                        m.PriceItem(name="#2/0 THHN copper wire", lookup_key="2/0", unit="ft", price=9.0),
                        m.PriceItem(name="6 AWG bare copper ground", lookup_key="6g", unit="ft", price=1.2),
                        m.PriceItem(name="1-1/4 in black iron gas pipe", lookup_key="pipe114", unit="ft", price=6.5),
                        m.PriceItem(name="1-1/4 in black iron elbow", lookup_key="elb", unit="ft", price=1.0),
                        m.PriceItem(name="Kohler 26RCA generator", lookup_key="26rca", unit="ea", price=7400)])
    db.session.commit(); job_id = job.id
owner = sign_in(m)
page = owner.get("/generators/upgrade").get_data(as_text=True)
check(page.index(">⚙︎</span> Sizing") < page.index("Upgrading</a>") < page.index('Website</div>'), "Upgrading sits under Sizing in Generators")
website = page[page.index('Website</div>'):page.index('</aside>')]
check(all(x in website for x in ("Settings</a>", "Bug Reports</a>", "Releases</a>")), "Settings, Bug Reports and Releases sit under Website")
query = "calculate=1&old=generac-20-7039&new=kohler-26rca&fuel=ng&wire_feet=30&gas_feet=50&existing_pipe=3/4"
page = owner.get("/generators/upgrade?" + query).get_data(as_text=True)
check("Side by side" in page and "125 A" in page and "1-1/4 in" in page, "the comparison shows the new breaker and pipe")
check("Pricing Sheet: 2 AWG THHN copper wire" in page and "#2/0" not in page.split("Estimate")[1], "2 AWG wire is priced from the sheet (2/0 is not mistaken for it)")
check("Pricing Sheet: 1-1/4 in black iron gas pipe" in page and "Pricing Sheet: Kohler 26RCA generator" in page, "the pipe (not a fitting) and the generator too")
check("needs a price" in page and "New Kohler automatic transfer switch" in page, "what has no price is marked")
with app.app_context():
    result = gen.upgrade({k: v for k, v in (p.split("=") for p in query.split("&"))})
    with app.test_request_context():
        from flask_login import login_user
        login_user(m.User.query.filter_by(username="owner").first())
        est = m.upgrade_estimate(result, {"switch_price": "1500", "pad_price": "250", "permit": "200", "conduit_price": "2.40"})
check(not est["missing"] and est["total"] == round(est["material"] + est["labor_hours"] * est["labor_rate"], 2), "typed prices fill the rest; the total adds up")
check(any(line["label"].startswith("Rip out") and line["hours"] == 3 for line in est["lines"]), "the rip-out time is in the estimate")

crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_quoting = False; db.session.commit()
crew_page = crew.get("/generators/upgrade?" + query).get_data(as_text=True)
check("Side by side" in crew_page and "Upgrading</a>" in crew_page, "a crew member uses the upgrade page")
check("Estimate</span>" not in crew_page and "Website</div>" not in crew_page, "without quoting they see no prices, and no Website section")

# 5. save to a job, and reopen it from the job page
r = owner.post("/generators/upgrade/save", data=dict(CSRF, job_id=job_id, old="generac-20-7039", new="kohler-26rca", fuel="ng", gas_feet="40"))
check(r.status_code == 302 and "#generator-upgrade" in r.headers["Location"], "Save to job goes to the job's upgrade panel")
job_page = owner.get("/jobs/%d" % job_id).get_data(as_text=True)
check("Generator upgrade" in job_page and "26RCA" in job_page and "Open it" in job_page, "the job page lists the upgrade")
with app.app_context():
    saved = m.GeneratorUpgrade.query.first()
    check(saved.inputs["new"] == "kohler-26rca" and "Estimate $" in saved.summary, "it keeps the inputs and the estimate")

# 6. Build the quote: Caden receives the upgrade as the office's message
SEEN = []


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(kwargs)
        if "tools" in kwargs:
            return SimpleNamespace(output=[], output_text="Keeping 3/4 in will starve it at full load; run 1-1/4 in.")
        return SimpleNamespace(output_text=json.dumps({"ready": False, "message": "One question.", "questions": ["Is the pad level?"], "quote_title": "",
                                                       "introduction": "", "mike_question": "", "options": [], "pricing_needed": []}))


m.OpenAI = FakeOpenAI
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
r = owner.post("/generators/upgrade/quote", data=dict(CSRF, job_id=job_id, old="generac-20-7039", new="kohler-26rca", fuel="ng", gas_feet="40",
                                                       existing_pipe="3/4", switch_price="1500"), follow_redirects=True)
check(r.status_code == 200, "Build the quote lands on the job's quote")
with app.app_context():
    quote = m.Quote.query.filter_by(job_id=job_id).first()
    asked = [msg.content for msg in quote.messages if msg.role == "user"]
check(asked and "Generator upgrade on natural gas" in asked[-1] and "Rip out" in asked[-1] and "1-1/4 in" in asked[-1], "Caden gets the whole brief: old, new, wire, gas, rip-out")
check(any("Generator upgrade" in json.dumps(call.get("input"), default=str) for call in SEEN), "and builds from it")

# 7. Ask Caleb about the upgrade
SEEN.clear()
r = owner.post("/generators/upgrade/ask", data=dict(CSRF, old="generac-20-7039", new="kohler-26rca", fuel="ng", gas_feet="40", existing_pipe="3/4",
                                                     question="Is it okay to keep the 3/4 in line?"), follow_redirects=True)
page = r.get_data(as_text=True)
check("Keeping 3/4 in will starve it" in page, "Caleb's answer shows on the upgrade page")
sent = SEEN[0]["input"][-1]["content"]
check("Generator upgrade on natural gas" in sent and "Is it okay to keep the 3/4 in line?" in sent, "he gets the upgrade with the question")
with app.app_context():
    check(m.CalebMessage.query.filter(m.CalebMessage.content.like("%About the generator upgrade%")).count() == 1, "and it is kept in the person's Caleb conversation")
check(owner.post("/generators/upgrade/ask", data={"question": "x"}).status_code == 400, "asking needs the CSRF token")
done()
