"""Every page opens: each GET address in the app, for an admin, with a client, a job and a quote in
the database. A page may redirect or say "not found" for an id it does not have; it may never crash.
Admin pages refuse someone who is not an admin."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in  # noqa: E402

appmod = make_site()
app, db = appmod.app, appmod.db

with app.app_context():
    client_row = appmod.Client(first_name="Ann", last_name="Lee", email="ann@example.com", phone="555-0100",
                               address_line1="12 Main St", city="Lancaster", state="PA", postal_code="17601")
    db.session.add(client_row)
    db.session.flush()
    job = appmod.Job(client=client_row, title="Panel upgrade", job_number=700, address_line1="12 Main St",
                     city="Lancaster", state="PA", postal_code="17601")
    db.session.add(job)
    db.session.flush()
    quote = appmod.Quote(job=job, status="confirmed", title="Panel upgrade — Service Options")
    db.session.add(quote)
    db.session.flush()
    db.session.add(appmod.QuoteOption(quote=quote, tier="Gold", title="Gold", position=0))
    db.session.add(appmod.PriceItem(name="200A panel", lookup_key="200a panel", unit="ea", price=410.0))
    db.session.commit()

admin = sign_in(appmod, "owner", admin=True)
crew = sign_in(appmod, "crew", admin=False)

SKIP = {"static", "logout", "releases_github_hook"}
rules = sorted((r for r in app.url_map.iter_rules() if "GET" in r.methods and r.endpoint not in SKIP), key=lambda r: r.rule)
opened = crashed = 0
for rule in rules:
    args = {}
    for name in rule.arguments:
        conv = rule._converters.get(name)
        args[name] = 1 if conv is not None and conv.__class__.__name__ in ("IntegerConverter",) else "x"
    with app.test_request_context():
        try:
            from flask import url_for
            url = url_for(rule.endpoint, **args)
        except Exception:  # noqa: BLE001
            continue
    try:
        response = admin.get(url)
        ok = response.status_code < 500
        opened += response.status_code == 200
    except Exception:  # noqa: BLE001
        ok = False
        print(traceback.format_exc()[-1500:])
    crashed += not ok
    if not ok:
        check(False, "%s opens (%s)" % (url, rule.endpoint))
check(crashed == 0, "%d addresses tried, none crashed (%d answered with a page)" % (len(rules), opened))
check(opened >= 25, "at least 25 pages render for an admin (%d)" % opened)

for url in ("/", "/jobs", "/clients", "/jobs/%d" % 1, "/flow", "/settings/releases", "/settings"):
    response = admin.get(url)
    check(response.status_code == 200, "admin sees %s" % url)

for url in ("/settings", "/settings/releases", "/flow", "/settings/roles"):
    check(crew.get(url).status_code == 403, "non-admin is refused %s" % url)

html = admin.get("/").get_data(as_text=True)
check("Power House Flow" in html and "Releases" in html, "admin menu shows Power House Flow and Releases")
html = crew.get("/").get_data(as_text=True)
check("Power House Flow" not in html and "Releases" not in html, "non-admin menu hides them")
check("v=" in admin.get("/").get_data(as_text=True), "CSS and JS links carry the build (no stale styling after a deploy)")
done()
