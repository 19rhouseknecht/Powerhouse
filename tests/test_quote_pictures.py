"""Generate Quote shows each material's Pricing Sheet picture: beside the likely matches and search results in Needs a
price, and on the options' lines. Admins can send the likely matches to Update from Cooper (pictures and prices) from there.
Browser half: the pictures and the button."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
with app.app_context():
    pictured = m.PriceItem(name="Halo 6in LED wafer light", lookup_key="halo6", unit="ea", price=24, cooper_part_number="HLB6", image_path="img/logo-wordmark.png")
    plain = m.PriceItem(name="RAB 6in LED wafer light", lookup_key="rab6", unit="ea", price=19, cooper_part_number="SLIM6")
    breaker = m.PriceItem(name="20A breaker", lookup_key="qo120", unit="ea", price=9, cooper_part_number="QO120")
    db.session.add_all([pictured, plain, breaker])
    person = m.Client(first_name="Kim", last_name="Doe"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Kitchen", job_number=880, address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543"); db.session.add(job)
    db.session.commit(); job_id = job.id; ids = {"pictured": pictured.id, "plain": plain.id, "breaker": breaker.id}
m.calculate_estimated_drive_time = lambda a: (20, 10)
url = "/quoting/generate/%d" % job_id
owner.get(url)
with app.app_context():
    q = m.Quote.query.filter_by(job_id=job_id).first(); q.option_count = 3
    for t, tier in enumerate(m.quote_tiers(3)):
        option = m.QuoteOption(tier=tier, title="Option %d" % t, description="", position=t); q.options.append(option)
        option.lines.append(m.QuoteOptionService(service=None, service_name="6in LED wafer light", example_quantity="Pricing pending", multiplier=6,
                                                 unit_price=0, material_unit_price=0, labor_hours_per_unit=0, labor_rate_snapshot=90, position=0))
        option.lines.append(m.QuoteOptionService(price_item_id=ids["breaker"], service_name="20A breaker", example_quantity="1 ea", multiplier=1,
                                                 unit_price=9, material_unit_price=9, labor_hours_per_unit=0, labor_rate_snapshot=90, position=1))
    db.session.commit()
page = owner.get(url).get_data(as_text=True)
needs = page.split('id="needs-price"')[1].split("</section>")[0]
check('class="pick-thumb" src="/static/img/logo-wordmark.png' in needs and "pick-thumb-none" in needs, "Needs a price shows each likely match's picture (or that it has none)")
check("Cooper #SLIM6" in needs and "data-cooper-update" in needs and "Update all matches from Cooper" in needs, "with its Cooper number and Update from Cooper")
check("line-thumb-none" in page, "an option's line from the Pricing Sheet with no picture shows that it has none")
found = owner.get("/quoting/pricing/find.json?q=wafer").json["results"]
check(any(r["image"].endswith("logo-wordmark.png") for r in found) and any(r["image"] == "" for r in found if r["kind"].startswith("Pricing")), "the search gives each item's picture")
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_quoting = True; role.view_jobs = True; db.session.commit()
check("data-cooper-update" not in crew.get(url).get_data(as_text=True), "Update from Cooper is for admins only")

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
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + url)
    card = page.locator(".needs-price-card").first
    check(card.locator("img.pick-thumb").count() >= 1 and card.locator("img.pick-thumb").first.evaluate("i => i.complete && i.naturalWidth > 0"), "the picture shows in the browser")
    card.locator("[data-sheet-search] input").fill("wafer")
    card.locator("[data-results] .pick-thumb").first.wait_for(timeout=5000)
    check(card.locator("[data-results] img.pick-thumb").count() >= 1, "search results show pictures too")
    card.locator(".cooper-refresh").click()
    card.locator("[data-cooper-said]", has_text="Sent to the office listener").wait_for(timeout=5000)
    with app.app_context():
        run = m.PriceSyncRun.query.order_by(m.PriceSyncRun.id.desc()).first()
        check(run is not None and sorted(run.item_ids) == sorted([ids["pictured"], ids["plain"]]), "Update from Cooper sends just those items to the office listener")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
