"""Caden builds a quote in the background: the page comes back at once (PythonAnywhere ends a page that waits too long), shows
him working, refuses a second message meanwhile, and shows the options when he is done. A run that went quiet is ended and
said. Browser half: the working bubble follows him and the page reloads with the options."""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
app.config["QUOTE_SYNC"] = False                       # the real thing: a thread
GO, CALLS = threading.Event(), []


class SlowOpenAI:
    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        CALLS.append(1)
        GO.wait(20)                                    # Caden "thinking" until the test lets him finish
        options = [{"tier": tier, "title": "Option %d" % i, "description": "Lights.", "bullets": ["Work %d" % i], "services": [], "items": [],
                    "direct_priced_items": [], "missing_services": [{"name": "Recessed light %d" % i, "quantity": i + 1, "unit": "ea"}]}
                   for i, tier in enumerate(TIERS)]
        return SimpleNamespace(output_text=json.dumps({"ready": True, "message": "Built your options.", "questions": [], "quote_title": "Lights",
                                                       "introduction": "Hi.", "mike_question": "", "options": options, "pricing_needed": []}))


m.OpenAI = SlowOpenAI
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
with app.app_context():
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen lighting", job_number=880, description="Recessed lights."); db.session.add(job); db.session.commit()
    job_id = job.id
owner = sign_in(m)
url = "/quoting/generate/%d" % job_id
owner.get(url)
with app.app_context():
    quote = m.Quote.query.filter_by(job_id=job_id).first(); quote.option_count = 3; db.session.commit(); quote_id = quote.id
    TIERS = m.quote_tiers(3)


def wait_until(test, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if test():
            return True
        time.sleep(0.1)
    return False


started = time.time()
r = owner.post(url, data=dict(CSRF, message="Six recessed lights in the kitchen"))
check(r.status_code == 302 and time.time() - started < 3, "sending a message comes back at once, while Caden is still working")
check(wait_until(lambda: CALLS), "Caden is working in the background")
page = owner.get(url).get_data(as_text=True)
check("Building your options" in page and "data-caden-poll" in page and "Caden is working…" in page, "the page shows him working, with Send held")
status = owner.get("/quotes/%d/caden-status" % quote_id).json
check(status["working"] and status["seconds"] >= 0, "the status says he is working")
owner.post(url, data=dict(CSRF, message="Also a ceiling fan"))
with app.app_context():
    check(not any(msg.content == "Also a ceiling fan" for msg in db.session.get(m.Quote, quote_id).messages), "a second message waits until he is done")
GO.set()
check(wait_until(lambda: not owner.get("/quotes/%d/caden-status" % quote_id).json["working"]), "when he is done the status says so")
with app.app_context():
    q = db.session.get(m.Quote, quote_id)
    check(len(q.options) == 3 and any(msg.content == "Built your options." for msg in q.messages), "and the options are on the quote")
page = owner.get(url).get_data(as_text=True)
check("Building your options" not in page and "Option 2" in page, "the page shows the options, not the working bubble")

with app.app_context():                                # a run that went quiet: the web app restarted under it
    q = db.session.get(m.Quote, quote_id); q.ai_working_since = datetime.utcnow() - timedelta(minutes=m.CADEN_STALE_MINUTES + 1); db.session.commit()
check(not owner.get("/quotes/%d/caden-status" % quote_id).json["working"], "a run that went quiet for too long is ended")
with app.app_context():
    check("web app restarted" in db.session.get(m.Quote, quote_id).messages[-1].content, "and the chat says to send the message again")


class BrokenOpenAI(SlowOpenAI):
    def create(self, **kwargs):
        raise RuntimeError("boom")


m.OpenAI = BrokenOpenAI
owner.post(url, data=dict(CSRF, message="Try again"))
check(wait_until(lambda: not owner.get("/quotes/%d/caden-status" % quote_id).json["working"]), "an AI error ends the run")
with app.app_context():
    check("nothing changed" in db.session.get(m.Quote, quote_id).messages[-1].content, "and says nothing changed")

m.OpenAI = SlowOpenAI; GO.clear(); CALLS.clear()
r = owner.post("/quotes/%d/rework" % quote_id, data=CSRF)
check(r.status_code == 302 and wait_until(lambda: CALLS) and owner.get("/quotes/%d/caden-status" % quote_id).json["working"], "Rework all options also runs in the background")
GO.set()
check(wait_until(lambda: not owner.get("/quotes/%d/caden-status" % quote_id).json["working"]), "and finishes")

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
    GO.clear(); CALLS.clear()
    page.goto(base + url)
    page.fill("textarea[name=message]", "Make option 3 brighter")
    page.locator(".chat-form button.btn-primary").click()
    page.wait_for_load_state()
    check(page.locator(".caden-working").is_visible(), "the working bubble shows right after sending")
    page.locator("[data-caden-time]", has_text="so far").wait_for(timeout=8000)
    check(True, "and counts the time")
    GO.set()
    page.wait_for_function("!document.querySelector('.caden-working')", timeout=15000)
    check("Built your options." in page.content(), "when he is done the page reloads with his answer")
    browser.close()
done()
