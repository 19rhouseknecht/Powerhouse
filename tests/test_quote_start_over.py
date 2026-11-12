"""Start over on a quote: a red button at the bottom deletes the draft with everything on it (Caden's conversation, options,
lines, what needed a price, the lighting room) and a fresh empty draft takes its place. Confirmed versions, the job and the
Pricing Sheet are untouched. Browser half: the button asks first, then starts fresh."""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    person = m.Client(first_name="Jo", last_name="Park"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen lights", job_number=600, description="New lights."); db.session.add(job)
    item = m.PriceItem(name="6in wafer 1200 lm", lookup_key="w6", category="Lighting", unit="ea", price=24)
    db.session.add(item); db.session.commit()
    job_id, item_id = job.id, item.id
owner = sign_in(m)
owner.get("/quoting/generate/%d" % job_id)
with app.app_context():
    owner_id = m.User.query.filter_by(username="owner").one().id
    draft = m.Quote.query.filter_by(job_id=job_id, status="draft").one()
    draft.messages.append(m.QuoteMessage(role="user", content="Nine wafers in the kitchen, 14x12."))
    draft.messages.append(m.QuoteMessage(role="assistant", content="Here are your options."))
    draft.pricing_needed_json = json.dumps({"dimmer": {"name": "Dimmer", "suggested_material_price": 30}})
    draft.lighting_json = json.dumps(m.lighting.read_room({"type": "kitchen", "length": 14, "width": 12, "height": 9}))
    option = m.QuoteOption(tier="Gold", title="Wafers", bullets_json="[]", position=0)
    option.lines.append(m.QuoteOptionService(service_name="6in wafer 1200 lm", price_item_id=item_id, multiplier=9, unit_price=24))
    draft.options.append(option)
    confirmed = m.Quote(job=job, created_by_user_id=owner_id, status="confirmed", revision=1, title="Earlier version")
    confirmed.messages.append(m.QuoteMessage(role="user", content="Old conversation"))
    db.session.add(confirmed); db.session.commit()
    draft_id, confirmed_id = draft.id, confirmed.id

page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
check('id="start-over"' in page and 'class="btn btn-danger">Start over</button>' in page and page.index('id="start-over"') > page.index("Live draft"),
      "the quote page has a red Start over button at the bottom")
check("can’t be undone" in page or "can&#8217;t be undone" in page, "it asks before deleting anything")
import glob, re  # noqa: E402
bad = [path for path in glob.glob(os.path.join(m.basedir, "templates", "*.html"))
       if (lambda found: found and "<" in found.group(1))(re.search(r"\{% block title %\}(.*?)\{% endblock %\}", open(path).read(), re.S))]
check(not bad, "no page's title holds markup (a script or panel put there by mistake): %s" % bad)
traps = [path for path in glob.glob(os.path.join(m.basedir, "templates", "*.html")) for form in re.findall(r"<form\b.*?</form>", open(path).read(), re.S)
         if re.search(r'name="confirm"', form) and re.search(r'onsubmit="[^"]*(?<!window\.)\bconfirm\(', form)]
check(not traps, "no form asks confirm() while it has a field named confirm (the field would hide the question and submit at once): %s" % traps)

r = owner.post("/quotes/%d/start-over" % draft_id, data=CSRF)
with app.app_context():
    check(db.session.get(m.Quote, draft_id) is not None, "without the confirmation nothing is deleted")

r = owner.post("/quotes/%d/start-over" % draft_id, data=dict(CSRF, start_over="yes"))
check(r.status_code == 302 and r.headers["Location"].endswith("/quoting/generate/%d" % job_id), "Start over goes back to the quote")
with app.app_context():
    check(db.session.get(m.Quote, draft_id) is None, "the draft is gone")
    check(m.QuoteMessage.query.filter_by(quote_id=draft_id).count() == 0 and m.QuoteOption.query.filter_by(quote_id=draft_id).count() == 0
          and m.QuoteOptionService.query.count() == 0, "with its whole conversation, its options and their lines")
    old = db.session.get(m.Quote, confirmed_id)
    check(old is not None and old.status == "confirmed" and len(old.messages) == 1, "a confirmed version is untouched")
    check(db.session.get(m.PriceItem, item_id) is not None and db.session.get(m.Job, job_id) is not None, "the job and the Pricing Sheet are untouched")
page = owner.get("/quoting/generate/%d" % job_id).get_data(as_text=True)
with app.app_context():
    fresh = m.Quote.query.filter_by(job_id=job_id, status="draft").one()
    check(fresh.id != draft_id and [msg.role for msg in fresh.messages] == ["assistant"] and not fresh.options and not fresh.lighting_json
          and not fresh.pricing_needed_json, "a fresh draft starts empty: only Caden's opening line, no options, nothing remembered")
check("Nine wafers in the kitchen" not in page and "Here are your options." not in page, "nothing of the old conversation is shown")
check(owner.post("/quotes/%d/start-over" % confirmed_id, data=dict(CSRF, start_over="yes")).status_code == 302 and m.app.app_context and True, "(a confirmed one is refused)")
with app.app_context():
    check(db.session.get(m.Quote, confirmed_id) is not None, "a confirmed version is never started over")
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_quoting = False; db.session.commit()
    fresh_id = m.Quote.query.filter_by(job_id=job_id, status="draft").one().id
check(crew.post("/quotes/%d/start-over" % fresh_id, data=dict(CSRF, start_over="yes")).status_code == 403, "starting over needs quoting access")
check(owner.post("/quotes/%d/start-over" % fresh_id, data={"start_over": "yes"}).status_code == 400, "and the CSRF token")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
with app.app_context():
    q = db.session.get(m.Quote, fresh_id); q.messages.append(m.QuoteMessage(role="user", content="Something to forget")); db.session.commit()
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
    page.goto(base + "/quoting/generate/%d" % job_id)
    button = page.locator("#start-over button")
    check(button.evaluate("b => getComputedStyle(b).backgroundColor") == "rgb(220, 38, 38)", "the button is red")
    page.once("dialog", lambda dialog: dialog.dismiss())
    button.click(); page.wait_for_timeout(500)
    check("Something to forget" in page.content(), "cancelling the question keeps everything")
    page.once("dialog", lambda dialog: dialog.accept())
    button.click(); page.wait_for_load_state()
    check("Something to forget" not in page.content() and "Started over" in page.content(), "accepting it starts fresh")
    browser.close()
done()
