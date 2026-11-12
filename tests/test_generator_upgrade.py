"""Generators -> Upgrading: the wiring and gas line for the generator that is there and the one going in, from the makers'
spec sheets and the NEC / fuel-gas tables; one table of what is there now against what the new one needs, green or red;
Food for thought; adding it to a job (Caden quotes it from Generate Quote); Caleb answering with the upgrade attached. And the
sidebar's Website section."""
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

# 3a. Kohler's installation manual: its pipe chart (Figure 10), 8 ft per 90-degree elbow, the fuel supply (Figure 8)
chart = {"kohler-14rca": (["3/4", "1", "1", "1-1/4", "1-1/4"], ["3/4", "3/4", "1", "1", "1"]),
         "kohler-20rca": (["1", "1", "1-1/4", "1-1/4", "1-1/4"], ["3/4", "1", "1", "1-1/4", "1-1/4"]),
         "kohler-26rca": (["1", "1-1/4", "1-1/4", "1-1/4", "1-1/2"], ["3/4", "1", "1", "1", "1-1/4"])}
for key, (ng, lp) in chart.items():
    unit = gen.MODEL_BY_KEY[key]
    check([gen.gas(unit, "ng", f)["size"] for f in (25, 50, 100, 150, 200)] == ng and [gen.gas(unit, "lp", f)["size"] for f in (25, 50, 100, 150, 200)] == lp,
          f"{key}: every pipe size is Kohler's own chart (natural gas and LPG, 25 to 200 ft)")
check(gen.gas(gen.MODEL_BY_KEY["kohler-26rca"], "ng", 250)["source"] == "fuel-gas code tables", "past the chart's 200 ft the code tables take over")
btu = {key: (gen.gas_demand(gen.MODEL_BY_KEY[key], "ng")[0], gen.gas_demand(gen.MODEL_BY_KEY[key], "lp")[0]) for key in chart}
check(btu == {"kohler-14rca": (193000, 202500), "kohler-20rca": (281000, 340000), "kohler-26rca": (290000, 322500)},
      "maximum fuel flow matches Figure 8 (14RCA LPG 202,500 from 81 cfh x 2,500 against the manual's 203,000)")
check(all(gen.MODEL_BY_KEY[key]["inlet"] == "1/2 in NPT" for key in chart) and gen.MODEL_BY_KEY["kohler-14rca"]["ng_wc"] == (5, 11)
      and gen.MODEL_BY_KEY["kohler-26rca"]["ng_wc"] == (3.5, 11) and all(gen.MODEL_BY_KEY[key]["lp_wc"] == (7, 11) for key in chart),
      "fuel inlet 1/2 in NPT and the supply pressures of Figure 8")
elbows = gen.upgrade({"old": "generac-20-7039", "new": "kohler-26rca", "gas_feet": "25", "gas_elbows": "3", "existing_pipe": "1"})
rows = {row["key"]: row for row in gen.upgrade_table(elbows)}
check(rows["gas_feet"]["needs"] == "49 ft total" and elbows["gas_new"]["size"] == "1-1/4" and rows["pipe"]["status"] == "bad",
      "25 ft with 3 elbows is read as 49 ft (the 50 ft row): the 26RCA needs 1-1/4 in, so a 1 in line is red")
straight = gen.upgrade({"old": "generac-20-7039", "new": "kohler-26rca", "gas_feet": "25", "existing_pipe": "1"})
check(straight["gas_new"]["size"] == "1" and {r["key"]: r for r in gen.upgrade_table(straight)}["pipe"]["status"] == "ok",
      "without the elbows the same 25 ft run takes 1 in, and the 1 in line is green")

