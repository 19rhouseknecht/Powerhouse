"""Suggest times on the job form: open site-review times and work days found in the real schedule (start 7-8 AM, done
by 4 PM, Monday to Friday), with the AI only reading the description for exceptions. A work day keeps its start time
and the schedule shows it. Browser half: the button, the options, and a click filling the form."""
import json
import os
import sys
import threading
from datetime import date, datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
S = m.scheduling
monday = date(2026, 11, 23)
rules = S.clean_rules()
check((rules["start_from"].hour, rules["start_to"].hour, rules["day_end"].hour, rules["weekdays"]) == (7, 8, 16, [0, 1, 2, 3, 4]), "the usual rules: start 7-8 AM, done by 4 PM, Mon-Fri")
check(S.clean_rules({"start_from": "nonsense", "weekdays": "x", "work_days": "lots"}) == rules, "anything unreadable falls back to the usual rules")
slots = S.review_slots(rules, [(datetime(2026, 11, 24, 7), datetime(2026, 11, 24, 8))], {date(2026, 11, 25)}, monday)
check([s["start"] for s in slots] == ["2026-11-24T08:00", "2026-11-26T07:00", "2026-11-27T07:00"],
      "site reviews: from tomorrow, around another appointment, never on a day the person is on a job, one per day")
check(slots[0]["label"] == "Tue Nov 24, 8:00 AM" and "60 min" in slots[0]["detail"], "with a plain label")
late = S.review_slots(rules, [(datetime(2026, 11, 24, h), datetime(2026, 11, 24, h + 1)) for h in range(7, 15)], set(), monday)
check(late[0]["start"] == "2026-11-24T15:00", "the last review of the day ends by 4 PM")
work = S.work_slots(rules, {date(2026, 11, 25)}, monday, 2)
check([(w["date"], w["days"], w["time"]) for w in work] == [("2026-11-26", 2, "07:00"), ("2026-11-30", 2, "07:00"), ("2026-12-02", 2, "07:00")],
      "work days: runs of open working days (the weekend skipped, a booked day avoided)")
check(work[0]["label"] == "Thu Nov 26 – Fri Nov 27" and "start 7:00 AM–8:00 AM, done by 4:00 PM" in work[0]["detail"], "saying when the crew starts and finishes")
custom = S.clean_rules({"start_from": "10:00", "start_to": "10:30", "weekdays": [5], "not_before": "2026-12-10"})
check(S.review_slots(custom, [], set(), monday)[0]["start"] == "2026-12-12T10:00" and S.work_slots(custom, set(), monday, 1)[0]["time"] == "10:00",
      "a description's exceptions (Saturdays, after 10, not before the 10th) are followed")

owner = sign_in(m)
with app.app_context():
    person = m.Client(first_name="Dana", last_name="Smith"); db.session.add(person); db.session.flush()
    crew = m.User.query.filter_by(username="owner").first()
    tomorrow = date.today() + timedelta(days=1)
    busy = m.Job(client=person, title="Booked", job_number=500, address_line1="1 A St", city="Lititz", state="PA", postal_code="17543",
                 work_start_date=tomorrow, duration_days=1, assigned_user_id=crew.id)
    db.session.add(busy); db.session.commit()
    client_id, owner_id = person.id, crew.id
os.environ.pop("OPENAI_API_KEY", None)
r = owner.post("/jobs/schedule-options", data=dict(CSRF, title="Panel swap", description="Swap the panel", assigned_user_id=str(owner_id), duration_days="1"))
data = r.json
check(r.status_code == 200 and "usual rules" in data["note"] and len(data["review"]) == 3 and len(data["work"]) == 3, "without the AI it still suggests, with the usual rules")
check(all(w["date"] != tomorrow.isoformat() for w in data["work"]), "never on a day the person already has work")


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        CALLS.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"start_from": "10:00", "start_to": "10:30", "day_end": "16:00", "weekdays": [0, 1, 2, 3, 4],
                                                       "not_before": "", "work_days": 3, "review_minutes": 60, "why": "The customer is home after 10 AM."}))


CALLS = []
m.OpenAI = FakeOpenAI
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
data = owner.post("/jobs/schedule-options", data=dict(CSRF, title="Basement", description="Finish the basement; customer home after 10", duration_days="1")).json
check("after 10" in CALLS[-1]["input"] and data["note"] == "The customer is home after 10 AM." and data["review"][0]["start"].endswith("T10:00"),
      "the AI reads the description for exceptions (after 10) and the times follow")
check(data["work_days"] == 3 and data["estimated"] and data["work"][0]["days"] == 3, "and estimates how many days the work takes when the form says 1")
data = owner.post("/jobs/schedule-options", data=dict(CSRF, title="Basement", description="x y z", duration_days="5")).json
check(data["work_days"] == 5 and not data["estimated"], "a number of days typed on the form wins")


class BrokenOpenAI(FakeOpenAI):
    def create(self, **kwargs):
        raise RuntimeError("down")


m.OpenAI = BrokenOpenAI
data = owner.post("/jobs/schedule-options", data=dict(CSRF, title="Basement", description="Finish it")).json
check("usual rules" in data["note"] and data["review"], "an AI error still gives times, with the usual rules")
m.OpenAI = FakeOpenAI

job_form = dict(CSRF, client_id=str(client_id), title="New service", description="", address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543",
                status="new_request", work_start_date="2026-12-01", work_start_time="07:30", duration_days="2")
r = owner.post("/jobs/new", data=job_form)
with app.app_context():
    job = m.Job.query.filter_by(title="New service").one()
    check(job.work_start_time == "07:30" and job.duration_days == 2, "a job keeps its work start time")
check("7:30 AM" in owner.get("/schedule?date=2026-12-01").get_data(as_text=True), "and the schedule shows it")
bad = dict(job_form, title="Bad time", work_start_time="25:99")
check(owner.post("/jobs/new", data=bad).status_code == 400, "a start time that isn't a time is refused")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
app.config["TESTING"] = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/jobs/new")
    page.fill("input[name=title]", "Basement finish")
    page.fill("textarea[name=description]", "Customer home after 10")
    page.click("[data-suggest-times]")
    page.locator("[data-work-options] .schedule-chip").first.wait_for(timeout=5000)
    check(page.locator("[data-review-options] .schedule-chip").count() == 3 and "home after 10" in page.locator("[data-suggest-note]").inner_text(),
          "Suggest times shows three review times and three work options, and why")
    page.locator("[data-review-options] .schedule-chip").first.click()
    check(page.input_value("input[name=review_at]").endswith("T10:00"), "clicking a review time fills the site review")
    page.locator("[data-work-options] .schedule-chip").first.click()
    check(page.input_value("input[name=work_start_date]") != "" and page.input_value("input[name=duration_days]") == "3" and page.input_value("input[name=work_start_time]") == "10:00",
          "clicking a work option fills the start date, days and start time")
    check(page.locator("[data-work-options] .schedule-chip.is-picked").count() == 1, "and shows which one is picked")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
