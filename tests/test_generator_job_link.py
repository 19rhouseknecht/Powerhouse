"""Generators -> Sizing and Upgrading linked to a job: picked at the top (or opened from the job page), saved there with one
click, kept through Ask Caleb, reopened from the job; and Caden reading the newest saved sizing and upgrade when he builds that
job's quote. Nothing calls OpenAI."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    person = m.Client(first_name="Lee", last_name="Ortiz"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Standby generator", job_number=950, description="Wants a whole-home generator.")
    other = m.Job(client=person, title="Kitchen outlets", job_number=951, description="Add two outlets.")
    db.session.add_all([job, other]); db.session.commit()
    job_id, other_id = job.id, other.id
owner = sign_in(m)

# 1. the job page starts both, already linked
page = owner.get("/jobs/%d" % job_id).get_data(as_text=True)
check('href="/generators/sizing?job_id=%d"' % job_id in page and 'href="/generators/upgrade?job_id=%d"' % job_id in page, "the job page offers Size a generator and Plan an upgrade for this job")
check("Caden uses it when you generate the quote" in page, "and says Caden will use what is saved")

# 2. sizing: picked at the top, saved with one click
page = owner.get("/generators/sizing?job_id=%d" % job_id).get_data(as_text=True)
check('name="job_id"' in page and "Linked to" in page and "job #950" in page, "Sizing shows the linked job at the top")
page = owner.get("/generators/sizing?calculate=1&job_id=%d&square_feet=2600&ac_count=1&ac_tons=4" % job_id).get_data(as_text=True)
check("Save sizing to this job" in page and 'type="hidden" name="job_id" value="%d"' % job_id in page, "the save button already knows the job")
form = dict(CSRF, job_id=job_id, square_feet="2600", ac_count="1", ac_tons="4", fuel="ng", coverage="whole", service_amps="200", soft_start="no")
r = owner.post("/generators/sizing", data=form)
check(r.status_code == 302 and "/jobs/%d" % job_id in r.headers["Location"], "saving goes to that job")
page = owner.get("/generators/sizing").get_data(as_text=True)
check('<option value="">No job (just working it out)</option>' in page and "Pick the job this is for" in page, "without a job it still works, and says why to pick one")

# 3. upgrading: linked at the top, kept through Ask Caleb, saved and reopened without losing it
query = "calculate=1&job_id=%d&old=generac-20-7039&new=kohler-26rca&fuel=ng&gas_feet=50&wire_feet=30" % job_id
page = owner.get("/generators/upgrade?" + query).get_data(as_text=True)
check('value="#950 · Lee Ortiz · Standby generator"' in page, "Upgrading opened from a job has that job ready in Add to job")
r = owner.post("/generators/upgrade/ask", data=dict(CSRF, job_id=job_id, old="generac-20-7039", new="kohler-26rca", question="Keep the pad?"))
check("job_id=%d" % job_id in r.headers["Location"], "asking Caleb keeps the page linked to the job")
r = owner.post("/generators/upgrade/save", data=dict(CSRF, job_id=job_id, old="generac-20-7039", new="kohler-26rca", fuel="ng", gas_feet="50", wire_feet="30"))
check(r.status_code == 302 and "#generator-upgrade" in r.headers["Location"], "saved to the job")
with app.app_context():
    saved = m.GeneratorUpgrade.query.first()
    check("job_id" not in json.loads(saved.inputs_json), "the saved inputs are the generator's, not the job")
page = owner.get("/jobs/%d" % job_id).get_data(as_text=True)
check("Caden uses the newest sizing and upgrade below" in page and page.count("Open it") == 2, "the job page lists both, each reopenable")
reopen = [p.split('"')[0] for p in page.split('href="') if p.startswith("/generators/upgrade?calculate=1")][0].replace("&amp;", "&")
check(owner.get(reopen).status_code == 200 and 'value="#950 · Lee Ortiz · Standby generator"' in owner.get(reopen).get_data(as_text=True), "Open it comes back with the job ready")

# 4. Caden reads the newest sizing and upgrade on that job (and nothing on a job without any)
SEEN = []


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"ready": False, "message": "Which side of the house?", "questions": ["Which side of the house?"],
                                                       "quote_title": "", "introduction": "", "mike_question": "", "options": [], "pricing_needed": []}))


m.OpenAI = FakeOpenAI
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
owner.get("/quoting/generate/%d" % job_id)
owner.post("/quoting/generate/%d" % job_id, data=dict(CSRF, message="Quote the generator swap."))
words = SEEN[-1]["instructions"]
block = words[words.index("GENERATOR_WORK"):words.index("SERVICE_LIBRARY")]
check("GENERATOR SIZING" in block and "NEC 220.82" in block and "calculated load" in block, "Caden gets the saved sizing and its calculation")
check("GENERATOR UPGRADE" in block and "26RCA" in block and "2 AWG copper" in block and "1-1/4 in" in block and "haul-away of the old unit" in block,
      "and the saved upgrade: new model, wire, gas pipe and the rip-out")
check("Needs work:" in block and "build the generator scope from GENERATOR_WORK" in block, "with what needs work, and the rule to use it when a generator is involved")
SEEN.clear()
owner.get("/quoting/generate/%d" % other_id)
owner.post("/quoting/generate/%d" % other_id, data=dict(CSRF, message="Two outlets."))
check("GENERATOR_WORK (saved on this job from Generators -> Sizing / Upgrading):\nNothing saved for this job." in SEEN[-1]["instructions"],
      "a job with no generator work tells Caden so")
with app.app_context():
    with app.test_request_context():
        from flask_login import login_user
        login_user(m.User.query.filter_by(username="owner").first())
        broken = m.GeneratorUpgrade(job_id=other_id, inputs_json="{not json", summary="Generac to Kohler (old row)")
        db.session.add(broken); db.session.commit()
        check("Generac to Kohler (old row)" in (m.generator_context(db.session.get(m.Job, other_id)) or ""), "an upgrade that no longer reads still reaches Caden as its summary")

# 5. a role without Jobs sees no job picker
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_jobs = False; db.session.commit()
page = crew.get("/generators/sizing?job_id=%d" % job_id).get_data(as_text=True)
check('name="job_id"' not in page and "job #950" not in page, "a role without Jobs sees no jobs on the generator pages")
done()
