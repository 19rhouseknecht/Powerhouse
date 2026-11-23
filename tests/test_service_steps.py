"""Services with step-by-step instructions: a switch on the service form opens a step builder (title, instructions, a
picture per step; add, remove, move); the service page shows the steps in order; everything else a service has stays.
Browser half: the builder in a real browser, a phone-sized photo made smaller before it is sent."""
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
base_form = dict(CSRF, name="200A Service Upgrade", category="Service Upgrades", example_quantity="1 service", estimated_labor_hours="8",
                 description="Panel and meter")
base_form["steps_sent"] = "1"
form = dict(base_form, has_steps="on")
form.update({"step_key[]": ["n1", "n2", "n3"], "step_id[]": ["", "", ""], "step_title[]": ["Pull the permit", "Set the meter base", ""],
             "step_body[]": ["Township permit before anything.", "Level, 5 ft to the center.", ""],
             "step_image_n2": (io.BytesIO(PNG), "meter.png"), "item_name[]": ["Meter base"], "item_quantity[]": ["1"], "item_unit[]": ["ea"], "item_price[]": ["120"]})
r = owner.post("/quoting/services/new", data=form, content_type="multipart/form-data")
with app.app_context():
    service = m.Service.query.one()
    steps = [(s.title, s.body, bool(s.image_filename)) for s in service.steps]
    check(r.status_code == 302 and service.has_steps and steps == [("Pull the permit", "Township permit before anything.", False), ("Set the meter base", "Level, 5 ft to the center.", True)],
          "a service is saved with its steps in order, a picture on step 2, and the empty row left out")
    check([i.name for i in service.items] == ["Meter base"] if hasattr(m.ServiceItem, "name") else len(service.items) == 1, "and everything else a service has (materials)")
    service_id = service.id; ids = [str(s.id) for s in service.steps]; picture = service.steps[1].image_filename
page = owner.get("/quoting/services/%d" % service_id).get_data(as_text=True)
check("Step-by-step instructions" in page and page.index("Pull the permit") < page.index("Set the meter base") and picture in page, "the service page shows the steps in order, with the picture")
edit = owner.get("/quoting/services/%d/edit" % service_id).get_data(as_text=True)
check('name="has_steps" value="on" data-steps-switch checked' in edit and "Set the meter base" in edit and picture in edit, "editing brings back the switch, the steps and the picture")

form2 = dict(base_form, has_steps="on")
form2.update({"step_key[]": ["e%s" % ids[1], "e%s" % ids[0], "n9"], "step_id[]": [ids[1], ids[0], ""],
              "step_title[]": ["Set the meter base", "Pull the permit", "Call for inspection"], "step_body[]": ["", "Township permit before anything.", ""]})
owner.post("/quoting/services/%d/edit" % service_id, data=form2, content_type="multipart/form-data")
with app.app_context():
    service = db.session.get(m.Service, service_id)
    check([s.title for s in service.steps] == ["Set the meter base", "Pull the permit", "Call for inspection"] and service.steps[0].image_filename == picture,
          "moving a step and adding one keeps each step's picture")
form3 = dict(form2); form3["step_image_remove_e%s" % ids[1]] = "on"
form3.update({"step_key[]": form2["step_key[]"][:2], "step_id[]": form2["step_id[]"][:2], "step_title[]": form2["step_title[]"][:2], "step_body[]": form2["step_body[]"][:2]})
owner.post("/quoting/services/%d/edit" % service_id, data=form3, content_type="multipart/form-data")
with app.app_context():
    service = db.session.get(m.Service, service_id)
    check([s.title for s in service.steps] == ["Set the meter base", "Pull the permit"] and service.steps[0].image_filename is None,
          "a step can be removed, and a step's picture taken off")
    check(os.path.exists(os.path.join(app.static_folder, "uploads", "services", picture)), "the picture file itself is never deleted")
off = dict(form3); off.pop("has_steps"); off.pop("step_image_remove_e%s" % ids[1])
owner.post("/quoting/services/%d/edit" % service_id, data=off, content_type="multipart/form-data")
page = owner.get("/quoting/services/%d" % service_id).get_data(as_text=True)
with app.app_context():
    service = db.session.get(m.Service, service_id)
    check(not service.has_steps and len(service.steps) == 2 and "Step-by-step instructions" not in page, "switching it off hides the steps but keeps them, for switching back on")
empty = dict(base_form, has_steps="on"); empty.update({"step_key[]": ["n1"], "step_id[]": [""], "step_title[]": [""], "step_body[]": [""]})
r = owner.post("/quoting/services/%d/edit" % service_id, data=empty, content_type="multipart/form-data")
check(r.status_code == 400 and "Add at least one step" in r.get_data(as_text=True), "switched on with no steps says to add one")
bad = dict(base_form, has_steps="on"); bad.update({"step_key[]": ["n1"], "step_id[]": [""], "step_title[]": ["x"], "step_body[]": [""], "step_image_n1": (io.BytesIO(b"%PDF"), "plan.pdf")})
r = owner.post("/quoting/services/%d/edit" % service_id, data=bad, content_type="multipart/form-data")
check(r.status_code == 400 and "must be a PNG" in r.get_data(as_text=True), "a step picture must be a picture")
older = {k: v for k, v in base_form.items() if k != "steps_sent"}
owner.post("/quoting/services/%d/edit" % service_id, data=older, content_type="multipart/form-data")
with app.app_context():
    check(len(db.session.get(m.Service, service_id).steps) == 2, "a form without the step builder leaves the steps alone")
