"""Pull from Jobber against a pretend Jobber: connecting (OAuth), the token refreshing itself, Jobber's rate
limit, fields an API version does not have, the preview, the import, and pulling again without duplicates."""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

appmod = make_site()
import jobber as jb  # noqa: E402

app, db = appmod.app, appmod.db
NOW = datetime.utcnow()
iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731


# ---------------------------------------------------------------- the pretend Jobber's schema
def S(name="String"):
    return ("SCALAR", name)


def O(name):
    return ("OBJECT", name)


def L(name):
    return ("LIST", name)


SCHEMA = {
    "Query": {"account": O("Account"), "clients": O("ClientConnection"), "requests": O("RequestConnection"),
              "quotes": O("QuoteConnection"), "jobs": O("JobConnection")},
    "Account": {"id": S("EncodedId"), "name": S()},
    "PageInfo": {"hasNextPage": S("Boolean"), "endCursor": S()},
    "Address": {k: S() for k in ("street1", "street2", "city", "province", "postalCode", "country")},
    "Email": {"address": S(), "description": S(), "primary": S("Boolean")},
    "ClientPhoneNumber": {"number": S(), "description": S(), "primary": S("Boolean"), "smsAllowed": S("Boolean")},
    "Property": {"id": S(), "address": O("Address")},
    "Tag": {"label": S()},
    "Name": {"full": S()},
    "User": {"name": O("Name")},
    "LineItem": {k: S() for k in ("name", "description", "quantity", "unitPrice", "totalPrice", "unitCost")},
    "Amounts": {k: S() for k in ("subtotal", "total", "discountAmount", "taxAmount", "depositAmount")},
    "Assessment": {"startAt": S(), "endAt": S(), "instructions": S()},
    "Visit": {"title": S(), "startAt": S(), "endAt": S(), "completedAt": S(), "visitStatus": ("ENUM", "VisitStatus"),
              "assignedUsers": O("UserConnection")},
    "Invoice": {"invoiceNumber": S(), "invoiceStatus": ("ENUM", "InvoiceStatus"), "subject": S(), "issuedDate": S(), "dueDate": S(),
                "total": S(), "amounts": O("Amounts")},
    "Client": {"id": S(), "name": S(), "firstName": S(), "lastName": S(), "companyName": S(), "isCompany": S("Boolean"), "title": S(),
               "emails": L("Email"), "phones": L("ClientPhoneNumber"), "billingAddress": O("Address"),
               "clientProperties": O("PropertyConnection"), "tags": O("TagConnection"), "isLead": S("Boolean"),
               "isArchived": S("Boolean"), "balance": S("Float"), "createdAt": S(), "updatedAt": S(), "jobberWebUri": S()},
    "Request": {"id": S(), "title": S(), "requestStatus": ("ENUM", "RequestStatus"), "createdAt": S(), "updatedAt": S(),
                "jobberWebUri": S(), "client": O("Client"), "property": O("Property"), "assessment": O("Assessment")},
    "Quote": {"id": S(), "quoteNumber": S(), "title": S(), "quoteStatus": ("ENUM", "QuoteStatus"), "message": S(), "createdAt": S(),
              "updatedAt": S(), "sentAt": S(), "jobberWebUri": S(), "amounts": O("Amounts"), "client": O("Client"),
              "property": O("Property"), "request": O("Request"), "salesperson": O("User"), "lineItems": O("LineItemConnection")},
    # this API version has no jobberWebUri on Job: the pull must leave it out and say so
    "Job": {"id": S(), "jobNumber": S("Int"), "title": S(), "instructions": S(), "jobStatus": ("ENUM", "JobStatus"),
            "jobType": ("ENUM", "JobType"), "startAt": S(), "endAt": S(), "completedAt": S(), "createdAt": S(), "updatedAt": S(),
            "total": S("Float"), "client": O("Client"), "property": O("Property"), "quote": O("Quote"), "request": O("Request"),
            "salesperson": O("User"), "lineItems": O("LineItemConnection"), "visits": O("VisitConnection"),
            "invoices": O("InvoiceConnection")},
}
for name, node in (("ClientConnection", "Client"), ("RequestConnection", "Request"), ("QuoteConnection", "Quote"),
                   ("JobConnection", "Job"), ("PropertyConnection", "Property"), ("TagConnection", "Tag"),
                   ("LineItemConnection", "LineItem"), ("VisitConnection", "Visit"), ("InvoiceConnection", "Invoice"),
                   ("UserConnection", "User")):
    SCHEMA[name] = {"nodes": L(node), "pageInfo": O("PageInfo"), "totalCount": S("Int")}


