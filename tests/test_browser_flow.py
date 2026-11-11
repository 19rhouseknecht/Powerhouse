"""Power House Flow in a real browser: the sample, editing, branching, connecting, undo, delete,
saving, reloading, a stale tab, and the phone layout. Skipped where no browser is installed
(GitHub installs Chromium for it; PW_CHROMIUM may name a browser binary)."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, skip  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed")
    done()

appmod = make_site()
app, db = appmod.app, appmod.db
app.config["TESTING"] = False
with app.app_context():
    user = appmod.User(username="owner", role=appmod.Role.query.filter_by(is_admin=True).first())
    user.set_password("a-long-password-1")
    db.session.add(user)
    db.session.commit()

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
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/login")
    page.fill("input[name=username]", "owner")
    page.fill("input[name=password]", "a-long-password-1")
    page.click("form button")
    page.goto(BASE + "/flow")

    check(page.is_visible("#flow-empty"), "an empty flow offers a first step or the sample")
    page.click("[data-flow=start-sample]")
    check(page.locator(".flow-node").count() == 16, "the sample flow has 16 steps")
    page.wait_for_selector("#flow-saved.is-ok", timeout=6000)
    check(True, "it saves by itself")
    boxes = page.eval_on_selector_all(".flow-node", "ns => ns.map(n => { const r = n.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })")
    overlaps = sum(1 for i, a in enumerate(boxes) for c in boxes[i + 1:] if a[0] < c[2] and c[0] < a[2] and a[1] < c[3] and c[1] < a[3])
    check(overlaps == 0, "no two steps overlap")

    page.click(".flow-node.kind-decision")
    title = page.locator(".flow-panel .flow-field input").first
    title.fill("Client decides")
    check(page.text_content(".flow-node.kind-decision .flow-node-title") == "Client decides", "a title edit shows on the chart as it is typed")
    count = page.locator(".flow-node").count()
    page.click(".flow-panel >> text=Add a branch")
    check(page.locator(".flow-node").count() == count + 1, "+ Add a branch adds a step")
    page.keyboard.type("Wants to think about it")
    page.click(".flow-panel >> text=Connect to a step")
    check(page.is_visible("#flow-linking"), "Connect to a step asks which step")
    page.click('.flow-node:has-text("Follow up later")')
    check(page.is_visible(".flow-panel >> text=Name on the arrow"), "clicking a step draws the arrow and selects it")
    page.keyboard.press("Escape")
    page.click("[data-flow=undo]")
    check(page.locator(".flow-edge-hit").count() == 17, "Undo takes the arrow back")
    page.click("[data-flow=redo]")
    check(page.locator(".flow-edge-hit").count() == 18, "Redo puts it back")

    page.click('.flow-node:has-text("Final walkthrough")')
    page.click(".flow-panel >> text=Delete step")
    check(page.locator('.flow-node:has-text("Final walkthrough")').count() == 0, "a step can be deleted")
    page.click('.flow-node:has-text("Do the work")')
    check("Invoice and collect payment" in page.text_content(".flow-panel"), "the steps around a deleted step stay joined")
    page.keyboard.press("Escape")
    page.wait_for_selector("#flow-saved.is-ok", timeout=6000)
    page.reload()
    check(page.locator(".flow-node").count() == count, "everything is still there after a reload")
    check(page.text_content(".flow-node.kind-decision .flow-node-title") == "Client decides", "including the edited title")

    other = ctx.new_page()
    other.goto(BASE + "/flow")
    other.click(".flow-node.kind-start")
    other.locator(".flow-panel .flow-field input").first.fill("Phone rings")
    other.wait_for_selector("#flow-saved.is-ok", timeout=6000)
    page.click(".flow-node.kind-start")
    page.locator(".flow-panel .flow-field input").first.fill("Stale edit")
    page.wait_for_selector("#flow-saved.is-error", timeout=6000)
    check("changed the flow" in page.text_content("#flow-saved"), "a stale tab is told to reload instead of overwriting")

    phone = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=ctx.storage_state()).new_page()
    phone.goto(BASE + "/flow")
    width = phone.evaluate("document.documentElement.scrollWidth")
    check(width <= 391, "no sideways scrolling on a phone (%dpx)" % width)
    check(phone.is_visible(".flow-node.kind-start"), "the first step is in view on a phone")
    check(not errors, "no script errors" + (": " + "; ".join(errors) if errors else ""))
    browser.close()
server.shutdown()
done()