plain = owner.get("/quoting/services/new").get_data(as_text=True)
check('data-steps-switch' in plain and 'data-steps-switch checked' not in plain, "a new service starts as pictures only, with the switch off")

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
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    big = page.evaluate("""() => new Promise(done => { const c = document.createElement('canvas'); c.width = 2400; c.height = 1800; const g = c.getContext('2d');
        const d = g.createImageData(2400, 1800); for (let i = 0; i < d.data.length; i += 4) { d.data[i] = (i * 7) % 255; d.data[i + 1] = Math.random() * 255; d.data[i + 2] = (i >> 9) % 255; d.data[i + 3] = 255; }
        g.putImageData(d, 0, 0);
        c.toBlob(b => { const r = new FileReader(); r.onload = () => done(r.result.split(',')[1]); r.readAsDataURL(b); }, 'image/png'); })""")
    photo = os.path.join(tempfile.mkdtemp(), "panel-photo.png"); open(photo, "wb").write(base64.b64decode(big))
    page.goto(base + "/quoting/services/new")
    check(page.locator("[data-steps-builder]").is_hidden(), "the step builder is hidden until the switch is on")
    page.fill("input[name=name]", "Whole-home surge protector")
    page.check("[data-steps-switch]")
    check(page.locator("[data-steps-builder]").is_visible(), "switching it on opens the builder")
    rows = page.locator("[data-step-row]")
    rows.nth(0).locator("input[name='step_title[]']").fill("Shut off the main")
    page.click("[data-step-add]")
    rows.nth(1).locator("input[name='step_title[]']").fill("Land the surge protector")
    rows.nth(1).locator("[data-step-file]").set_input_files(photo)
    crop = page.locator(".crop-dialog")
    crop.locator(".crop-stage img").wait_for(timeout=8000)
    page.wait_for_function("document.querySelector('.crop-dialog .crop-stage img').naturalWidth > 0")
    check(crop.is_visible(), "choosing a picture opens the crop window")
    crop.locator(".crop-foot [data-crop-skip]").click()
    rows.nth(1).locator("[data-step-file-note]", has_text="ready").wait_for(timeout=8000)
    sent = rows.nth(1).locator("[data-step-file]").evaluate("f => [f.files[0].size, f.files[0].type]")
    check(sent[1] == "image/jpeg" and sent[0] < os.path.getsize(photo) / 2, "kept as it is, a big photo is still made smaller before it is sent (%d KB from %d KB)" % (sent[0] // 1024, os.path.getsize(photo) // 1024))
    rows.nth(1).locator("[data-step-crop]").click()
    page.wait_for_function("document.querySelector('.crop-dialog').open && document.querySelector('.crop-dialog .crop-stage img').naturalWidth > 0")
    crop.locator(".crop-shape", has_text="1:1").click()
    handle = crop.locator("[data-crop-box] i[data-h='se']").bounding_box()
    page.mouse.move(handle["x"] + 9, handle["y"] + 9); page.mouse.down(); page.mouse.move(handle["x"] - 120, handle["y"] - 60, steps=6); page.mouse.up()
    crop.locator("[data-crop-apply]").click()
    rows.nth(1).locator("[data-step-file-note]", has_text="Cropped").wait_for(timeout=8000)
    size = rows.nth(1).locator("[data-step-preview]").evaluate("i => new Promise(done => { if (i.complete && i.naturalWidth) done([i.naturalWidth, i.naturalHeight]); else i.onload = () => done([i.naturalWidth, i.naturalHeight]); })")
    check(abs(size[0] - size[1]) <= 2 and size[0] < 1800, "Crop with the square shape, after dragging a corner, sends a square picture (%dx%d)" % tuple(size))
    page.click("[data-step-add]")
    rows.nth(2).locator("input[name='step_title[]']").fill("Test it")
    rows.nth(2).locator("[data-step-up]").click()
    check([rows.nth(i).locator("[data-step-number]").inner_text() for i in range(3)] == ["1", "2", "3"] and
          rows.nth(1).locator("input[name='step_title[]']").input_value() == "Test it", "moving a step renumbers them")
    rows.nth(1).locator("[data-step-remove]").click()
    check(rows.count() == 2, "Remove takes a step out")
    page.click("button:has-text('Create service')")
    page.wait_for_load_state()
    check("Step-by-step instructions" in page.content() and page.locator(".step-slide").count() == 2 and page.locator(".step-slide img").count() == 1,
          "the service page has both steps, with the picture")
    slides = page.locator("[data-step-slide]")
    check(slides.nth(0).is_visible() and slides.nth(1).is_hidden() and page.locator("[data-step-counter]").inner_text() == "Step 1 of 2",
          "it shows one step at a time")
    check(page.locator("[data-step-prev]").is_disabled() and page.locator(".step-progress-seg.is-current").count() == 1, "with arrows and a progress bar")
    page.click("[data-step-next]")
    check(slides.nth(1).is_visible() and slides.nth(0).is_hidden() and page.locator("[data-step-next]").is_disabled(), "the arrow goes to the next step")
    pic = slides.nth(1).locator("img").bounding_box(); words = slides.nth(1).locator(".step-slide-text").bounding_box()
    check(pic["x"] < words["x"] and pic["width"] > words["width"], "the picture is the main view, the words beside it on the right")
    page.click(".step-progress-seg >> nth=0")
    check(slides.nth(0).is_visible(), "a piece of the progress bar jumps to its step")
    page.focus("[data-step-carousel]"); page.keyboard.press("ArrowRight")
    check(slides.nth(1).is_visible(), "the arrow keys move between steps")
    box = page.locator("[data-step-carousel]").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + 60); page.mouse.down(); page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + 64, steps=5); page.mouse.up()
    check(slides.nth(0).is_visible(), "and a swipe does too")
    page.set_viewport_size({"width": 390, "height": 800})
    check(page.evaluate("document.documentElement.scrollWidth") <= 391, "the steps fit a phone")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