def type_ref(kind_name):
    kind, name = kind_name
    if kind == "LIST":
        return {"kind": "NON_NULL", "name": None, "ofType": {"kind": "LIST", "name": None, "ofType":
                {"kind": "NON_NULL", "name": None, "ofType": {"kind": "OBJECT", "name": name, "ofType": None}}}}
    return {"kind": kind, "name": name, "ofType": None}


# ---------------------------------------------------------------- the pretend Jobber's data
def addr(street, city="Lancaster", zip_code="17601"):
    return {"street1": street, "street2": "", "city": city, "province": "PA", "postalCode": zip_code, "country": "US"}


ITEMS = {"nodes": [{"name": "200A panel upgrade", "description": "Square D", "quantity": 1, "unitPrice": 4000, "totalPrice": 4000},
                   {"name": "Permit", "description": "", "quantity": 1, "unitPrice": 500, "totalPrice": 500}]}
DATA = {
    "clients": [
        {"id": "C1", "firstName": "Ann", "lastName": "Lee", "companyName": "", "emails": [{"address": "ANN@example.com", "primary": True}],
         "phones": [{"number": "717-555-1111", "primary": True}], "billingAddress": addr("12 Main St"),
         "clientProperties": {"nodes": [{"id": "P1", "address": addr("12 Main St")}, {"id": "P2", "address": addr("40 Rental Rd", "Lititz", "17543")}]},
         "tags": {"nodes": []}, "jobberWebUri": "https://secure.getjobber.com/clients/1", "createdAt": "2025-03-01T12:00:00Z"},
        {"id": "C2", "firstName": "", "lastName": "", "companyName": "Acme Rentals", "name": "Acme Rentals",
         "emails": [{"address": "office@acme.test", "primary": True}, {"address": "billing@acme.test", "description": "Billing"}],
         "phones": [{"number": "717-555-2222", "primary": True}], "billingAddress": addr("1 Market St"),
         "clientProperties": {"nodes": []}, "tags": {"nodes": [{"label": "Commercial"}, {"label": "Landlord"}]},
         "jobberWebUri": "https://secure.getjobber.com/clients/2"},
        {"id": "C3", "firstName": "Bob", "lastName": "Stone", "emails": [], "phones": [{"number": "717-555-3333"}],
         "billingAddress": addr("9 Oak Ave"), "clientProperties": {"nodes": []}, "tags": {"nodes": []}},
    ],
    "requests": [
        {"id": "R1", "title": "Outlet not working", "requestStatus": "new", "client": {"id": "C3"}, "property": {"id": "", "address": addr("9 Oak Ave")}},
        {"id": "R2", "title": "Panel quote", "requestStatus": "converted", "client": {"id": "C1"}},
        {"id": "R3", "title": "Old lead", "requestStatus": "archived", "client": {"id": "C3"}},
        {"id": "R4", "title": "Warehouse lighting", "requestStatus": "assessment_scheduled", "client": {"id": "C2"},
         "assessment": {"startAt": iso(NOW + timedelta(days=3)), "instructions": "Meet the manager at the loading dock"}},
    ],
    "quotes": [
        {"id": "Q1", "quoteNumber": 31, "title": "Generator hookup", "quoteStatus": "awaiting_response", "client": {"id": "C3"},
         "amounts": {"total": 1200}, "lineItems": ITEMS, "jobberWebUri": "https://secure.getjobber.com/quotes/31"},
        {"id": "Q2", "quoteNumber": 30, "title": "Panel upgrade", "quoteStatus": "converted", "client": {"id": "C1"},
         "request": {"id": "R2"}, "amounts": {"total": 4500}, "lineItems": ITEMS},
        {"id": "Q3", "quoteNumber": 29, "title": "Sign lighting", "quoteStatus": "archived", "client": {"id": "C2"}, "amounts": {"total": 800}},
    ],
    "jobs": [
        {"id": "J1", "jobNumber": 101, "title": "Panel upgrade", "instructions": "Shut off main first", "jobStatus": "upcoming", "jobType": "ONE_OFF",
         "startAt": iso(NOW + timedelta(days=2)), "endAt": iso(NOW + timedelta(days=3)), "total": 4500, "client": {"id": "C1"},
         "property": {"id": "P1", "address": addr("12 Main St")}, "quote": {"id": "Q2", "quoteNumber": 30}, "request": {"id": "R2"},
         "salesperson": {"name": {"full": "Reese H"}}, "lineItems": ITEMS,
         "visits": {"nodes": [{"title": "Install", "startAt": iso(NOW + timedelta(days=2)), "assignedUsers": {"nodes": [{"name": {"full": "Caden"}}]}}]},
         "invoices": {"nodes": [{"invoiceNumber": 55, "invoiceStatus": "draft", "total": 4500}]}},
        {"id": "J2", "jobNumber": 99, "title": "Hot tub circuit", "jobStatus": "requires_invoicing", "completedAt": iso(NOW - timedelta(days=5)),
         "startAt": iso(NOW - timedelta(days=6)), "total": 950, "client": {"id": "C2"}, "property": {"id": "", "address": addr("1 Market St")}},
        {"id": "J3", "jobNumber": 100, "title": "Fix flicker", "jobStatus": "unscheduled", "total": 300, "client": {"id": "C3"}},
    ],
}

