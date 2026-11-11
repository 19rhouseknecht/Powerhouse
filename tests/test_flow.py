"""Power House Flow's saving: what is kept, what is cleaned away, and that a stale tab never
overwrites a newer version."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, done, make_site, sign_in  # noqa: E402

appmod = make_site()
client = sign_in(appmod)
H = {"X-CSRF-Token": "test-token"}

page = client.get("/flow")
check(page.status_code == 200 and b'id="flow-app"' in page.data, "the page opens")
check(b'"nodes": []' in page.data or b'"nodes":[]' in page.data, "a new flow starts empty")

good = {"nodes": [{"id": "a", "title": "Customer calls", "kind": "start", "who": "Office", "auto": "manual", "notes": "x"},
                  {"id": "b", "title": "Quote", "kind": "step", "who": "", "auto": "software", "notes": ""},
                  {"id": "c", "title": "Decide", "kind": "decision", "who": "Client", "auto": "", "notes": ""}],
        "edges": [{"from": "a", "to": "b", "label": ""}, {"from": "b", "to": "c", "label": ""},
                  {"from": "c", "to": "b", "label": "Needs changes"}]}
r = client.post("/flow/save", json={"version": 0, "data": good}, headers=H)
check(r.status_code == 200 and r.json["ok"] and r.json["version"] == 1, "saves and moves to version 1")
check(client.post("/flow/save", json={"version": 1, "data": good}).status_code == 400, "a save without the CSRF token is refused")

r = client.post("/flow/save", json={"version": 0, "data": {"nodes": []}}, headers=H)
check(r.status_code == 409 and r.json.get("conflict"), "a stale tab (old version) is refused, nothing overwritten")
with appmod.app.app_context():
    chart = appmod.flow_chart()
    kept = json.loads(chart.data_json)
    check(len(kept["nodes"]) == 3 and len(kept["edges"]) == 3, "the newer version is intact")
    check(chart.updated_by.username == "owner", "it remembers who saved")

messy = {"nodes": [{"id": "a", "title": "  ", "kind": "rocket", "auto": "maybe", "notes": "n" * 9000},
                   {"id": "a", "title": "duplicate id"}, {"id": "bad id!", "title": "bad"}, "not a step",
                   {"id": "b", "title": "<script>alert(1)</script>", "kind": "end"}],
         "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "b"}, {"from": "a", "to": "a"},
                   {"from": "a", "to": "missing"}, {"from": "b", "to": "a", "label": "L" * 300}]}
r = client.post("/flow/save", json={"version": 1, "data": messy}, headers=H)
check(r.status_code == 200, "a messy save is accepted and cleaned")
with appmod.app.app_context():
    kept = json.loads(appmod.flow_chart().data_json)
    a = kept["nodes"][0]
    check([n["id"] for n in kept["nodes"]] == ["a", "b"], "bad and duplicate steps are dropped")
    check(a["title"] == "Untitled step" and a["kind"] == "step" and a["auto"] == "", "blank title, unknown type and mark get safe defaults")
    check(len(a["notes"]) == 4000, "notes are capped")
    check(len(kept["edges"]) == 2 and len(kept["edges"][1]["label"]) == 80, "duplicate, self and dangling arrows are dropped; labels capped")

page = client.get("/flow").get_data(as_text=True)
check("<script>alert(1)</script>" not in page, "step text is never written into the page as HTML")
done()
