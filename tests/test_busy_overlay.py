"""While a page waits for an answer after a button sends a form, a loading overlay says what's happening and blocks other
clicks (Generate Quote's Use this says "Pricing it from the Pricing Sheet…"). It doesn't show for a confirm box that was
cancelled or for forms that save in place. Browser only."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
m.calculate_estimated_drive_time = lambda a: (20, 10)
with app.app_context():
    item = m.PriceItem(name="Halo 6in LED wafer light", lookup_key="halo6", unit="ea", price=24, cooper_part_number="HLB6"); db.session.add(item)
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen", job_number=880, address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    other = m.User(username="second", role=m.Role.query.filter_by(is_admin=True).first()); other.set_password("a-long-password-1"); db.session.add(other)
    db.session.commit(); job_id = job.id
owner.get("/quoting/generate/%d" % job_id)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3
    for t, tier in enumerate(m.quote_tiers(3)):
        o = m.QuoteOption(tier=tier, title="O%d" % t, description="", position=t); q.options.append(o)
        o.lines.append(m.QuoteOptionService(service=None, service_name="6in LED wafer light", example_quantity="Pricing pending", multiplier=6, unit_price=0,
                                            material_unit_price=0, labor_hours_per_unit=0, labor_rate_snapshot=90, position=0))
    db.session.commit()
real = app.view_functions["use_sheet_item"]


def slow_use(*args, **kwargs):
    time.sleep(1.5)                                    # a slow answer, as on a busy server (the overlay waits a quarter second)
    return real(*args, **kwargs)


app.view_functions["use_sheet_item"] = slow_use
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed"); done()
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
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/quoting/generate/%d" % job_id)
    # the answer goes to a hidden frame here, so the page stays put and can be looked at while it "waits"
    page.evaluate("""() => { const f = document.createElement('iframe'); f.name = 'hold'; f.hidden = true; document.body.append(f);
                             document.querySelector('.needs-price-form').target = 'hold'; }""")
    page.locator(".needs-price-card button", has_text="Use this").first.click()
    overlay = page.locator(".busy-overlay")
    overlay.wait_for(state="visible", timeout=3000)
    check("Pricing it from the Pricing Sheet" in overlay.inner_text(), "Use this shows a loading overlay that says what's happening")
    covered = page.evaluate("(() => { const e = document.elementFromPoint(200, 200); return !!(e && e.closest('.busy-overlay')); })()")
    check(covered, "and covers the page so nothing else can be clicked")
    page.wait_for_timeout(2500)                         # the slow answer finishes
    page.goto(base + "/quoting/generate/%d" % job_id)
    check(page.locator(".busy-overlay:not([hidden])").count() == 0, "the next page arrives without it")
    with app.app_context():
        check(all(not line.is_missing for o in m.Quote.query.filter_by(job_id=job_id).first().options for line in o.lines), "and the item was priced")
    page.goto(base + "/settings")
    page.once("dialog", lambda d: d.dismiss())
    page.locator("#users form[data-keep-scroll] button", has_text="Remove").first.click()
    page.wait_for_timeout(600)
    check(page.locator(".busy-overlay:not([hidden])").count() == 0, "a cancelled confirm box shows no overlay")
    row = page.locator("#users tr[id^=user-row]").first
    row.locator("button", has_text="Save").click()
    page.wait_for_timeout(700)
    check(page.locator(".busy-overlay:not([hidden])").count() == 0, "a form that saves in place shows no overlay")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