CALLS = {"token": [], "graphql": 0, "throttled": 0, "queries": []}
STATE = {"access": "access-1", "refresh": "refresh-1", "throttle_once": True, "expire_next": False}


def page_of(items, after, size):
    start = int(after or 0)
    chunk = items[start:start + size]
    more = start + size < len(items)
    return {"nodes": chunk, "pageInfo": {"hasNextPage": more, "endCursor": str(start + size) if more else None}}


def transport(method, url, headers, body):
    if url == jb.TOKEN:
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        CALLS["token"].append(form)
        if form["grant_type"] == "authorization_code":
            if form["code"] != "good-code" or form["client_secret"] != "the-secret":
                return 400, b'{"error": "invalid_grant"}'
        elif form["refresh_token"] != STATE["refresh"]:
            return 400, b'{"error": "invalid_grant"}'
        n = len(CALLS["token"]) + 1
        STATE["access"], STATE["refresh"] = "access-%d" % n, "refresh-%d" % n       # rotation: a new refresh token every time
        return 200, json.dumps({"access_token": STATE["access"], "refresh_token": STATE["refresh"], "expires_in": 3600}).encode()
    assert url == jb.API and headers.get("X-JOBBER-GRAPHQL-VERSION"), "GraphQL calls carry the version header"
    if STATE["expire_next"]:
        STATE["expire_next"] = False
        STATE["access"] = "expired"
    if headers.get("Authorization") != "Bearer " + STATE["access"]:
        return 401, b'{"message": "Unauthorized"}'
    CALLS["graphql"] += 1
    payload = json.loads(body.decode())
    query, variables = payload["query"], payload.get("variables") or {}
    CALLS["queries"].append(query)
    if "__type" in query:
        fields = SCHEMA.get(variables["n"])
        if fields is None:
            return 200, b'{"data": {"__type": null}}'
        return 200, json.dumps({"data": {"__type": {"name": variables["n"], "kind": "OBJECT",
                                                     "fields": [{"name": f, "type": type_ref(t)} for f, t in fields.items()]}}}).encode()
    if query.strip().startswith("{ account"):
        return 200, b'{"data": {"account": {"id": "A1", "name": "Power House Electrical"}}}'
    for what in jb.ORDER:
        if query.split("{", 2)[1].strip().startswith(what + "("):
            if what == "jobs" and STATE["throttle_once"]:
                STATE["throttle_once"] = False
                CALLS["throttled"] += 1
                return 200, json.dumps({"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                                        "extensions": {"cost": {"requestedQueryCost": 500, "throttleStatus": {"currentlyAvailable": 100, "restoreRate": 500}}}}).encode()
            return 200, json.dumps({"data": {what: page_of(DATA[what], variables.get("after"), jb.PAGE[what])}}).encode()
    return 200, b'{"errors": [{"message": "unknown query"}]}'


jb.TRANSPORT = transport
WAITS = []
jb.SLEEP = WAITS.append
jb.PAGE.update(clients=2, requests=3, quotes=2, jobs=2)          # small pages, so paging is exercised


def wait_pull(timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        with app.app_context():
            db.session.remove()
            state = appmod.jobber_pull_state()
        if state.get("status") != "running":
            return state
        time.sleep(0.1)
    raise SystemExit("the pull did not finish")


# ---------------------------------------------------------------- a client already here
with app.app_context():
    ann = appmod.Client(first_name="Ann", last_name="Lee", email="ann@example.com", phone="555-0000", state="PA")
    db.session.add(ann)
    db.session.commit()
    ann_id = ann.id

client = sign_in(appmod)
crew = sign_in(appmod, "crew", admin=False)

page = client.get("/settings").get_data(as_text=True)
check("Pull from Jobber" in page, "Settings has the Pull from Jobber button")
check(crew.get("/settings/jobber").status_code == 403, "Jobber settings are admins only")
page = client.get("/settings/jobber").get_data(as_text=True)
check("Not connected" in page and "/settings/jobber/callback" in page, "before connecting, the page explains the Jobber app and shows the callback address")
r = client.post("/settings/jobber/pull", data=CSRF, follow_redirects=True)
check(b"Connect Jobber first" in r.data, "pulling before connecting says to connect first")

# ---------------------------------------------------------------- connect (OAuth)
r = client.post("/settings/jobber/keys", data=dict(CSRF, client_id="client-123", client_secret="the-secret"))
check(r.status_code == 302 and r.headers["Location"].endswith("/settings/jobber/connect"), "saving the keys goes on to connect")
r = client.get("/settings/jobber/connect")
target = urlparse(r.headers["Location"])
params = {k: v[0] for k, v in parse_qs(target.query).items()}
check(r.headers["Location"].startswith(jb.AUTHORIZE) and params["client_id"] == "client-123" and params["state"], "Connect sends you to Jobber's sign-in")
check(params["redirect_uri"].startswith("https://") and params["redirect_uri"].endswith("/settings/jobber/callback"), "with the https callback address")
r = client.get("/settings/jobber/callback?code=good-code&state=wrong", follow_redirects=True)
check(b"stale" in r.data, "a callback with the wrong state is refused")
client.get("/settings/jobber/connect")
with client.session_transaction() as sess:
    state_value = sess["jobber_state"]
r = client.get("/settings/jobber/callback?code=good-code&state=" + state_value, follow_redirects=True)
check(b"Connected to Jobber (Power House Electrical)" in r.data, "Jobber's answer connects the account")
with app.app_context():
    stored = db.session.get(appmod.CompanySetting, "jobber_tokens").value
    check("refresh-" not in stored and "the-secret" not in db.session.get(appmod.CompanySetting, "jobber_client_secret").value,
          "the secret and Jobber's tokens are stored encrypted")

# ---------------------------------------------------------------- pull
STATE["expire_next"] = True                       # Jobber turns the token away once: it must refresh and carry on
r = client.post("/settings/jobber/pull", data=CSRF)
state = wait_pull()
check(state.get("status") == "done", "the pull finishes (%s)" % (state.get("error") or state.get("status")))
check(state.get("counts") == {"clients": 3, "requests": 4, "quotes": 3, "jobs": 3}, "it reads every page of everything: %s" % state.get("counts"))
check(any(f["grant_type"] == "refresh_token" for f in CALLS["token"]), "an expired token is refreshed by itself")
with app.app_context():
    tokens = json.loads(appmod.jobber_secret("tokens"))
    check(tokens["refresh_token"] == STATE["refresh"], "the rotated refresh token is saved at once")
check(CALLS["throttled"] == 1 and WAITS and WAITS[0] >= 1, "when Jobber says THROTTLED it waits and tries again (%s s)" % WAITS[:1])
job_query = next(q for q in CALLS["queries"] if q.split("{", 2)[1].strip().startswith("jobs("))
check("jobberWebUri" not in job_query and "lineItems(first: 40)" in job_query, "fields this API version lacks are left out of the query")
check("sourceAttribution" not in next(q for q in CALLS["queries"] if "clients(" in q), "an unknown client field is left out too")

page = client.get("/settings/jobber").get_data(as_text=True)
check("What an import will do" in page and "Nothing has been saved yet" in page, "the pull shows a preview")
check("jobs.jobberWebUri" in page, "the preview lists what this API version did not have")
with app.app_context():
    plan = appmod.jobber_plan(appmod.jobber_pulled())
    c = plan["counts"]
    check((c["clients_new"], c["clients_link"]) == (2, 1), "preview: 2 new clients, 1 linked to a client already here: %s" % c)
    check(c["jobs_new"] == 7 and (c["from_request"], c["from_quote"], c["from_job"]) == (2, 2, 3),
          "preview: 7 jobs (2 open requests, 2 quotes not yet jobs, 3 jobs); converted and archived requests left out")
    check(appmod.Job.query.count() == 0, "nothing is saved by the pull")

# ---------------------------------------------------------------- import
r = client.post("/settings/jobber/import", data=CSRF, follow_redirects=True)
check(b"Imported from Jobber: 2 new clients, 7 new jobs" in r.data, "Import saves it")
check(any(n.endswith("before-jobber-import.db") for n in os.listdir(os.path.join("instance", "backups"))), "the database was backed up first")
with app.app_context():
    Client, Job = appmod.Client, appmod.Job
    ann = db.session.get(Client, ann_id)
    check(ann.jobber_id == "C1" and Client.query.count() == 3, "Ann, already here, was linked rather than duplicated")
    check(ann.phone == "555-0000" and ann.address_line1 == "12 Main St", "a linked client keeps her phone and gets her blank address filled")
    check([p.text for p in ann.properties] == ["40 Rental Rd Lititz, PA 17543"], "her second service address came over (not the billing one twice)")
    acme = Client.query.filter_by(jobber_id="C2").one()
    check(acme.full_name == "Acme Rentals" and acme.company_name == "Acme Rentals", "a company with no person's name is named by the company")
    check(acme.tag_list == ["Commercial", "Landlord"] and "billing@acme.test" in acme.other_contacts, "tags and other emails came over")
    by = {j.title: j for j in Job.query.all()}
    buckets = {t: j.bucket for t, j in by.items()}
    check(buckets == {"Outlet not working": "in_works", "Warehouse lighting": "in_works", "Generator hookup": "quoted",
                      "Sign lighting": "completed", "Panel upgrade": "active", "Hot tub circuit": "completed", "Fix flicker": "active"},
          "each lands in the right Jobs tab: %s" % buckets)
    panel = by["Panel upgrade"]
    check(panel.status == "scheduled" and panel.duration_days == 2 and panel.work_start_date is not None, "a scheduled Jobber job keeps its dates")
    check(panel.contract_amount == 4500 and panel.jobber_number == "101" and panel.jobber_quote_number == "30", "and its total, job number and quote")
    check(panel.description == "Shut off main first" and panel.source == "jobber", "its instructions become the description")
    check(len(panel.jobber["job"]["line_items"]) == 2 and panel.jobber["job"]["visits"][0]["crew"] == "Caden", "line items and visits are kept")
    check(by["Warehouse lighting"].status == "review_scheduled" and by["Warehouse lighting"].review_at is not None, "a scheduled assessment becomes the site review")
    check(by["Warehouse lighting"].address_line1 == "1 Market St", "a request with no address uses the client's")
    check(by["Generator hookup"].jobber_quote_total == 1200, "a quote's total is kept")
    numbers = sorted(j.job_number for j in Job.query.all())
    check(numbers == list(range(700, 707)), "imported jobs get Power House job numbers")
    panel_id, outlet_id, gen_id, bob_id = panel.id, by["Outlet not working"].id, by["Generator hookup"].id, by["Generator hookup"].client_id

page = client.get("/jobs/%d" % panel_id).get_data(as_text=True)
check("Jobber job #101" in page and "200A panel upgrade" in page and "Invoice #55" in page, "the job page shows what came from Jobber")
page = client.get("/clients/%d" % ann_id).get_data(as_text=True)
check("40 Rental Rd" in page and "Open in Jobber" in page, "the client page shows the other address and the Jobber link")
page = client.get("/clients?q=Acme").get_data(as_text=True)
check("Acme Rentals" in page, "clients can be found by company name")

# ---------------------------------------------------------------- pull again: no duplicates, edits here are kept
with app.app_context():
    outlet = db.session.get(appmod.Job, outlet_id)
    outlet.title = "Kitchen outlet (renamed here)"
    db.session.commit()
DATA["quotes"][0]["quoteStatus"] = "converted"
DATA["jobs"].append({"id": "J4", "jobNumber": 102, "title": "Generator hookup", "jobStatus": "upcoming", "startAt": iso(NOW + timedelta(days=9)),
                     "total": 1250, "client": {"id": "C3"}, "quote": {"id": "Q1", "quoteNumber": 31}})
DATA["clients"][2]["phones"] = [{"number": "717-555-9999", "primary": True}]
DATA["requests"][0]["title"] = "Outlet not working (Jobber)"
client.post("/settings/jobber/pull", data=CSRF)
check(wait_pull().get("status") == "done", "pulling again works")
client.post("/settings/jobber/import", data=CSRF)
with app.app_context():
    Client, Job = appmod.Client, appmod.Job
    check(Client.query.count() == 3 and Job.query.count() == 7, "pulling again makes no duplicates (%d clients, %d jobs)" % (Client.query.count(), Job.query.count()))
    gen = db.session.get(Job, gen_id)
    check(gen.jobber_number == "102" and gen.bucket == "active" and gen.contract_amount == 1250, "a quote that became a job in Jobber moves to Active here")
    check(db.session.get(Client, bob_id).phone == "717-555-9999", "a client not edited here is refreshed from Jobber")
    check(db.session.get(Job, outlet_id).title == "Kitchen outlet (renamed here)", "a job edited here keeps the edit")

# ---------------------------------------------------------------- the client form's new fields
r = client.post("/clients/new", data=dict(CSRF, company_name="Lancaster Lofts", tags="Commercial, ,Repeat ", lead_source="Referral",
                                          other_contacts="Phone (Office): 717-555-0001"))
with app.app_context():
    lofts = appmod.Client.query.filter_by(company_name="Lancaster Lofts").first()
    check(lofts is not None and lofts.tag_list == ["Commercial", "Repeat"] and lofts.lead_source == "Referral", "a client can be just a company, with tags and a lead source")
r = client.post("/clients/new", data=dict(CSRF, first_name="Only"), follow_redirects=True)
check(b"or a company name" in r.data, "a client needs a full name or a company")

client.post("/settings/jobber/disconnect", data=CSRF)
with app.app_context():
    check(not appmod.jobber_connected() and appmod.Client.query.count() == 4, "disconnecting keeps everything imported")
done()
