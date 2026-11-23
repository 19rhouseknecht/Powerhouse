"""The round + on every page and Upload expense: the AI reads a receipt (store, total, date, accounting code), it can be
reimbursable to the person signed in, and it is linked to today's job unless another is picked. Employees can upload but
don't see Finance or the Expenses list. Browser half: the button, the menu, the form filled from a receipt, saving."""
import base64
import io
import json
import os
import sys
import threading
from datetime import date, datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
m = make_site()
app, db = m.app, m.db
CALLS = []


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        CALLS.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"vendor": "Home Depot", "total": 84.37, "date": "2026-11-20", "code": "Materials direct job",
                                                       "summary": "PVC conduit and fittings"}))


m.OpenAI = FakeOpenAI
owner = sign_in(m)
with app.app_context():
    _, employee_role = m.two_roles()
    worker = m.User(username="caden", role=employee_role); worker.set_password("a-long-password-1"); db.session.add(worker)
    person = m.Client(first_name="Ann", last_name="Lee"); db.session.add(person); db.session.flush()
    today_job = m.Job(client=person, title="Panel upgrade", job_number=500, address_line1="1 A St", city="Lititz", state="PA", postal_code="17543",
                      assigned_user_id=worker.id, work_start_date=date.today(), duration_days=1, status="scheduled")
    other_job = m.Job(client=person, title="Hot tub", job_number=501, address_line1="1 A St", city="Lititz", state="PA", postal_code="17543")
    db.session.add_all([today_job, other_job]); db.session.commit()
    worker_id, today_id, other_id = worker.id, today_job.id, other_job.id
crew = m.app.test_client()
with crew.session_transaction() as sess:
    sess["_user_id"] = str(worker_id); sess["_fresh"] = True; sess["csrf_token"] = "test-token"

check(crew.get("/finance").status_code == 403 and crew.get("/finance/expenses").status_code == 403, "an Employee can't open Finance or the Expenses list")
page = crew.get("/jobs").get_data(as_text=True)
check("Finance</div>" not in page and "data-quick-fab" in page and "Upload expense" in page, "an Employee's menu has no Finance, and every page has the round +")
check("data-quick-fab" not in m.app.test_client().get("/login").get_data(as_text=True), "not on the sign-in page")
options = crew.get("/quick/expense-options").json
check(options["codes"] == ["Equipment rental", "Van gas", "Van maintenance", "Materials direct job", "Materials general overhead", "Hotel",
                           "Small tools general overhead"], "the accounting codes")
check([j["id"] for j in options["today"]] == [today_id] and other_id in [j["id"] for j in options["jobs"]], "today's job comes first, the open jobs after")
os.environ.pop("OPENAI_API_KEY", None)
check(crew.post("/quick/read-receipt", data=dict(CSRF, receipt=(io.BytesIO(PNG), "r.png")), content_type="multipart/form-data").status_code == 503,
      "without the AI it says to fill it in")
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
read = crew.post("/quick/read-receipt", data=dict(CSRF, receipt=(io.BytesIO(PNG), "r.png")), content_type="multipart/form-data").json
check(read == {"ok": True, "vendor": "Home Depot", "total": 84.37, "date": "2026-11-20", "code": "Materials direct job", "summary": "PVC conduit and fittings"},
      "the AI reads the store, total, date and accounting code")
check(CALLS[-1]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,"), "from the photo")
r = crew.post("/quick/expense", data=dict(CSRF, vendor="Home Depot", total="84.37", expense_date="2026-11-20", category="Materials direct job",
                                          reimbursable="yes", job_id=str(today_id), receipt=(io.BytesIO(PNG), "r.png")),
              content_type="multipart/form-data", headers={"X-Requested-With": "fetch"})
with app.app_context():
    e = m.Expense.query.one()
    check(r.json["ok"] and (e.submitted_by_user_id, e.job_id, e.total, e.category, e.reimbursable, e.expense_date) == (worker_id, today_id, 84.37, "Materials direct job", True, date(2026, 11, 20)),
          "saved: theirs, on the job, reimbursable")
    check(e.notes.startswith("Reimburse to caden") and e.receipt_filename, "marked to be paid back to them, with the receipt")
r = crew.post("/quick/expense", data=dict(CSRF, vendor="Sheetz", total="40", category="Meals", reimbursable="no"), headers={"X-Requested-With": "fetch"})
check(r.status_code == 400, "an accounting code must be one of the seven")
r = crew.post("/quick/expense", data=dict(CSRF, vendor="Sheetz", total="40", category="Van gas", reimbursable="no"), headers={"X-Requested-With": "fetch"})
with app.app_context():
    gas = m.Expense.query.filter_by(vendor="Sheetz").one()
    check(r.json["ok"] and gas.job_id is None and not gas.reimbursable, "an expense with no job and not reimbursable")
check("84.37" in owner.get("/finance/expenses").get_data(as_text=True), "an Admin sees it in Expenses")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
import tempfile  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
app.config["TESTING"] = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
receipt = os.path.join(tempfile.mkdtemp(), "receipt.png"); open(receipt, "wb").write(PNG)
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "caden"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/jobs")
    fab = page.locator("[data-quick-fab]")
    box = fab.bounding_box()
    check(box["x"] + box["width"] > 340 and box["y"] + box["height"] > 780, "the round + sits in the bottom-right corner")
    fab.click()
    check(page.locator("[data-quick-menu]").is_visible(), "it opens the quick-action menu")
    page.click("[data-quick-open=quick-expense]")
    page.locator("[data-quick-jobs] optgroup[label=Today] option").first.wait_for(state="attached", timeout=5000)
    check(page.locator("#quick-expense").is_visible() and page.input_value("[data-quick-jobs]") == str(today_id), "Upload expense opens with today's job picked")
    page.set_input_files("[data-quick-receipt]", receipt)
    page.locator("[data-quick-read]", has_text="Check it").wait_for(timeout=5000)
    check(page.input_value("input[name=vendor]") == "Home Depot" and page.input_value("input[name=total]") == "84.37" and page.input_value("select[name=category]") == "Materials direct job",
          "the photo fills in the store, total and accounting code")
    page.click(".quick-reimburse label:has-text('pay me back')")
    page.click("[data-quick-save]")
    page.locator("[data-quick-status]", has_text="Expense saved").wait_for(timeout=5000)
    with app.app_context():
        check(m.Expense.query.count() == 3 and m.Expense.query.order_by(m.Expense.id.desc()).first().reimbursable, "Save keeps it")
    check(page.evaluate("document.documentElement.scrollWidth") <= 391, "it fits a phone")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
