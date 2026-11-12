"""Clients sort by first or last name; the onsite checklist lists only jobs in the works; Tools sits under Quoting;
the sidebar keeps its scroll position from page to page (browser check, skipped without a browser)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
with app.app_context():
    people = [m.Client(first_name=f, last_name=l) for f, l in (("Zoe", "Adams"), ("Adam", "Young"), ("mia", "baker"))]
    db.session.add_all(people); db.session.flush()
    for i, (title, status) in enumerate((("Needs review", "new_request"), ("Quoted one", "awaiting_decision"), ("Doing it", "scheduled"))):
        db.session.add(m.Job(client=people[i], title=title, job_number=700 + i, status=status))
    db.session.flush()
    db.session.add(m.Quote(job=m.Job.query.filter_by(title="Quoted one").one(), status="confirmed"))
    db.session.commit()
client = sign_in(m)


def order(sort):
    page = client.get("/clients?sort=" + sort).get_data(as_text=True)
    found = [name for name in ("Zoe Adams", "Adam Young", "mia baker")]
    return sorted(found, key=page.index)


check(order("last") == ["Zoe Adams", "mia baker", "Adam Young"], "clients sort by last name A–Z (ignoring capitals)")
check(order("last_desc") == ["Adam Young", "mia baker", "Zoe Adams"], "last name Z–A")
check(order("first") == ["Adam Young", "mia baker", "Zoe Adams"], "first name A–Z")
check(order("first_desc") == ["Zoe Adams", "mia baker", "Adam Young"], "first name Z–A")
check('name="sort"' in client.get("/clients").get_data(as_text=True), "with a sort menu")

page = client.get("/sales/onsite-checklist").get_data(as_text=True)
check("Needs review" in page and "Quoted one" not in page and "Doing it" not in page, "the onsite checklist lists only jobs in the works")

page = client.get("/").get_data(as_text=True)
services, tools, clients_section = page.index(">⚡</span> Services"), page.index("</span> Tools</a>"), page.index("Clients &amp; Jobs</div>")
check(services < tools < clients_section, "Tools is under Quoting, right below Services")
check(page.count("</span> Tools</a>") == 1, "and only there")
crew = sign_in(m, "crew", admin=False)
check("</span> Tools</a>" not in crew.get("/").get_data(as_text=True), "non-admins still do not see Tools")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed"); done()
import logging
import threading
from werkzeug.serving import make_server
logging.getLogger("werkzeug").setLevel(logging.ERROR)
app.config["TESTING"] = False
with app.app_context():
    viewer = m.User(username="viewer", role=m.Role.query.filter_by(is_admin=True).first()); viewer.set_password("a-long-password-1")
    db.session.add(viewer); db.session.commit()
server = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
with sync_playwright() as pw:
    try:
        exe = os.environ.get("PW_CHROMIUM") or None
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as error:  # noqa: BLE001
        skip("no browser (%s)" % str(error).splitlines()[0][:80]); done()
    page = browser.new_page(viewport={"width": 1280, "height": 520})
    page.goto(base + "/login"); page.fill("input[name=username]", "viewer"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/clients")
    page.evaluate("document.getElementById('sidebar').scrollTop = 220")
    page.wait_for_timeout(100)
    before = page.evaluate("document.getElementById('sidebar').scrollTop")
    page.locator("#sidebar a", has_text="Jobs").first.click()
    page.wait_for_load_state("load")
    after = page.evaluate("document.getElementById('sidebar').scrollTop")
    check(before > 0 and abs(after - before) <= 2, "the sidebar stays where it was after opening another page (%s → %s)" % (before, after))
    browser.close()
server.shutdown()
done()
