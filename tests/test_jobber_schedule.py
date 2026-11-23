"""Schedule and time from Jobber: the pull reads Jobber's time sheets too (and carries on when Jobber won't allow them);
"Bring over schedule & time" puts each Jobber visit on the schedule with its crew, assigns jobs with nobody on them, and
puts each time sheet entry on that person's timesheet, on its job. Bringing it over again never doubles anything."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db, jb = m.app, m.db, m.jb


# --- the pull: timeSheetEntries is read under its own name, with only the fields this Jobber has
class FakeSession:
    TYPES = {"Query": {"timeSheetEntries": ("OBJECT", "TimeSheetEntryConnection")},
             "TimeSheetEntryConnection": {"nodes": ("OBJECT", "TimeSheetEntry"), "pageInfo": ("OBJECT", "PageInfo")},
             "TimeSheetEntry": {"id": ("SCALAR", "ID"), "startAt": ("SCALAR", "ISO8601DateTime"), "endAt": ("SCALAR", "ISO8601DateTime"),
                                "finalDuration": ("SCALAR", "Int"), "user": ("OBJECT", "User"), "job": ("OBJECT", "Job")},
             "User": {"id": ("SCALAR", "ID"), "name": ("OBJECT", "Name")}, "Name": {"full": ("SCALAR", "String")}, "Job": {"id": ("SCALAR", "ID")}}

    def __init__(self, refuse=False):
        self.calls, self.refuse, self.queries = 0, refuse, []

    def fields_of(self, name):
        return self.TYPES.get(name, {})

    def selection(self, *args, **kwargs):
        return jb.Session.selection(self, *args, **kwargs)

    def gql(self, query, variables=None):
        self.calls += 1; self.queries.append(query)
        if "account" in query:
            return {"account": {"id": "A", "name": "PH"}}
        if self.refuse:
            raise jb.JobberError('Jobber refused the request: "Access denied".', "refused")
        return {"timeSheetEntries": {"nodes": [{"id": "T9", "startAt": "2026-11-02T12:00:00Z", "endAt": "2026-11-02T20:30:00Z", "finalDuration": 30600,
                                                "user": {"id": "U1", "name": {"full": "Caden Miller"}}, "job": {"id": "J1"}}],
                                     "pageInfo": {"hasNextPage": False}}}


session = FakeSession()
pulled = jb.pull(session)
check(pulled["timesheets"][0]["id"] == "T9" and "timeSheetEntries(first: 50" in session.queries[-1] and "finalDuration" in session.queries[-1],
      "the pull reads Jobber's time sheets (timeSheetEntries)")
check("visit" not in session.queries[-1] and "timesheets.visit" in pulled["missing"], "asking only for the fields this Jobber has")
refused = jb.pull(FakeSession(refuse=True))
check("timesheets" in refused["missing"] and "Access denied" in refused["refused"]["timesheets"], "when Jobber won't allow time sheets, the pull says so and carries on")
entry = jb.timesheet_fields(pulled["timesheets"][0])
check(entry["seconds"] == 30600 and entry["start"] == datetime(2026, 11, 2, 12, 0) and entry["person"] == "Caden Miller", "an entry: who, when (UTC), how long")

# --- bringing it over
owner = sign_in(m)
with app.app_context():
    caden = m.User(username="caden", role=m.Role.query.filter_by(is_admin=True).first(), hourly_rate=32); caden.set_password("a-long-password-1")
    mike = m.User(username="MikeS", role=caden.role, hourly_rate=28); mike.set_password("a-long-password-1")
    db.session.add_all([caden, mike])
    person = m.Client(first_name="Ann", last_name="Lee"); db.session.add(person); db.session.flush()
    j1 = m.Job(client=person, title="Panel upgrade", job_number=101, jobber_id="J1", address_line1="1 A St", city="Lititz", state="PA", postal_code="17543")
    j2 = m.Job(client=person, title="Hot tub", job_number=99, jobber_id="J2", address_line1="1 A St", city="Lititz", state="PA", postal_code="17543", assigned_user_id=None)
    db.session.add_all([j1, j2]); db.session.commit()
    caden_id, mike_id, j1_id, j2_id = caden.id, mike.id, j1.id, j2.id
data = {"jobs": [
    {"id": "J1", "jobNumber": 101, "title": "Panel upgrade", "visits": {"nodes": [
        {"id": "V1", "title": "Day 1", "startAt": "2026-11-02T12:00:00Z", "endAt": "2026-11-02T21:00:00Z", "completedAt": "2026-11-02T21:00:00Z",
         "assignedUsers": {"nodes": [{"name": {"full": "Caden Miller"}}, {"name": {"full": "Mike Smith"}}]}},
        {"id": "V2", "title": "Day 2", "startAt": "2026-11-03T12:30:00Z", "endAt": "2026-11-03T18:00:00Z", "assignedUsers": {"nodes": [{"name": {"full": "Caden Miller"}}]}}]}},
    {"id": "J2", "jobNumber": 99, "title": "Hot tub", "visits": {"nodes": [{"id": "V3", "title": "Wire it", "startAt": "2026-10-20T13:00:00Z", "assignedUsers": {"nodes": [{"name": {"full": "Mike Smith"}}]}}]}},
    {"id": "J7", "jobNumber": 7, "title": "Not imported", "visits": {"nodes": [{"id": "V9", "startAt": "2026-10-01T13:00:00Z"}]}}],
    "timesheets": [
        {"id": "T1", "startAt": "2026-11-02T12:00:00Z", "endAt": "2026-11-02T20:30:00Z", "finalDuration": 30600, "user": {"name": {"full": "Caden Miller"}}, "job": {"id": "J1"}},
        {"id": "T2", "startAt": "2026-11-02T12:15:00Z", "endAt": "2026-11-02T20:00:00Z", "user": {"name": {"full": "Mike Smith"}}, "visit": {"id": "V1"}, "note": "pulled wire"},
        {"id": "T3", "startAt": "2026-11-04T12:00:00Z", "endAt": "2026-11-04T16:00:00Z", "user": {"name": {"full": "Zed Unknown"}}, "job": {"id": "J1"}},
        {"id": "T4", "startAt": "2026-11-05T12:00:00Z", "user": {"name": {"full": "Caden Miller"}}, "job": {"id": "J1"}},
        {"id": "T5", "startAt": "2026-11-06T12:00:00Z", "endAt": "2026-11-06T14:00:00Z", "user": {"name": {"full": "Caden Miller"}}}]}
path = os.path.join(tempfile.mkdtemp(), "pull.json"); json.dump(data, open(path, "w"))
with app.app_context():
    m.put_jobber_setting("pull", {"status": "done", "file": path, "started_at": "2026-11-23T12:00:00", "finished_at": "2026-11-23T12:01:00",
                                  "by": "owner", "log": [], "counts": {"clients": 0, "requests": 0, "quotes": 0, "jobs": 3, "timesheets": 5},
                                  "account": "PH", "calls": 4})
    plan = m.jobber_schedule_plan(data)
check(plan["mapping"] == {"Caden Miller": caden_id, "Mike Smith": mike_id, "Zed Unknown": 0}, "Jobber people are matched to site users by name (Caden -> caden, Mike Smith -> MikeS)")
check(plan["counts"]["visits_new"] == 3 and plan["counts"]["visits_no_job"] == 1 and plan["counts"]["assign"] == 2, "the plan: 3 visits, one on a job not imported, 2 jobs to assign")
check(plan["counts"]["time_new"] == 3 and plan["counts"]["time_unmatched"] == 1 and plan["counts"]["time_open"] == 1, "3 time entries; one for a stranger, one still running")
page = owner.get("/settings/jobber").get_data(as_text=True)
check("Bring over the schedule and who worked" in page and ("Who&#39;s who" in page or "Who's who" in page) and "Zed Unknown" in page,
      "the Jobber page shows Step 3 with who's who")
r = owner.post("/settings/jobber/schedule-import", data=CSRF)
with app.app_context():
    j1, j2 = db.session.get(m.Job, j1_id), db.session.get(m.Job, j2_id)
    check([(v.title, v.people, v.user_ids) for v in j1.schedule_visits] == [("Day 1", ["Caden Miller", "Mike Smith"], [caden_id, mike_id]), ("Day 2", ["Caden Miller"], [caden_id])],
          "each Jobber visit is on the schedule with its crew")
    check(j1.assigned_user_id == caden_id and j2.assigned_user_id == mike_id, "jobs with nobody on them are assigned to who worked them most")
    sessions = m.ClockSession.query.order_by(m.ClockSession.clock_in).all()
    check([(s.user_id, s.clock_in, s.clock_out) for s in sessions] == [(caden_id, datetime(2026, 11, 2, 12), datetime(2026, 11, 2, 20, 30)),
                                                                        (mike_id, datetime(2026, 11, 2, 12, 15), datetime(2026, 11, 2, 20)),
                                                                        (caden_id, datetime(2026, 11, 6, 12), datetime(2026, 11, 6, 14))],
          "each time sheet entry is on that person's timesheet, at Jobber's times")
    visits = m.JobVisit.query.all()
    check(sorted((v.job_id, v.hourly_rate) for v in visits) == [(j1_id, 28.0), (j1_id, 32.0)] and "pulled wire" in sessions[1].notes,
          "on its job (found by the job, or by the visit), at the person's pay rate; the one with no job is general time")
page = owner.get("/schedule?date=2026-11-02").get_data(as_text=True)
check("7:00 AM–4:00 PM · Caden Miller, Mike Smith" in page and "Day 2" in page, "the schedule shows each visit at its time in Pennsylvania, with its crew")
check("Work day 1/" not in page, "a job brought over with visits isn't also drawn as a run of work days")
owner.post("/settings/jobber/schedule-import", data=CSRF)
with app.app_context():
    check(m.JobScheduleVisit.query.count() == 3 and m.ClockSession.query.count() == 3, "bringing it over again doubles nothing")
owner.post("/settings/jobber/people", data=dict(CSRF, jobber_name=["Zed Unknown"], user_id=[str(mike_id)]))
owner.post("/settings/jobber/schedule-import", data=CSRF)
with app.app_context():
    check(m.ClockSession.query.filter_by(jobber_id="T3").one().user_id == mike_id, "matching someone by hand brings over their time too")
crew = sign_in(m, "crew", admin=False)
check(crew.post("/settings/jobber/schedule-import", data=CSRF).status_code == 403, "only admins can bring it over")
done()
