"""Generators -> Sizing: the NEC 220.82 load calculation (each case worked by hand below, as GenSizer does it), load
management (NEC 702.4(B)), the starting check, the page with every step shown, and saving a sizing to a job."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
import generators as gen  # noqa: E402

# A: 2,000 sq ft, the default appliances (dishwasher 1,200, disposal 900, microwave 1,500, dryer 5,000, water heater 4,500,
# blower 1,176, sump pump 1,176 VA) and one 3-ton A/C
#   lighting 2,000 x 3 = 6,000; circuits 3 x 1,500 = 4,500; appliances 15,452; general 25,952 VA
#   10,000 + 15,952 x 40 % = 16,380.8; A/C 3 x 1,300 + 240 = 4,140; total 20,520.8 VA = 85.5 A -> 20.52 kW -> 26RCA (24 kW NG)
a = gen.size({})
steps = {label: value for number, label, how, value, ref in a["steps"]}
check(steps["General lighting and receptacles"] == 6000 and steps["Small-appliance and laundry circuits"] == 4500, "1 and 2: 3 VA per sq ft, 1,500 VA per circuit")
check(steps["General load"] == 25952, "3: appliances at their nameplate VA; general load 25,952 VA")
check(abs(steps["General load after demand"] - 16380.8) < 1e-6, "4: first 10,000 VA at 100%, the rest at 40% = 16,380.8 VA")
check(a["total_va"] == 20521 and a["total_amps"] == 85.5, "5 and 6: + the A/C 4,140 VA = 20,521 VA, 85.5 A")
check(a["required_kw"] == 20.52 and a["model"]["model"] == "26RCA", "the generator must carry 20.52 kW: a 26RCA (24 kW on natural gas)")
check(gen.size({"fuel": "lp"})["model"]["model"] == "26RCA" and gen.size({"dryer": "0", "water_heater": "0"})["model"]["model"] == "20RCA",
      "without the dryer and water heater (9,500 VA, 40% of it): 16.72 kW, a 20RCA")

# B: heating and cooling, 220.82(C): the largest choice is used
hp = gen.size({"heat_pump_count": "1", "heat_strip_kw": "10"})
used = [label for number, label, how, value, ref in hp["steps"] if "(the largest: used)" in label]
check(used == ["Heat pump at 100% + heat strips at 65% (the largest: used)"] and abs(hp["totals"]["hvac"] - (4140 + 6500)) < 1e-6,
      "a heat pump with 10 kW strips: compressor 4,140 + 65% of 10,000 = 10,640 VA, more than the A/C")
check(abs(gen.size({"heat_pump_count": "1", "heat_strip_kw": "10", "heat_interlock": "yes"})["totals"]["hvac"] - 6500) < 1e-6, "locked out while the strips run: 65% of the strips only")
check(abs(gen.size({"ac_count": "0", "heat_strip_kw": "10", "heat_units": "4"})["totals"]["hvac"] - 4000) < 1e-6, "4 or more electric heaters: 40%")
check(abs(gen.size({"ac_count": "0", "heat_strip_kw": "10", "heat_units": "2"})["totals"]["hvac"] - 6500) < 1e-6, "fewer than 4: 65%")
check(abs(gen.size({"ac_amps": "20"})["totals"]["hvac"] - 4800) < 1e-6, "an A/C's nameplate amps are used when typed (20 A x 240 V)")

# C: load management (702.4(B)(2)): managed loads are left out of what the generator carries, not out of the calculation
lm = gen.size({"managed_dryer": "on", "managed_water_heater": "on"})
check(lm["total_va"] == 20521 and lm["generator_va"] == 16721 and lm["required_kw"] == 16.72 and lm["model"]["model"] == "20RCA",
      "dryer and water heater managed: the house is still 20,521 VA, the generator carries 16,721 VA (16.72 kW): a 20RCA")
check(gen.size({"managed_hvac": "on", "heat_strip_kw": "15", "ac_count": "0"})["totals"]["hvac"] == 9750 and
      gen.size({"managed_hvac": "on", "heat_strip_kw": "15", "ac_count": "0"})["generator_va"] < gen.size({"heat_strip_kw": "15", "ac_count": "0"})["generator_va"],
      "heating and cooling can be managed too")

# D: the starting check and the edges
check(gen.size({"ac_lra": "80"})["starting_kw"] == 19.2 and gen.size({"ac_lra": "80", "soft_start": "yes"})["starting_kw"] == 7.68,
      "the A/C start from its nameplate LRA (80 A x 240 V = 19.2 kVA), 40% of it with a soft starter")
check(any("Starting check" in note for note in a["notes"]), "the starting check is said")
big = gen.size({"square_feet": "6000", "range": "1", "tankless": "1", "ev_charger": "2", "ac_count": "3", "ac_tons": "5"})
check(big["model"] is None and any("liquid-cooled" in note for note in big["notes"]), "too much for the air-cooled range: it says so, no model")
check(gen.read_inputs({"square_feet": "-5", "ac_tons": "abc", "fuel": "diesel", "va_dryer": "x"})["square_feet"] == 0
      and gen.read_inputs({"ac_tons": "abc"})["ac_tons"] == 3.0 and gen.read_inputs({"fuel": "diesel"})["fuel"] == "ng", "odd values are cleaned")

# the page
app, db = m.app, m.db
with app.app_context():
    person = m.Client(first_name="Ann", last_name="Lee"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Standby generator", job_number=700); db.session.add(job); db.session.commit()
    job_id = job.id
client = sign_in(m)
crew = sign_in(m, "crew", admin=False)
page = client.get("/").get_data(as_text=True)
check("Generators</div>" in page and "</span> Sizing</a>" in page, "the sidebar has a Generators section with Sizing")
check(crew.get("/generators/sizing").status_code == 200, "every employee can size a generator")
page = client.get("/generators/sizing").get_data(as_text=True)
check("Size the generator" in page and "Recommendation" not in page and "NEC 220.82" in page, "the page opens with the form and says the method")
page = client.get("/generators/sizing", query_string={"calculate": "1", "managed_dryer": "on"}).get_data(as_text=True)
check("How it&#39;s calculated" in page or "How it's calculated" in page, "the answer shows how it's calculated")
for words in ("2,000 sq ft x 3 VA", "first 10,000 VA at 100%", "A/C at 100% (the largest: used)", "÷ 240 V", "managed loads left out"):
    check(words in page, f"step shown: {words}")
r = client.post("/generators/sizing", data=dict(CSRF, job_id=str(job_id), calculate="1", managed_dryer="on"), follow_redirects=True)
page = r.get_data(as_text=True)
check("Saved the sizing to job #700" in page and "NEC 220.82 calculated load 20,521 VA" in page, "a sizing is saved to a job and shows its calculation there")
with app.app_context():
    saved = m.GeneratorSizing.query.one()
    check(saved.model == "26RCA" and saved.required_kw == 18.52 and saved.inputs["managed_dryer"] == "on" and "managed" not in saved.inputs, "with its inputs, managed loads included")
reopen = [p.split('"')[0] for p in page.split('href="') if p.startswith("/generators/sizing?calculate=1")][0].replace("&amp;", "&")
again = client.get(reopen).get_data(as_text=True)
check('name="managed_dryer" value="on" checked' in again and "18.52 kW" in again, "Open it on the job brings back the same answer")
done()