# 3b. the older, smaller Generac units, and anything not on the list
seven = gen.MODEL_BY_KEY["generac-7-5240"]
check((seven["kw_lp"], seven["kw_ng"], seven["breaker"], seven["ng_cfh"], seven["lp_cfh"]) == (7, 6, 30, 119, 54), "Generac 7 kW (6 kW NG): 30 A breaker, 119 cfh NG, 54 cfh LP (its manual)")
check(all(key in gen.MODEL_BY_KEY for key in ("generac-8-6237", "generac-9-7030", "generac-10-5241", "generac-11-6439", "generac-11-7032",
                                            "generac-13-5242", "generac-14-6247", "generac-15-7163", "generac-16-5243")), "and the other earlier 8 to 16 kW units")
r = gen.upgrade({"old": "generac-7-5240", "new": "generac-22", "fuel": "ng", "gas_feet": "40", "wire_feet": "30"})
check(r["wire_old"]["size"] == "10" and r["gas_old"]["size"] == "3/4" and r["wire_new"]["size"] == "3" and r["gas_new"]["size"] == "1",
      "7 kW to 22 kW: 10 AWG on 30 A and 3/4 in becomes 3 AWG on 100 A and 1 in")
typed = gen.upgrade({"old": "other", "old_kw": "7", "old_breaker": "30", "new": "generac-22", "fuel": "lp"})
check(typed["old"]["breaker"] == 30 and typed["old"]["lp_cfh"] == round(7 * gen.LP_CFH_PER_KW) and typed["wire_old"]["size"] == "10", "a unit not on the list is typed in")
check(any("typed in" in t and "estimated" in t for _, t in typed["notes"]), "and the page says its fuel use is an estimate until typed")
check(gen.upgrade({"old": "other", "old_kw": "12", "old_cfh": "190", "new": "generac-22"})["old"]["ng_cfh"] == 190, "a typed fuel figure is used as typed")
check(gen.upgrade({"old": "other", "old_kw": "9"})["old"]["breaker"] == 40, "no breaker typed: the next standard size above its current")

