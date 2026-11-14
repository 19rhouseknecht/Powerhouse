"""Describe what you need on a job's material order: the AI searches the Pricing Sheet and suggests only items it found
there (never an invented one), each with a quantity and why; Add puts it on the list. Browser half: the same in a real browser."""
import json
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
SCRIPT, SEEN = [], []


class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(kwargs)
        reply = SCRIPT.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, list):
            return SimpleNamespace(output=[SimpleNamespace(type="function_call", name=n, arguments=json.dumps(a), call_id="c%d" % i,
                                                           model_dump=lambda **k: {"type": "function_call"}) for i, (n, a) in enumerate(reply)], output_text="")
        return SimpleNamespace(output=[SimpleNamespace(type="message", model_dump=lambda **k: {"type": "message"})], output_text=reply)


m.OpenAI = FakeOpenAI
with app.app_context():
    person = m.Client(first_name="Dana", last_name="Smith"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Basement wiring", job_number=1042); db.session.add(job); db.session.flush()
    box = m.PriceItem(name="Single gang old work box", lookup_key="b114r", unit="ea", price=1.2, cooper_part_number="B114R")
    wire = m.PriceItem(name="12/2 NM-B wire", lookup_key="122", unit="ft", price=0.52, cooper_part_number="122NMB250")
    gone = m.PriceItem(name="Old work box retired", lookup_key="oldbox", unit="ea", price=2, active=False)
    db.session.add_all([box, wire, gone]); db.session.add(m.JobMaterialOrder(job=job)); db.session.commit()
    ids = {"box": box.id, "wire": wire.id, "gone": gone.id}
    job_id = job.id
client = sign_in(m)
url = "/jobs/%d/materials/suggest" % job_id


def answer(*picks, message=""):
    return json.dumps({"message": message, "suggestions": [{"price_item_id": i, "quantity": q, "why": w} for i, q, w in picks]})


os.environ.pop("OPENAI_API_KEY", None)
r = client.post(url, data=dict(CSRF, description="a box for a switch"))
check(r.status_code == 503 and "no OpenAI key" in r.json["message"], "without an OpenAI key it says so and points to the search")
os.environ["OPENAI_API_KEY"] = "test-key"
check(client.post(url, data=dict(CSRF, description="  ")).status_code == 400, "an empty description is refused")

SCRIPT[:] = [[("search_pricing_sheet", {"query": "old work box"})],
             answer((ids["box"], 4, "Old work box for a switch in an existing wall"), (ids["wire"], 50, "Never searched for"), (9999, 1, "Invented"))]
r = client.post(url, data=dict(CSRF, description="the plastic box for a switch in an existing wall, 4 of them"))
got = r.json["suggestions"]
check(r.status_code == 200 and [s["id"] for s in got] == [ids["box"]], "it suggests only Pricing Sheet items its search found (nothing invented, nothing unsearched)")
check(got[0]["quantity"] == 4 and got[0]["part"] == "B114R" and got[0]["price"] == 1.2 and "existing wall" in got[0]["why"], "with the quantity, price, Cooper part number and why")
check("Old work box retired" not in json.dumps(SEEN[1].get("input", "")), "the search leaves out items that are switched off")

SCRIPT[:] = [answer((ids["box"], 1, "Guess"))]
r = client.post(url, data=dict(CSRF, description="something"))
check(r.json["suggestions"] == [] and r.json["message"], "a pick it never searched for is dropped and it says it found nothing")

SCRIPT[:] = [RuntimeError("boom")]
check(client.post(url, data=dict(CSRF, description="something")).status_code == 502, "an AI error is a clear error, not a crash")

crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_jobs = False; db.session.commit()
check(crew.post(url, data=dict(CSRF, description="box")).status_code in (302, 403), "it needs access to Jobs")

page = client.get("/jobs/%d/materials" % job_id).get_data(as_text=True)
check("Describe what you need" in page and "data-material-suggest" in page, "the page has the Describe what you need box")

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
    page = browser.new_page()
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/jobs/%d/materials" % job_id)
    SCRIPT[:] = [[("search_pricing_sheet", {"query": "old work box"})], answer((ids["box"], 3, "Old work box for a switch"))]
    page.fill("#material-describe", "box for a switch in an old wall")
    page.click("[data-suggest-go]")
    pick = page.locator(".material-ai-pick", has_text="Single gang old work box")
    pick.wait_for(timeout=5000)
    check(pick.locator('input[name="quantity"]').input_value() == "3" and "Old work box for a switch" in pick.inner_text(), "the AI's pick shows with its quantity and why")
    pick.locator("button", has_text="Add").click()
    page.wait_for_load_state()
    check(page.locator(".material-row", has_text="Single gang old work box").count() == 1, "pressing Add puts it on the list")
    browser.close()
done()
