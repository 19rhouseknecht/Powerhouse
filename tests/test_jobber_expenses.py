"""Job expenses and material costs from Jobber, and Update from Jobber (one click: pull, import, schedule & time, expenses).
Jobber expenses on a job become Expenses on that job (counted as its materials) with their receipt downloaded; a job with no
material cost gets its Jobber line items' cost. Nothing is doubled; nothing is changed in Jobber."""
import base64
import json
import os
import sys
import tempfile
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
m = make_site()
app, db, jb = m.app, m.db, m.jb
DOWNLOADS = []


def transport(method, url, headers, body):
    DOWNLOADS.append(url)
    if url.endswith("/gone.png"):
        return 404, b""
    return 200, PNG


jb.TRANSPORT = transport
e = jb.expense_fields({"id": "E1", "title": "Home Depot", "description": "conduit and fittings", "date": "2026-11-03", "total": "212.40",
                       "linkedJob": {"id": "J1"}, "enteredBy": {"name": {"full": "Caden Miller"}}, "receipt": {"url": "https://files.jobber.test/r1.png", "fileName": "r1.png"}})
check((e["title"], e["total"], e["date"], e["job_jobber_id"], e["person"], e["receipt_url"]) == ("Home Depot", 212.4, date(2026, 11, 3), "J1", "Caden Miller", "https://files.jobber.test/r1.png"),
      "a Jobber expense: what, how much, when, which job, who, and its receipt")
check(jb.line_item_cost([{"quantity": 2, "unit_cost": 40.5}, {"quantity": 1, "unit_cost": None}, {"quantity": "3", "unit_cost": 10}]) == 111.0,
      "a job's line items cost quantity x unit cost, where Jobber has a cost")

owner = sign_in(m)
with app.app_context():
    caden = m.User(username="caden", role=m.Role.query.filter_by(is_admin=True).first()); caden.set_password("a-long-password-1"); db.session.add(caden)
    person = m.Client(first_name="Ann", last_name="Lee", jobber_id="C1"); db.session.add(person); db.session.flush()
    j1 = m.Job(client=person, title="Panel upgrade", job_number=101, jobber_id="J1", address_line1="1 A St", city="Lititz", state="PA", postal_code="17543")
    j2 = m.Job(client=person, title="Hot tub", job_number=99, jobber_id="J2", address_line1="1 A St", city="Lititz", state="PA", postal_code="17543", material_cost=50)
    db.session.add_all([j1, j2]); db.session.commit()
    j1_id, j2_id, caden_id = j1.id, j2.id, caden.id
data = {"clients": [], "requests": [], "quotes": [], "timesheets": [],
        "jobs": [{"id": "J1", "jobNumber": 101, "title": "Panel upgrade", "client": {"id": "C1"}, "lineItems": {"nodes": [{"name": "Panel", "quantity": 1, "unitCost": 300}, {"name": "Breakers", "quantity": 4, "unitCost": 12.5}]}},
                 {"id": "J2", "jobNumber": 99, "title": "Hot tub", "client": {"id": "C1"}, "lineItems": {"nodes": [{"name": "GFCI", "quantity": 1, "unitCost": 90}]}}],
        "expenses": [{"id": "E1", "title": "Home Depot", "description": "conduit", "date": "2026-11-03", "total": 212.4, "linkedJob": {"id": "J1"},
                      "enteredBy": {"name": {"full": "Caden Miller"}}, "receiptUrl": "https://files.jobber.test/r1.png"},
                     {"id": "E2", "title": "Cooper", "total": 80, "date": "2026-11-04", "linkedJob": {"id": "J2"}, "receiptUrl": "https://files.jobber.test/gone.png"},
                     {"id": "E3", "title": "Gas", "total": 40, "date": "2026-11-04"}]}
with app.app_context():
    plan = m.jobber_expenses_plan(data)["counts"]
