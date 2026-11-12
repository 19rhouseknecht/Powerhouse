"""How quotes read to a client: scope phrases only where they fit, trade terms in plain words, and the
Generate quote list (only jobs in the works, searchable, long descriptions folded with Read more)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db = m.app, m.db
bullet = m.professionalize_scope_bullet

CASES = [
    ("Permit", "Permit"),
    ("Electrical permit and township inspection", "Electrical permit and township inspection"),
    ("Surge protection for the whole home", "Surge protection for the whole home"),
    ("Install GFCI outlets in the kitchen", "Turnkey installation of water-protection receptacles in the kitchen"),
    ("install 2 GFCI receptacles.", "Turnkey installation of 2 water-protection receptacles"),
    ("Replace AFCI breakers", "Replacement of fire-protection breakers"),
    ("Install dual function breakers for bedrooms", "Turnkey installation of fire- and water-protection breakers for bedrooms"),
    ("Install whole-house SPD", "Turnkey installation of whole-home surge protector"),
    ("Run 12/2 Romex to the garage", "Turnkey installation of new wiring to the garage"),
    ("Add tamper-resistant receptacles", "Turnkey installation of child-safe receptacles"),
    ("Install EV disconnect", "Turnkey installation of outdoor shut-off switch"),
    ("Replace load center", "Replacement of electrical panel"),
    ("Install generator interlock kit", "Turnkey installation of generator safety switch"),
    ("Install WR receptacle with in-use cover on patio", "Turnkey installation of weatherproof outdoor receptacle on patio"),
    ("Check grounding electrode and bonding", "Safety inspection of grounding system and bonding"),
    ("We will install a new meter base", "Turnkey installation of new electric meter housing"),
    ("Premium installation of designer LED fixtures", "Premium installation of designer LED fixtures"),
    ("Turnkey installation of a Level 2 EV charger", "Turnkey installation of a Level 2 EV charger"),
    ("Upgrade 100A panel to 200A", "Upgrade of 100A panel to 200A"),
]
for raw, want in CASES:
    got = bullet(raw)
    check(got == want, "%r reads %r (got %r)" % (raw, want, got))
    check(bullet(got) == got, "%r stays the same when cleaned again" % want)
check(bullet("   ") == "", "an empty bullet is dropped")
check(m.plain_terms("Adds GFCI protection and a new load center.") == "Adds water protection and a new electrical panel.", "plain words in a sentence")
check(m.plain_terms("Two GFCI outlets") == "Two water-protection receptacles", "plurals stay plural")

with app.app_context():
    caden = m.AiAgent.query.filter(db.func.lower(m.AiAgent.name) == "caden").first()
    check("CLIENT WORDING (plain language):" in caden.prompt and "water-protection receptacle" in caden.prompt, "a new install gives Caden the plain-language rules")
    check("CLIENT PRESENTATION WORDING:" not in caden.prompt, "and not the old block")

    person = m.Client(first_name="Ann", last_name="Lee")
    db.session.add(person); db.session.flush()
    jobs = {}
    for i, (title, status, desc) in enumerate([
            ("Kitchen outlets", "new_request", "Outlets by the sink stop working. " * 30),
            ("Garage subpanel", "working_quote", "Wants a subpanel."),
            ("EV charger", "awaiting_decision", "Tesla charger"),
            ("Panel swap", "scheduled", "Done deal"),
            ("Old job", "completed", "")]):
        jobs[title] = m.Job(client=person, title=title, job_number=700 + i, status=status, description=desc)
        db.session.add(jobs[title])
    db.session.flush()
    quoted = m.Quote(job=jobs["EV charger"], status="confirmed", title="EV — options", option_count=3)
    db.session.add(quoted); db.session.flush()

    # what Caden sends back is cleaned before it is saved
    tiers = m.quote_tiers(3)
    parts = [m.PriceItem(name=n, lookup_key=n.lower(), unit="ea", price=p) for n, p in (("GFCI receptacle", 22), ("AFCI breaker", 48), ("Surge protector", 160))]
    db.session.add_all(parts); db.session.flush()
    pid = [p.id for p in parts]
    m.apply_ai_quote_options(quoted, [
        {"tier": tiers[0], "title": "Basic GFCI fix", "description": "Swaps the GFCI by the sink.", "bullets": ["Replace GFCI outlet", "Permit", "  "],
         "services": [], "items": [{"price_item_id": pid[0], "quantity": 1}], "direct_priced_items": [], "missing_services": ["GFCI receptacle"]},
        {"tier": tiers[1], "title": "Kitchen protection", "description": "Adds AFCI protection.", "bullets": ["Replace GFCI outlet", "Install AFCI breakers"],
         "services": [], "items": [{"price_item_id": pid[0], "quantity": 1}, {"price_item_id": pid[1], "quantity": 2}], "direct_priced_items": [], "missing_services": []},
        {"tier": tiers[2], "title": "Whole-home protection", "description": "Adds an SPD.", "bullets": ["Replace GFCI outlet", "Install AFCI breakers", "Install whole-house SPD"],
         "services": [], "items": [{"price_item_id": pid[0], "quantity": 1}, {"price_item_id": pid[1], "quantity": 2}, {"price_item_id": pid[2], "quantity": 1}], "direct_priced_items": [], "missing_services": []}])
    db.session.commit()
    first = sorted(quoted.options, key=lambda o: o.position)[0]
    check(json.loads(first.bullets_json) == ["Replacement of water-protection receptacle", "Permit"], "Caden's bullets are saved in plain words, blanks dropped: %s" % first.bullets_json)
    check(first.title == "Basic water-protection receptacle fix" and first.description == "Swaps the water-protection receptacle by the sink.", "titles and descriptions too")
    quote_id = quoted.id
    ids = {t: j.id for t, j in jobs.items()}

client = sign_in(m)
page = client.get("/quotes/%d" % quote_id).get_data(as_text=True)
check("Premium installation of Permit" not in page and ">Permit<" in page, "the client presentation shows a permit as a permit")

page = client.get("/quoting/generate").get_data(as_text=True)
check("Kitchen outlets" in page and "Garage subpanel" in page, "Generate quote lists jobs in the works")
check("EV charger" not in page and "Panel swap" not in page and "Old job" not in page, "and not jobs already quoted, active or completed")
check(page.count("Outlets by the sink stop working.") == 30, "a long description is all there (folded by the page, not cut): %d" % page.count("Outlets by the sink stop working."))
check("data-read-more" in page and 'name="q"' in page, "with Read more and a search box")
page = client.get("/quoting/generate?q=subpanel").get_data(as_text=True)
check("Garage subpanel" in page and "Kitchen outlets" not in page, "search finds by description")
page = client.get("/quoting/generate?q=%23700").get_data(as_text=True)
check("Kitchen outlets" in page and "Garage subpanel" not in page, "search finds by job number")
page = client.get("/quoting/generate?q=zzz").get_data(as_text=True)
check("No jobs match" in page, "an empty search says so")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright:
    import logging
    import threading
    from werkzeug.serving import make_server
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.config["TESTING"] = False
    with app.app_context():
        user = m.User(username="viewer", role=m.Role.query.filter_by(is_admin=True).first())
        user.set_password("a-long-password-1"); db.session.add(user); db.session.commit()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_port
    with sync_playwright() as pw:
        try:
            exe = os.environ.get("PW_CHROMIUM") or None
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        except Exception:  # noqa: BLE001
            browser = None
        if browser:
            for width in (1440, 390):
                page = browser.new_page(viewport={"width": width, "height": 900})
                page.goto(base + "/login"); page.fill("input[name=username]", "viewer"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
                page.goto(base + "/quoting/generate")
                card = page.locator(".quote-job-card", has_text="Kitchen outlets")
                text = card.locator(".quote-job-description")
                lines = text.evaluate("e => Math.round(e.clientHeight / parseFloat(getComputedStyle(e).lineHeight))")
                check(lines == 3, "%dpx: a long description shows 3 lines (%s)" % (width, lines))
                more = card.locator("[data-read-more]")
                check(more.is_visible(), "%dpx: Read more appears for a long description" % width)
                check(not page.locator(".quote-job-card", has_text="Garage subpanel").locator("[data-read-more]").is_visible(), "%dpx: not for a short one" % width)
                build = card.locator(".quote-job-build")
                box = build.bounding_box()
                check(box is not None and box["x"] + box["width"] <= width, "%dpx: Build quote is on screen" % width)
                more.click()
                check(text.evaluate("e => e.scrollHeight <= e.clientHeight + 2") and more.inner_text() == "Show less", "%dpx: Read more opens the whole description" % width)
                check(page.url.endswith("/quoting/generate"), "%dpx: and does not open the job" % width)
                card.locator(".quote-job-title").click()
                check("/quoting/generate/%d" % ids["Kitchen outlets"] in page.url, "%dpx: the card opens the quote assistant" % width)
                page.close()
            browser.close()
    server.shutdown()
done()
