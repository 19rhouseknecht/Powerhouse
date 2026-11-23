"""Two roles, Admin and Employee, and job analytics. Everyone was moved onto the two roles once (old roles kept, unused).
Employees don't see Settings, job analytics, Finance, other people's timesheets, the labor rate / Labor calculator or Cooper
price updates. Admins get View analytics on each job: a pie of materials, labor and profit out of revenue."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
with app.app_context():
    office = m.Role.query.filter_by(name="Office").first()
    old = m.User(username="olduser", role=office); old.set_password("a-long-password-1"); db.session.add(old)
    db.session.delete(db.session.get(m.CompanySetting, "two_roles_once")); db.session.commit()
    m.two_roles_once(); db.session.commit()
    admin_role, employee_role = m.two_roles()
    check(m.User.query.filter_by(username="olduser").one().role.name == "Employee" and m.User.query.filter_by(username="owner").one().role.name == "Admin",
          "everyone was moved onto Admin or Employee once")
    check(m.Role.query.filter_by(name="Office").first() is not None, "the old roles are kept (unused)")
    employee_id = m.User.query.filter_by(username="olduser").one().id
    admin_id = m.User.query.filter_by(username="owner").one().id

worker = m.app.test_client()
with worker.session_transaction() as sess:
    sess["_user_id"] = str(employee_id); sess["_fresh"] = True; sess["csrf_token"] = "test-token"
with app.app_context():
    person = m.Client(first_name="Ann", last_name="Lee"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Panel upgrade", job_number=500, address_line1="1 A St", city="Lititz", state="PA", postal_code="17543",
                contract_amount=10000, material_cost=3000)
    db.session.add(job); db.session.flush()
    worker_user = db.session.get(m.User, employee_id); worker_user.hourly_rate = 40
    clock = m.ClockSession(user=worker_user, clock_in=datetime(2026, 11, 2, 12), clock_out=datetime(2026, 11, 2, 22))
    clock.visits.append(m.JobVisit(job=job, started_at=datetime(2026, 11, 2, 12), ended_at=datetime(2026, 11, 2, 22), hourly_rate=40))
    db.session.add(clock)
    db.session.add(m.Expense(job=job, vendor="Sunbelt", total=100, category="Equipment rental", submitted_by_user_id=admin_id) if hasattr(m.Expense, "submitted_by_user_id") else m.Expense(job=job, vendor="Sunbelt", total=100, category="Equipment rental"))
    db.session.commit(); job_id = job.id

for url in ["/settings", "/settings/roles", "/jobs/%d/analytics" % job_id, "/finance", "/quoting/labor-calculator", "/quoting/pricing/cooper-account"]:
    check(worker.get(url).status_code == 403, "an Employee can't open %s" % url)
for url, data in [("/quoting/pricing/labor-rate", dict(CSRF, labor_rate="1")), ("/quoting/pricing/cooper-sync", dict(CSRF, scope="new"))]:
    check(worker.post(url, data=data).status_code == 403, "an Employee can't post %s" % url)
for url in ["/jobs", "/jobs/%d" % job_id, "/quoting/pricing", "/schedule", "/time-tracking/timesheet", "/quoting/services"]:
    check(worker.get(url).status_code == 200, "an Employee can open %s" % url)
page = worker.get("/jobs/%d" % job_id).get_data(as_text=True)
check("View analytics" not in page and "Tracked labor" not in page and ">Settings</a>" not in page and "Settings</a>" not in page.split("side-section")[-1],
      "an Employee's job page has no View analytics or money, and no Settings in the menu")
sheet = worker.get("/quoting/pricing").get_data(as_text=True)
check("cooper-dialog" not in sheet and "Labor calculator</strong>" not in sheet and "check_cooper" not in sheet and "/quoting/pricing/cooper-sync" not in sheet,
      "an Employee's Pricing Sheet has no Cooper window, Labor calculator or Cooper price update")
sheet_admin = owner.get("/quoting/pricing").get_data(as_text=True)
check("cooper-dialog" in sheet_admin and "Labor calculator</strong>" in sheet_admin, "an Admin's still has them")
other = worker.get("/time-tracking/timesheet?user_id=%d" % admin_id).get_data(as_text=True)
check("owner" not in other.split("<select")[1].split("</select>")[0] if "<select" in other else True, "an Employee only sees their own timesheet")

page = owner.get("/jobs/%d" % job_id).get_data(as_text=True)
check("View analytics" in page, "an Admin's job page has View analytics")
a = owner.get("/jobs/%d/analytics" % job_id).get_data(as_text=True)
with app.app_context():
    money = m.job_money(db.session.get(m.Job, job_id))
check((money["revenue"], money["materials"], money["labor"], money["other"], money["profit"]) == (10000, 3000, 400, 100, 6500), "revenue, materials, labor, other expenses, profit: %s" % money)
check('class="donut"' in a and a.count("<path") == 4 and "$6,500" in a and "65% of revenue" in a, "the analytics page draws the pie with profit in the middle")
check("olduser" in a and "10.0" in a and "$400.00" in a, "and who worked it, for how long, at what cost")
with app.app_context():
    j = db.session.get(m.Job, job_id); j.contract_amount = 2000; db.session.commit()
a = owner.get("/jobs/%d/analytics" % job_id).get_data(as_text=True)
check("Loss" in a and "cost more than it brought in" in a and "-$1,500" in a, "a job that lost money says so")

s = owner.get("/settings").get_data(as_text=True)
roles_in_select = s.split('name="role_id"')[1].split("</select>")[0]
check("Admin" in roles_in_select and "Employee" in roles_in_select and "Office" not in roles_in_select and "Installer" not in roles_in_select,
      "Settings offers only Admin and Employee")
owner.post("/settings/add-user", data=dict(CSRF, username="newhire", password="a-long-password-1", confirm="a-long-password-1", role_id="999"))
with app.app_context():
    check(m.User.query.filter_by(username="newhire").one().role.name == "Employee", "a new user is an Employee unless made an Admin")
r = owner.post("/settings/users/%d" % admin_id, data=dict(CSRF, role_id=str(employee_role.id), hourly_rate="0"), follow_redirects=True)
with app.app_context():
    check(db.session.get(m.User, admin_id).role.is_admin and "at least one Admin" in r.get_data(as_text=True), "the last Admin can't be made an Employee")
check("Two roles" in owner.get("/settings/roles").get_data(as_text=True), "Roles explains the two roles")
done()
