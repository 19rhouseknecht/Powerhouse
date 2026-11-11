"""The Jobs tabs: In the works (no confirmed quote) · Quoted (a confirmed quote, no decision) ·
Active (the client approved) · Completed. Choosing the client's option approves the job."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

appmod = make_site()
app, db = appmod.app, appmod.db
with app.app_context():
    person = appmod.Client(first_name="Ann", last_name="Lee")
    db.session.add(person)
    db.session.flush()
    jobs = {name: appmod.Job(client=person, title=name, job_number=900 + i, status=status)
            for i, (name, status) in enumerate([("New request", "new_request"), ("Drafting", "working_quote"),
                                                ("Quoted job", "awaiting_decision"), ("Picked", "awaiting_decision"),
                                                ("Scheduled", "scheduled"), ("Done", "completed"), ("Lost", "declined")])}
    db.session.add_all(jobs.values())
    db.session.flush()
    db.session.add(appmod.Quote(job=jobs["Drafting"], status="draft"))
    db.session.add(appmod.Quote(job=jobs["Quoted job"], status="confirmed"))
    picked = appmod.Quote(job=jobs["Picked"], status="confirmed")
    db.session.add(picked)
    db.session.flush()
    option = appmod.QuoteOption(quote=picked, tier="Gold", title="Gold", position=0)
    db.session.add(option)
    db.session.commit()
    ids = {name: job.id for name, job in jobs.items()}
    option_id = option.id

client = sign_in(appmod)
response = client.post("/jobs/%d/accepted-option" % ids["Picked"], data=dict(CSRF, option_id=option_id))
check(response.status_code == 302, "choosing the client's option works")

with app.app_context():
    buckets = {job.title: job.bucket for job in appmod.Job.query.all()}
    check(buckets["New request"] == "in_works", "a job with no quote is In the works")
    check(buckets["Drafting"] == "in_works", "a job with only a draft quote is In the works")
    check(buckets["Quoted job"] == "quoted", "a job with a confirmed quote is Quoted")
    check(buckets["Picked"] == "active", "choosing the client's option makes the job Active")
    check(db.session.get(appmod.Job, ids["Picked"]).status == "approved", "and marks it approved")
    check(buckets["Scheduled"] == "active", "a scheduled job is Active")
    check(buckets["Done"] == "completed" and buckets["Lost"] == "completed", "completed and declined jobs are Completed")

page = client.get("/jobs?tab=quoted").get_data(as_text=True)
check("Quoted job" in page and "New request" not in page and "Picked" not in page, "the Quoted tab lists only quoted jobs")
page = client.get("/jobs?tab=in_works").get_data(as_text=True)
check("New request" in page and "Drafting" in page and "Quoted job" not in page, "the In the works tab lists jobs before a quote")
page = client.get("/jobs?tab=active").get_data(as_text=True)
check("Picked" in page and "Scheduled" in page and "Quoted job" not in page, "the Active tab lists approved jobs")
page = client.get("/jobs?tab=all").get_data(as_text=True)
check(all(name in page for name in ids), "All jobs lists every job")
for label, count in (("In the works", 2), ("Quoted", 1), ("Active jobs", 2), ("Completed", 2), ("All jobs", 7)):
    check("%s <span>%d</span>" % (label, count) in page, "tab count %s = %d" % (label, count))
done()
