"""Modern (the default) and Dark (the classic look): each person picks theirs from the menu on their name, it is kept on
their account, Dark is style.css exactly as before, and modern-colors.css is regenerated whenever style.css changes.
Browser half: the switch in a real browser, kept after a reload, and both looks readable."""
import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, ROOT, check, done, make_site, sign_in, skip  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_modern_css  # noqa: E402

generated = open(os.path.join(ROOT, "static", "css", "modern-colors.css"), encoding="utf-8").read()
check(build_modern_css.build() == generated, "modern-colors.css is up to date with style.css (run: python tools/build_modern_css.py)")
scope = ':where(html[data-theme="modern"])'
rules = [line.strip() for line in generated.splitlines()[1:] if line.strip() and not line.strip().startswith(("@", "}"))]
check(rules and all(all(part.strip().startswith(scope) for part in build_modern_css.split_top(rule.split("{")[0], ",")) for rule in rules),
      "every generated rule applies only to Modern (%d rules)" % len(rules))
modern = re.sub(r"/\*.*?\*/", "", open(os.path.join(ROOT, "static", "css", "modern.css"), encoding="utf-8").read(), flags=re.S)
selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", modern) for s in build_modern_css.split_top(block, ",") if s.strip()]
check(selectors and all(s.startswith('html[data-theme="modern"]') for s in selectors), "every hand-written Modern rule applies only to Modern")
check("data-theme" not in open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8").read(), "style.css (Dark) knows nothing of Modern")

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
login = m.app.test_client().get("/login").get_data(as_text=True)
check('data-theme="modern"' in login and "Log in" in login, "the sign-in page is Modern")
crew = sign_in(m, "crew", admin=False)
page = owner.get("/").get_data(as_text=True)
check('data-theme="modern"' in page and "modern-colors.css" in page and "modern.css" in page and "family=Inter" in page and "Outfit" in page,
      "Modern is the default, with its fonts")
check("data-user-menu-button" in page and 'value="dark"' in page and "Log out" in page, "your name opens a menu with Modern / Dark and Log out")
r = owner.post("/account/theme", data=dict(CSRF, theme="dark", next="/jobs"))
check(r.status_code == 302 and r.headers["Location"].endswith("/jobs"), "picking a look without scripts comes back to the same page")
check('data-theme="dark"' in owner.get("/").get_data(as_text=True), "Dark is kept on your account")
check('data-theme="modern"' in crew.get("/").get_data(as_text=True), "and only yours: everyone else keeps their own")
r = owner.post("/account/theme", data=dict(CSRF, theme="modern"), headers={"X-Requested-With": "fetch"})
check(r.json == {"ok": True, "theme": "modern"} and 'data-theme="modern"' in owner.get("/").get_data(as_text=True), "switching back to Modern works")
owner.post("/account/theme", data=dict(CSRF, theme="neon"))
check('data-theme="modern"' in owner.get("/").get_data(as_text=True), "anything else is Modern")
check(m.app.test_client().post("/account/theme", data=dict(CSRF, theme="dark")).status_code in (302, 401), "signed out, nothing changes")

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


def luminance(css):
    r, g, b = [int(x) / 255 for x in re.findall(r"[\d.]+", css)[:3]]
    lin = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in (r, g, b)]
    return .2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + .05) / (lb + .05)


with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
    except Exception as exc:  # noqa: BLE001
        skip("no browser (%s)" % str(exc).splitlines()[0][:100]); done()
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/jobs")
    body = page.evaluate("[getComputedStyle(document.body).backgroundColor, getComputedStyle(document.body).color]")
    check(luminance(body[0]) > .8 and contrast(*body) > 7, "Modern is light, with dark, readable text (%.1f:1)" % contrast(*body))
    check("Outfit" in page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily"), "Modern headings use Outfit")
    panel = page.locator("[data-user-menu-panel]")
    check(panel.is_hidden(), "the menu starts closed")
    page.click("[data-user-menu-button]")
    check(panel.is_visible() and page.get_attribute("[data-user-menu-button]", "aria-expanded") == "true", "clicking your name opens it")
    page.keyboard.press("Escape")
    check(panel.is_hidden(), "Escape closes it")
    page.click("[data-user-menu-button]"); page.click(".theme-choice-dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    page.wait_for_timeout(400)
    dark = page.evaluate("getComputedStyle(document.body).backgroundColor")
    check(dark == "rgb(12, 10, 9)" and page.url.endswith("/jobs"), "Dark switches at once, without leaving the page: the classic background")
    page.reload()
    check(page.evaluate("document.documentElement.dataset.theme") == "dark", "and is still Dark after a reload")
    check(page.locator(".theme-choice-dark").get_attribute("aria-checked") == "true", "the menu shows which look is on")
    page.click("[data-user-menu-button]"); page.click(".theme-choice-modern")
    page.wait_for_function("document.documentElement.dataset.theme === 'modern'")
    page.click("body", position={"x": 700, "y": 600})
    check(panel.is_hidden(), "clicking elsewhere closes the menu")
    page.set_viewport_size({"width": 390, "height": 800})
    check(page.evaluate("document.documentElement.scrollWidth") <= 391, "it fits a phone")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
