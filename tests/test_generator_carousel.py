"""Generators -> Sizing as four steps, one at a time (Home, Appliances, HVAC, Results): Back / Continue keep what was typed,
done steps can be revisited, Results is the same NEC 220.82 answer as before (generators.size), and saving to a job still works.
Browser half: the slides in a real browser, on a desk and a phone."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in, skip  # noqa: E402

m = make_site()
app, db = m.app, m.db
owner = sign_in(m)
with app.app_context():
    person = m.Client(first_name="Ann", last_name="Lee"); db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Standby generator", job_number=700); db.session.add(job); db.session.commit()
    job_id = job.id


def visible(page, n):
    part = page.split('data-slide="%d"' % n)[1].split(">")[0]
    return "hidden" not in part


page = owner.get("/generators/sizing").get_data(as_text=True)
check(visible(page, 1) and not visible(page, 2) and not visible(page, 3) and 'data-slide="4"' not in page, "it opens on Home only")
check(all(name in page for name in ("Home</span>", "Appliances</span>", "HVAC</span>", "Results</span>")), "the steps are named at the top")
page = owner.get("/generators/sizing?step=2").get_data(as_text=True)
check(visible(page, 2) and not visible(page, 1) and not visible(page, 3), "without scripts Continue still works: the server opens the asked-for step")
for key in m.gen.APPLIANCES:
    check('name="%s"' % key in page and 'name="va_%s"' % key in page and 'name="managed_%s"' % key in page, "the Appliances step has %s (count, VA, Managed)" % key)
for name in ("ac_count", "ac_tons", "ac_amps", "ac_lra", "heat_pump_count", "heat_strip_kw", "heat_units", "soft_start", "heat_interlock", "managed_hvac", "other_va",
             "fuel", "service_amps", "square_feet", "small_appliance_circuits", "laundry_circuits", "job_id"):
    check('name="%s"' % name in page, "the page keeps the field %s" % name)
query = {"calculate": "1", "square_feet": "2600", "range": "1", "managed_dryer": "on", "ac_tons": "4", "fuel": "lp"}
expected = m.gen.size(query)
page = owner.get("/generators/sizing", query_string=query).get_data(as_text=True)
check(visible(page, 4) and not visible(page, 1), "sizing opens on Results")
check("Kohler %s" % expected["model"]["model"] in page and "%g kW" % expected["required_kw"] in page and "{:,} VA".format(expected["total_va"]) in page,
      "Results is the same answer as the calculation (%s, %g kW)" % (expected["model"]["model"], expected["required_kw"]))
check("Largest motor start" in page and "%g kVA" % expected["starting_kw"] in page and "Propane" in page and "200 A" in page and "Electric dryer" in page,
      "with the motor start, fuel, service and managed loads")
check("NEC 220.82 optional calculation" in page and "14RCA" in page and "20RCA" in page and "26RCA" in page, "and the NEC 220.82 explanation and Kohler's models")
est = owner.get("/generators/sizing/estimate", query_string=query).json
check(est["total_va"] == expected["total_va"] and est["required_kw"] == expected["required_kw"] and est["model"] == expected["model"]["model"],
      "the load so far is the server's own calculation")
check(page.count("<form") == 2 and 'id="gz-save-form"' in page and 'form="gz-save-form"' in page, "the save form is not inside the sizing form")

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
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/login"); page.fill("input[name=username]", "owner"); page.fill("input[name=password]", "a-long-password-1"); page.click("form button")
    page.goto(base + "/generators/sizing")
    shown = lambda: [n for n in (1, 2, 3, 4) if page.locator('[data-slide="%d"]' % n).count() and page.locator('[data-slide="%d"]' % n).is_visible()]  # noqa: E731
    check(shown() == [1], "one step at a time: Home")
    check(page.locator('[data-go="2"]').is_disabled() and page.locator('[data-go="3"]').is_disabled(), "steps not reached yet can't be jumped to")
    page.fill('input[name="square_feet"]', "2600")
    page.locator('input[name="square_feet"]').press("Enter")
    check(shown() == [2] and page.url.endswith("/generators/sizing"), "Enter in a box goes to the next step (it doesn't submit)")
    check(page.locator('[data-go="2"]').get_attribute("aria-current") == "step", "the step names show where you are")
    page.fill('input[name="range"]', "1")
    page.check('input[name="managed_dryer"]')
    page.locator('[data-slide="2"] button', has_text="Back").click()
    check(shown() == [1] and page.input_value('input[name="square_feet"]') == "2600", "Back keeps what was typed")
    page.click('[data-go="2"]')
    check(shown() == [2] and page.input_value('input[name="range"]') == "1" and page.is_checked('input[name="managed_dryer"]'), "a step already done can be revisited")
    page.locator('[data-slide="2"] button', has_text="Continue to HVAC").click()
    check(shown() == [3], "Continue to HVAC")
    page.locator('[data-sofar-load]').filter(has_text="VA").wait_for(timeout=5000)
    sofar = m.gen.size({"square_feet": "2600", "range": "1", "managed_dryer": "on"})
    page.wait_for_function("document.querySelector('[data-sofar-load]').textContent.replace(/,/g, '').includes('%d')" % sofar["total_va"], timeout=5000)
    check(True, "the load so far follows what's typed (the server's own calculation, %s VA)" % format(sofar["total_va"], ","))
    page.fill('input[name="ac_tons"]', "4")
    page.click(".gz-go")
    page.wait_for_load_state()
    expected = m.gen.size({"square_feet": "2600", "range": "1", "managed_dryer": "on", "ac_tons": "4"})
    check(shown() == [4] and "%g kW" % expected["required_kw"] in page.locator('[data-slide="4"]').inner_text(), "Size the generator shows Results only, with the calculated answer")
    page.click('[data-go="2"]')
    check(shown() == [2] and page.input_value('input[name="range"]') == "1", "from Results every step can be opened again, with its values")
    page.click('[data-go="4"]'); page.wait_for_load_state()
    check(shown() == [4], "and Results again")
    page.select_option('select[form="gz-save-form"]', str(job_id))
    page.locator("[data-save] button").click()
    page.wait_for_load_state()
    with app.app_context():
        saved = m.GeneratorSizing.query.one()
        check("/jobs/%d" % job_id in page.url and saved.required_kw == expected["required_kw"] and saved.inputs.get("managed_dryer") == "on"
              and saved.inputs.get("range") in (1, 1.0, "1"), "Save sizing saves the same answer to the job")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + "/generators/sizing?step=2")
    check(page.evaluate("document.documentElement.scrollWidth") <= 391 and shown() == [2], "on a phone it fits, one step at a time")
    check(not errors, "no script errors" + ("" if not errors else ": " + "; ".join(errors)))
    browser.close()
done()
