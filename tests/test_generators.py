"""Generators -> Sizing: the arithmetic (checked by hand below), the page, and saving a sizing to a job."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
import generators as gen  # noqa: E402

check(abs(gen.general_lighting_kw(2000) - 4.05) < 1e-9, "2,000 sq ft: 6,000 VA → 3,000 + 3,000 × 35 % = 4.05 kW (NEC 220.12 / 220.42)")
check(gen.general_lighting_kw(800) == 2.4, "800 sq ft: 2.4 kW, all at 100 %")

home = {"coverage": "whole", "fuel": "ng", "square_feet": "2000", "ac_count": "1", "ac_tons": "3", "ac_lra": "0", "furnace_blowers": "1",
        "water_heater_kw": "0", "sump_pump_hp": "0", "dryer_kw": "0", "refrigerators": "1", "well_pump_hp": "0"}
r = gen.size(home)
# running: lighting 4.05 + A/C 3.45 + blower 0.8 + fridge 0.2 = 8.5; A/C starts at 10.35 → 8.5 − 3.45 + 10.35 = 15.4
check(r["running_kw"] == 8.5 and r["starting_kw"] == 15.4 and r["required_kw"] == 15.4, "a 3-ton A/C home: 8.5 kW running, 15.4 kW starting, needs 15.4 kW")
check(r["model"]["model"] == "20RCA" and r["model"]["rated_kw"] == 18, "→ 20RCA (18 kW on natural gas)")
check(gen.size(dict(home, fuel="lp"))["model"]["model"] == "20RCA", "on propane too (20 kW)")
soft = gen.size(dict(home, soft_start="yes"))
# with a soft starter the A/C adds only 10.35 × 0.4 − 3.45 = 0.69 kW when it starts, so the furnace blower (2.4 − 0.8 = 1.6 kW) sets the start
check(soft["starting_kw"] == 10.1 and "blower" in soft["sets_start"].lower() and soft["model"]["model"] == "14RCA",
      "a soft starter makes the A/C start smaller than the blower's: 10.1 kW starting → 14RCA (12 kW NG)")
lra = gen.size(dict(home, ac_lra="80"))
check(lra["starting_kw"] == round(8.5 - 3.45 + 80 * 240 / 1000, 2), "the nameplate LRA is used when given (80 A × 240 V = 19.2 kVA)")
check(any("LRA" in note for note in r["notes"]) and not any("LRA" in note for note in lra["notes"]), "it says the A/C start is estimated until the LRA is entered")
big = gen.size(dict(home, ac_count="2", water_heater_kw="4.5", dryer_kw="5", ev_kw="11.5", hot_tub_kw="6", square_feet="4000"))
check(big["model"] is None and any("liquid-cooled" in note for note in big["notes"]), "too much for the air-cooled range: it says so, no model")
check(big["with_load_management"]["model"] is not None and "EV charger" in big["with_load_management"]["shed"], "and shows what load management would allow")
check(gen.read_inputs({"square_feet": "-5", "ac_tons": "abc", "fuel": "diesel"}) ["square_feet"] == 0, "odd values are cleaned (negative → 0)")
check(gen.read_inputs({"ac_tons": "abc"})["ac_tons"] == 3.0 and gen.read_inputs({"fuel": "diesel"})["fuel"] == "ng", "(not a number → default; unknown fuel → natural gas)")

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
check("Size the generator" in page and "Recommendation" not in page, "the page opens with the form")
page = client.get("/generators/sizing", query_string=dict(home, calculate="1")).get_data(as_text=True)
check("20RCA" in page and "15.4 kW" in page and "How it adds up" in page, "and shows the answer with how it adds up")
r = client.post("/generators/sizing", data=dict(CSRF, job_id=str(job_id), **home), follow_redirects=True)
page = r.get_data(as_text=True)
check("Saved the sizing to job #700" in page and "20RCA · 15.4 kW needed" in page, "a sizing can be saved to a job and shows on the job")
with app.app_context():
    saved = m.GeneratorSizing.query.one()
    check(saved.model == "20RCA" and saved.inputs["ac_tons"] == 3.0, "with its inputs kept")
done()