check((plan["expenses_new"], plan["expenses_no_job"], plan["total_new"], plan["receipts"], plan["materials_fill"]) == (2, 1, 292.4, 2, 1),
      "the plan: 2 job expenses ($292.40) with receipts, 1 not on a job, 1 job to get a material cost")
path = os.path.join(tempfile.mkdtemp(), "pull.json"); json.dump(data, open(path, "w"))
with app.app_context():
    m.put_jobber_setting("pull", {"status": "done", "file": path, "started_at": "2026-11-23T12:00:00", "finished_at": "2026-11-23T12:01:00", "by": "owner",
                                  "log": [], "counts": {k: len(data.get(k, [])) for k in jb.ORDER}, "account": "PH", "calls": 3})
page = owner.get("/settings/jobber").get_data(as_text=True)
check("Bring over what jobs cost" in page and "Update from Jobber" in page, "the Jobber page has Update from Jobber and Step 4 (expenses & materials)")
owner.post("/settings/jobber/expenses-import", data=CSRF)
with app.app_context():
    j1, j2 = db.session.get(m.Job, j1_id), db.session.get(m.Job, j2_id)
    e1 = m.Expense.query.filter_by(jobber_id="E1").one(); e2 = m.Expense.query.filter_by(jobber_id="E2").one()
    check((e1.job_id, e1.total, e1.category, e1.expense_date, e1.submitted_by_user_id) == (j1_id, 212.4, "Materials direct job", date(2026, 11, 3), caden_id),
          "a Jobber expense becomes an Expense on its job, as materials, entered by the person who entered it")
    check(e1.receipt_filename and e1.receipt_filename.endswith(".png") and open(os.path.join(app.static_folder, "uploads", "receipts", e1.receipt_filename), "rb").read() == PNG,
          "with its receipt downloaded and saved")
    check(not e2.receipt_filename and "gone.png" in e2.notes, "a receipt that can't be downloaded leaves its link in the notes")
    check(m.Expense.query.filter_by(jobber_id="E3").first() is None, "an expense on no job is left out")
    check(j1.material_cost == 350.0 and j2.material_cost == 50, "a job with no material cost gets its line items' cost; one that has one keeps it")
    check(m.job_money(j1)["materials"] == 562.4, "and the job's analytics count both")
owner.post("/settings/jobber/expenses-import", data=CSRF)
with app.app_context():
    check(m.Expense.query.filter(m.Expense.jobber_id != "").count() == 2, "bringing it over again doubles nothing")

# --- Update from Jobber: one click, in the background
data["expenses"].append({"id": "E4", "title": "Lowe's", "total": 15, "date": "2026-11-05", "linkedJob": {"id": "J1"}})
m.jobber_connected = lambda: True
m.jobber_session = lambda: None
jb.pull = lambda session, say=None: data
app.config["TESTING"] = True
r = owner.post("/settings/jobber/update", data=CSRF)
check(r.status_code == 302, "Update from Jobber starts")
for _ in range(100):
    with app.app_context():
        db.session.remove()
        state = m.jobber_pull_state()
    if state.get("status") in ("done", "failed"):
        break
    time.sleep(0.1)
check(state.get("status") == "done" and state.get("updated", {}).get("expenses") == 1, "it pulls and brings over what's new: %s" % (state.get("error") or state.get("updated")))
with app.app_context():
    check(m.Expense.query.filter(m.Expense.jobber_id != "").count() == 3 and m.Job.query.count() == 2, "only the new expense came over; no job was doubled")
page = owner.get("/settings/jobber").get_data(as_text=True)
check("Updated from Jobber" in page and "1 job expenses" in page, "the page says what it brought over")
check("Update from Jobber" in owner.get("/settings").get_data(as_text=True), "Settings has Update from Jobber too")
crew = sign_in(m, "crew", admin=False)
check(crew.post("/settings/jobber/update", data=CSRF).status_code == 403, "only admins can update from Jobber")
done()
