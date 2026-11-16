"""Tidier Pricing Sheet: Cooper, the Labor calculator (with the hourly rate) and Tools are small windows at its top and no longer
in the sidebar. Jobs: notes with pictures. Browser half: the Cooper window opens and closes; a note with a picture is added."""
import base64
import io
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
m = make_site()
app, db = m.app, m.db
owner = sign_in(m)

sheet = owner.get("/quoting/pricing").get_data(as_text=True)
check("Labor Calculator</a>" not in sheet and "> Tools</a>" not in sheet, "the sidebar no longer has Labor Calculator or Tools")
tiles = sheet.split('class="sheet-tiles"')[1].split("</dialog>")[0]
check('data-open-dialog="cooper-dialog"' in tiles and "Labor calculator</strong>" in tiles and "Tools</strong>" in tiles,
      "the top of the Pricing Sheet has Cooper, Labor calculator and Tools")
dialog = sheet.split('id="cooper-dialog"')[1].split("</dialog>")[0]
check("Cooper price update" in dialog and "Add all items from Cooper" in dialog, "the Cooper controls live in the Cooper window")
check("labor-rate-panel" not in sheet and "$" in sheet.split('class="sheet-tiles"')[1].split("</div>")[0], "the labor rate is not a box on the sheet; the Labor tile shows it")
labor_url = [part.split('"')[0] for part in sheet.split('class="sheet-tile" href="')[1:]][0]
labor = owner.get(labor_url).get_data(as_text=True)
check('name="labor_rate"' in labor and "Hourly labor rate" in labor, "the hourly labor rate lives on the Labor calculator page")
r = owner.post("/quoting/pricing/labor-rate", data=dict(CSRF, labor_rate="92.5", next=labor_url))
with app.app_context():
    check(m.get_labor_rate() == 92.5 and r.headers["Location"].endswith(labor_url), "saving it there keeps you on the Labor calculator")
check("$92.50 / hour" in owner.get("/quoting/pricing").get_data(as_text=True), "and the Pricing Sheet's tile shows the new rate")
check("tool" in owner.get("/quoting/pricing").get_data(as_text=True).split('class="sheet-tiles"')[1].split("</div>")[0].lower(), "the Tools tile is there for admins")
crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role; role.view_pricing = True; role.view_jobs = True; db.session.commit()
check("Tools</strong>" not in crew.get("/quoting/pricing").get_data(as_text=True), "people who aren't admins don't get the Tools tile (Tools is admin only)")

# job notes
with app.app_context():
    person = m.Client(first_name="Dana", last_name="Smith"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Basement wiring", job_number=1042); db.session.add(job); db.session.commit()
    job_id = job.id
url = "/jobs/%d" % job_id
check("Notes &amp; pictures" in owner.get(url).get_data(as_text=True), "a job has a Notes & pictures section")
r = owner.post(url + "/notes", data=dict(CSRF, body=""), content_type="multipart/form-data")
with app.app_context():
    check(m.JobNote.query.count() == 0, "an empty note is refused")
r = owner.post(url + "/notes", data=dict(CSRF, body="Panel is a 1980s Zinsco; customer wants it gone.",
                                         photos=[(io.BytesIO(PNG), "panel.png"), (io.BytesIO(b"hello"), "notes.txt")]), content_type="multipart/form-data")
with app.app_context():
    note = m.JobNote.query.one()
    check(note.body.startswith("Panel is a 1980s Zinsco") and [p.original_name for p in note.photos] == ["panel.png"] and note.created_by.username == "owner",
          "a note is saved with its picture and who wrote it (a file that isn't a picture is skipped)")
    stored = note.photos[0].stored_name; note_id = note.id
page = owner.get(url).get_data(as_text=True)
check("1980s Zinsco" in page and stored in page, "the job page shows the note and its picture")
check(owner.get("/jobs/note-photos/" + stored).data == PNG, "the picture is served to people signed in")
check(m.app.test_client().get("/jobs/note-photos/" + stored).status_code in (302, 401), "and not to anybody signed out")
owner.post(url + "/notes", data=dict(CSRF, photos=[(io.BytesIO(PNG), "meter.png")]), content_type="multipart/form-data")
with app.app_context():
    check(m.JobNote.query.count() == 2, "a picture alone is a note too")
check(crew.post(url + "/notes/%d/remove" % note_id, data=CSRF).status_code == 403, "someone else can't remove your note")
owner.post(url + "/notes/%d/remove" % note_id, data=CSRF)
with app.app_context():
    check(m.JobNote.query.count() == 1 and m.JobNotePhoto.query.count() == 1, "its writer can remove it, with its pictures")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    skip("Playwright is not installed (browser half)"); done()
import logging  # noqa: E402
import tempfile  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
app.config["TESTING"] = False
logging.getLogger("werkzeug").setLevel(logging.ERROR)
server = make_server("127.0.0.1", 0, app, threaded=True); threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_port
picture = os.path.join(tempfile.mkdtemp(), "breaker.png"); open(picture, "wb").write(PNG)
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/quoting/pricing")
    check(not page.locator("#cooper-dialog").is_visible(), "the Cooper window starts closed")
    tiles = page.locator(".sheet-tiles").bounding_box()
    check(tiles["height"] < 90, "the three windows sit in one small row (%dpx tall)" % tiles["height"])
    page.click("[data-open-dialog=cooper-dialog]")
    check(page.locator("#cooper-dialog").is_visible() and page.locator("#cooper-dialog", has_text="Add all items from Cooper").is_visible(), "clicking Cooper opens its window")
    page.click("#cooper-dialog [data-close-dialog]")
    check(not page.locator("#cooper-dialog").is_visible(), "and × closes it")
    page.goto(base + url)
    page.fill("#job-note-body", "New breaker installed")
    page.set_input_files("[data-note-photos]", picture)
    check(page.locator("[data-note-photo-count]").inner_text() == "1 picture ready", "picking a picture says it is ready")
    page.click(".job-note-form button.btn-primary")
    page.wait_for_load_state()
    check(page.locator(".job-note-list li", has_text="New breaker installed").locator("img").count() == 1, "the note shows with its picture")
    page.set_viewport_size({"width": 390, "height": 800})
    check(page.evaluate("document.documentElement.scrollWidth") <= 391, "the notes fit a phone screen")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
