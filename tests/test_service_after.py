"""See it after the upgrade: a photo of the existing install becomes the AI's picture of it after the service (drawn with
the service's parts), with a box over each part the AI finds; it runs in the background and the page waits for it.
Nothing calls OpenAI: the image and the part-finding are stand-ins. Browser half: the parts and boxes, Before / After."""
import base64
import io
import json
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in, skip  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
CALLS = {"images": [], "responses": []}
SPOTS = [{"part": 0, "x": 10, "y": 10, "w": 30, "h": 40, "note": "the new panel"}, {"part": 1, "x": 50, "y": 60, "w": 5, "h": 5, "note": "locknut"},
         {"part": 1, "x": 60, "y": 60, "w": 5, "h": 5, "note": "locknut"}, {"part": 9, "x": 1, "y": 1, "w": 5, "h": 5, "note": "made up"},
         {"part": 0, "x": 98, "y": 98, "w": 30, "h": 30, "note": "off the edge"}]


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.images = SimpleNamespace(edit=self.edit)
        self.responses = self

    def edit(self, **kwargs):
        CALLS["images"].append(kwargs)
        if FAIL.get("image"):
            raise RuntimeError("image service down")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode())])

    def create(self, **kwargs):
        CALLS["responses"].append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"spots": SPOTS}))


FAIL = {}
m.OpenAI = FakeOpenAI
with app.app_context():
    service = m.Service(name="200A Service Upgrade", category="Upgrades", description="New panel and meter", estimated_labor_hours=8)
    service.items.append(m.ServiceItem(name="Square D 200A panel", quantity=1, unit="ea", unit_price=300, position=0))
    service.items.append(m.ServiceItem(name="2 in. locknut", quantity=6, unit="ea", unit_price=0.5, position=1))
    db.session.add(service); db.session.commit(); service_id = service.id
url = "/quoting/services/%d" % service_id
os.environ.pop("OPENAI_API_KEY", None)
page = owner.get(url).get_data(as_text=True)
check("See it after the upgrade" in page and "connected (no OpenAI key)" in page, "the service page has See it after the upgrade (and says when the AI isn't connected)")
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
r = owner.post(url + "/after", data=dict(CSRF, photo=(io.BytesIO(b"%PDF"), "plan.pdf")), content_type="multipart/form-data")
with app.app_context():
    check(m.ServiceRender.query.count() == 0, "it needs a photo")
r = owner.post(url + "/after", data=dict(CSRF, photo=(io.BytesIO(PNG), "old-panel.png")), content_type="multipart/form-data")
with app.app_context():
    render = m.ServiceRender.query.one()
    check(r.status_code == 302 and render.status == "done" and render.image_filename.endswith("-after.png"), "the photo becomes the AI's picture of the finished job")
    prompt = CALLS["images"][-1]["prompt"]
    check("200A Service Upgrade" in prompt and "1 ea Square D 200A panel" in prompt and "6 ea 2 in. locknut" in prompt and "Do not add any text" in prompt,
          "it is drawn with the service and its parts")
    hotspots = render.hotspots
    check([(h["part"], h["x"]) for h in hotspots] == [("Square D 200A panel", 10.0), ("2 in. locknut", 50.0), ("2 in. locknut", 60.0)],
          "the parts are found in the picture; a part that isn't listed is dropped, and so is a box off the edge")
    sent = CALLS["responses"][-1]["input"][0]["content"]
    check("0: Square D 200A panel" in sent[0]["text"] and sent[1]["image_url"].startswith("data:image/png;base64,"), "the part-finder sees the finished picture and the numbered parts")
    render_id = render.id
status = owner.get("/quoting/services/renders/%d" % render_id).json
check(status["status"] == "done" and len(status["hotspots"]) == 3 and status["image"].endswith("-after.png") and status["parts"] == ["Square D 200A panel", "2 in. locknut"],
      "the page gets the picture, the boxes and the parts")
FAIL["image"] = True
owner.post(url + "/after", data=dict(CSRF, photo=(io.BytesIO(PNG), "again.png")), content_type="multipart/form-data")
with app.app_context():
    failed = m.ServiceRender.query.order_by(m.ServiceRender.id.desc()).first()
    check(failed.status == "failed" and "couldn't be made" in failed.error, "an AI failure is said plainly")
FAIL.clear()
page = owner.get(url + "?render=%d" % render_id).get_data(as_text=True)
check("Earlier pictures" not in page or "render=%d" % render_id in page, "an earlier picture can be opened again")
with app.app_context():
    stuck = m.ServiceRender(service_id=service_id, photo_filename="x.png", status="working", created_at=m.datetime.utcnow() - m.timedelta(minutes=m.RENDER_STALE_MINUTES + 1))
    db.session.add(stuck); db.session.commit(); stuck_id = stuck.id
check(owner.get("/quoting/services/renders/%d" % stuck_id).json["status"] == "failed", "a picture that never finished (the web app restarted) is ended, so another can be made")

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
    page.goto(base + url + "?render=%d" % render_id)
    view = page.locator("[data-after-view]")
    check(view.is_visible() and page.locator(".after-box").count() == 3, "the picture shows a box over each part found")
    check(page.locator(".after-parts [data-part]").count() == 2 and "2 in picture" in page.locator(".after-parts [data-part]").nth(1).inner_text(), "and lists the parts, with how many places each is")
    page.locator(".after-parts [data-part]").nth(1).click()
    check(page.locator(".after-box.is-on").count() == 2 and page.locator("[data-after-label]").inner_text() == "2 in. locknut", "clicking a part highlights it in the picture")
    page.locator(".after-box").first.click()
    check(page.locator("[data-after-label]").inner_text() == "Square D 200A panel", "clicking a box says what it is")
    page.click("[data-after-show=before]")
    check("-before-" in page.locator("[data-after-image]").get_attribute("src") and page.locator(".after-box").count() == 0, "Before shows the original photo")
    page.click("[data-after-show=after]")
    check(page.locator(".after-box").count() == 3, "and After brings the picture back")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
