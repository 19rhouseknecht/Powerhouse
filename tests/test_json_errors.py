"""The Cooper buttons (and any fetch that asks for JSON) never end in 'Unexpected token "<"'. A signed-out request, an
out-of-date page, a missing record or a server error is answered in JSON with the reason in words; ordinary pages keep
redirecting to the login page as before. In a real browser the button then says what happened and keeps its own label."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db


@app.route("/test-only/boom", methods=["POST"])
def test_boom():
    raise RuntimeError("boom")


with app.app_context():
    db.session.add(m.PriceItem(name="Breaker", lookup_key="breaker", price=10, cooper_part_number="QO120"))
    db.session.commit()

JSON = {"Accept": "application/json"}
client = sign_in(m)
anon = app.test_client()
r = anon.post("/quoting/pricing/cooper-sync", data={"scope": "all"}, headers=JSON)
check(r.status_code == 401 and r.is_json and "signed out" in r.get_json()["error"], "signed out, the Cooper button gets JSON saying so (401)")
r = anon.get("/quoting/pricing")
check(r.status_code == 302 and "/login?next=" in r.headers["Location"], "an ordinary page still sends a signed-out person to the login page")
crew = sign_in(m, "crew", admin=False)
r = crew.post("/settings/releases/check", data=CSRF, headers=JSON)
check(r.status_code == 403 and r.is_json and "role" in r.get_json()["error"], "a role without access gets JSON saying so (403)")
r = client.post("/quoting/pricing/cooper-sync", data={"scope": "all", "csrf_token": "old-token"}, headers=JSON)
check(r.status_code == 400 and r.is_json and "out of date" in r.get_json()["error"], "an out-of-date page gets JSON: refresh and try again")
r = client.post("/quoting/pricing/cooper-sync", data={"scope": "all", "csrf_token": "old-token"})
check(r.status_code == 400 and not r.is_json, "a plain form post keeps the normal error page")
r = client.get("/quoting/pricing/cooper-sync/999/status", headers=JSON)
check(r.status_code == 404 and r.is_json, "a missing price check answers JSON")
app.config["TESTING"] = False
app.config["PROPAGATE_EXCEPTIONS"] = False
r = client.post("/test-only/boom", data=CSRF, headers=JSON)
check(r.status_code == 500 and r.is_json and "RuntimeError" in r.get_json()["error"], "a server error answers JSON naming the error")
r = client.post("/quoting/pricing/cooper-sync", data=dict(CSRF, scope="all"), headers=JSON)
check(r.status_code == 200 and r.is_json and r.get_json()["status_url"], "and a good request still starts the price check")
with app.app_context():
    m.PriceSyncRun.query.update({"status": "cancelled"}); db.session.commit()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)")
    done()

with app.app_context():
    user = m.User(username="browser", role=m.Role.query.filter_by(is_admin=True).first())
    user.set_password("a-long-password-1"); db.session.add(user); db.session.commit()
import logging  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_port

with sync_playwright() as pw:
    try:
        exe = os.environ.get("PW_CHROMIUM") or None
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        skip("no browser to drive (%s)" % str(exc).splitlines()[0][:120])
        done()
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    page.goto(BASE + "/login"); page.fill("input[name=username]", "browser"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(BASE + "/quoting/pricing")
    said = []
    page.on("dialog", lambda dialog: (said.append(dialog.message), dialog.accept()))
    ctx.clear_cookies()                                  # signed out while the page sat open
    button = page.locator("button", has_text="Update all prices")
    button.click(); page.wait_for_timeout(800)
    check(said and "signed out" in said[-1] and "Unexpected token" not in said[-1], "signed out: the button says to sign in again, not 'Unexpected token'")
    check(button.inner_text() == "Update all prices" and button.is_enabled(), "and the button keeps its own label")
    page.goto(BASE + "/login"); page.fill("input[name=username]", "browser"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(BASE + "/quoting/pricing")
    page.evaluate("document.querySelectorAll('[data-cooper-sync-form] input[name=csrf_token]').forEach(i => i.value = 'stale')")
    page.locator("button", has_text="Add all items from Cooper").click(); page.wait_for_timeout(800)
    check("out of date" in said[-1], "an out-of-date page: the button says to refresh")
    check(page.locator("button", has_text="Add all items from Cooper").count() == 1, "the catalog button keeps its label too")
    page.reload()
    page.locator("button", has_text="Update all prices").click()
    page.wait_for_selector("[data-cooper-bot]:not([hidden])", timeout=5000)
    check(page.locator("button", has_text="Price check queued").count() == 1, "with a fresh page the price check starts as before")
    browser.close()

done()