# 4. the page: the two generators, the table lined up under them, green and red rows, Food for thought, a searchable job
with app.app_context():
    person = m.Client(first_name="Pat", last_name="Reyes"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Generator swap", job_number=901); db.session.add(job)
    db.session.commit(); job_id = job.id
owner = sign_in(m)
page = owner.get("/generators/upgrade").get_data(as_text=True)
check(page.index(">⚙︎</span> Sizing") < page.index("Upgrading</a>") < page.index('Website</div>'), "Upgrading sits under Sizing in Generators")
website = page[page.index('Website</div>'):page.index('</aside>')]
check(all(x in website for x in ("Settings</a>", "Bug Reports</a>", "Releases</a>")), "Settings, Bug Reports and Releases sit under Website")
check('class="upgrade-grid"' in page and 'name="existing_wire"' in page and 'name="existing_pipe"' in page and "Calculate</button>" in page,
      "the table is there to fill in before calculating")
check('class="ug-ok"' not in page and 'class="ug-bad"' not in page and "Food for thought" not in page, "nothing is coloured until Calculate")
check(page.index('id="up-old"') < page.index('id="up-new"') < page.index('data-row="feeder"'), "the two generators head the table's columns")
query = "calculate=1&old=generac-20-7039&new=kohler-26rca&fuel=ng&wire_feet=30&gas_feet=45&existing_pipe=3/4&existing_wire=4&existing_material=copper"
page = owner.get("/generators/upgrade?" + query).get_data(as_text=True)


def row_class(key):
    return page.split('data-row="%s"' % key)[0].rsplit("<tr", 1)[1]


check('ug-bad' in row_class("feeder") and "85 A wire on a 125 A breaker" in page, "the 4 AWG feeder is red: 85 A wire on the new 125 A breaker")
check('ug-bad' in row_class("pipe") and "Under Kohler&#39;s installation manual for 45 ft (it carries about 52% of full-load fuel)" in page.replace("Kohler's", "Kohler&#39;s"),
      "the 3/4 in gas line is red: under Kohler's chart, and it carries 52% of the new unit's fuel")
check('ug-ok' in row_class("drop"), "voltage drop is green")
check('ug-bad' in row_class("switch") and "Different maker" in page, "Generac to Kohler: the transfer switch is red")
check("Food for thought" in page and "Plan for" not in page, "the notes are Food for thought")
check("Estimate</span>" not in page and "Build the quote" not in page and "Pricing:" not in page, "no pricing or quoting on this page")
check('list="upgrade-jobs"' in page and '<option value="#901 · Pat Reyes · Generator swap">' in page, "Add to job is a searchable list of jobs")
ok = owner.get("/generators/upgrade?calculate=1&old=generac-20-7040&new=generac-22-7043&existing_wire=3&existing_pipe=1-1/4&existing_switch=200&existing_ground=8&existing_conduit=1-1/4").get_data(as_text=True)
check('ug-ok' in ok.split('data-row="feeder"')[0].rsplit("<tr", 1)[1] and 'ug-ok' in ok.split('data-row="switch"')[0].rsplit("<tr", 1)[1],
      "a same-maker swap with the right wire and a 200 A switch: those rows are green")
assumed = owner.get("/generators/upgrade?calculate=1&old=generac-20-7039&new=generac-22").get_data(as_text=True)
check("(assumed: what the old unit needed)" in assumed, "what is left as not known is assumed to be what the old unit needed, and says so")
check(owner.get("/generators/upgrade?calculate=1&old=other&old_kw=7&old_breaker=30&new=generac-22").status_code == 200, "a typed-in unit works")

# 5. add to a job by typing in the searchable box; the job page lists it; Caden reads the table
r = owner.post("/generators/upgrade/save", data=dict(CSRF, job="#901 · Pat Reyes · Generator swap", old="generac-20-7039", new="kohler-26rca",
                                                      fuel="ng", gas_feet="45", existing_pipe="3/4", existing_wire="4"))
check(r.status_code == 302 and "#generator-upgrade" in r.headers["Location"], "Add to job saves it to the job typed")
r = owner.post("/generators/upgrade/save", data=dict(CSRF, job="nothing like a job", old="generac-20-7039", new="kohler-26rca"))
check("#save" in r.headers["Location"], "a job that cannot be found is asked for again")
job_page = owner.get("/jobs/%d" % job_id).get_data(as_text=True)
check("Generator work" in job_page and "Needs work: Feeder wire (x 3), Ground wire, Conduit (PVC 40), Gas pipe" in job_page, "the job page lists it with what needs work")
with app.app_context():
    words = m.generator_context(db.session.get(m.Job, job_id))
check("Needs work:" in words and "Gas pipe: now 3/4 in" in words and "removal and haul-away of the old unit" in words,
      "Caden gets the red rows to quote and the rip-out")
check(owner.post("/generators/upgrade/quote", data=dict(CSRF, job_id=job_id)).status_code == 404, "the old Build the quote is gone (quoting is Generate Quote's)")

SEEN = []


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(kwargs)
        return SimpleNamespace(output=[], output_text="Keeping 3/4 in will starve it at full load; run 1-1/4 in.")


m.OpenAI = FakeOpenAI
os.environ["OPENAI_API_KEY"] = "test-key-not-used"

# 7. Ask Caleb about the upgrade
SEEN.clear()
r = owner.post("/generators/upgrade/ask", data=dict(CSRF, old="generac-20-7039", new="kohler-26rca", fuel="ng", gas_feet="40", existing_pipe="3/4",
                                                     question="Is it okay to keep the 3/4 in line?"), follow_redirects=True)
page = r.get_data(as_text=True)
check("Keeping 3/4 in will starve it" in page, "Caleb's answer shows on the upgrade page")
sent = SEEN[0]["input"][-1]["content"]
check("Generator upgrade on natural gas" in sent and "Needs work:" in sent and "Is it okay to keep the 3/4 in line?" in sent, "he gets the upgrade, its red rows, and the question")
with app.app_context():
    check(m.CalebMessage.query.filter(m.CalebMessage.content.like("%About the generator upgrade%")).count() == 1, "and it is kept in the person's Caleb conversation")
check(owner.post("/generators/upgrade/ask", data={"question": "x"}).status_code == 400, "asking needs the CSRF token")
done()
