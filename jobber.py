"""Jobber: read clients, requests, quotes and jobs out of a Jobber account (Settings -> Jobber).

Read only. Nothing here ever writes to Jobber. No database or Flask in this file either (app.py
does the matching and saving), so it can be tested on its own.

    authorize_url / exchange / refresh   Jobber's OAuth: an app made in Jobber's Developer Center
                                         (Client ID + Secret), the owner signs in once, tokens refresh
                                         themselves (an access token lasts 60 minutes; with rotation on,
                                         every refresh returns a new refresh token, saved at once)
    Session.gql                          one GraphQL call, refreshing the token and waiting out
                                         Jobber's rate limit when it says THROTTLED
    Session.selection                    builds a query from WANT, keeping only the fields Jobber's own
                                         schema says exist on this API version (asked through GraphQL
                                         introspection), so a renamed or missing field never breaks a pull
    pull                                 everything, page by page, as plain dicts
    client_fields / job_fields /         turn Jobber's records into Power House's fields
    quote_fields / request_fields
"""
import base64
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

API = "https://api.getjobber.com/api/graphql"
AUTHORIZE = "https://api.getjobber.com/api/oauth/authorize"
TOKEN = "https://api.getjobber.com/api/oauth/token"
VERSION = "2025-04-16"           # X-JOBBER-GRAPHQL-VERSION; Jobber supports a version for 12-18 months
AGENT = "Power House"
TRANSPORT = None                 # tests: callable(method, url, headers, body_bytes) -> (status, bytes)
SLEEP = time.sleep               # tests replace it
PAGE = {"clients": 25, "requests": 25, "quotes": 15, "jobs": 10}


class JobberError(Exception):
    def __init__(self, msg, kind=""):
        super().__init__(msg)
        self.kind = kind             # auth · throttled · refused · network · data


# --------------------------------------------------------------------------- HTTP
def _http(method, url, headers, body=None, timeout=60):
    if TRANSPORT is not None:
        return TRANSPORT(method, url, headers, body)
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        raise JobberError("Could not reach Jobber (%s). Try again in a minute." % exc.__class__.__name__, "network")


def _json(payload):
    try:
        return json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}


# --------------------------------------------------------------------------- OAuth
def authorize_url(client_id, redirect_uri, state):
    return AUTHORIZE + "?" + urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state})


def _token_expiry(data):
    if data.get("expires_in"):
        return time.time() + float(data["expires_in"])
    try:                                                    # Jobber's access token is a JWT: read its exp
        part = data.get("access_token", "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if claims.get("exp"):
            return float(claims["exp"])
    except (IndexError, ValueError, TypeError):
        pass
    return time.time() + 55 * 60


def _token_call(form):
    status, payload = _http("POST", TOKEN, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                                            "User-Agent": AGENT}, urlencode(form).encode())
    data = _json(payload)
    if status >= 400 or not data.get("access_token"):
        words = data.get("error_description") or data.get("error") or data.get("message") or ""
        raise JobberError("Jobber did not accept the sign-in (%s%s). Connect again on Settings -> Jobber." %
                          (status, (': "%s"' % words) if words else ""), "auth")
    return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token") or form.get("refresh_token", ""),
            "expires_at": _token_expiry(data)}


def exchange(client_id, client_secret, code, redirect_uri):
    return _token_call({"grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret,
                        "code": code, "redirect_uri": redirect_uri})


def refresh(client_id, client_secret, refresh_token):
    return _token_call({"grant_type": "refresh_token", "client_id": client_id, "client_secret": client_secret,
                        "refresh_token": refresh_token})


# --------------------------------------------------------------------------- GraphQL
class Session:
    """Calls Jobber with a token that stays fresh. `save(tokens)` is called after every refresh,
    because with rotation on the old refresh token stops working the moment a new one is issued."""

    def __init__(self, client_id, client_secret, tokens, save=None):
        self.client_id, self.client_secret = client_id, client_secret
        self.tokens = dict(tokens or {})
        self.save = save or (lambda tokens: None)
        self.types = {}
        self.calls = 0

    def _fresh(self, force=False):
        if not self.tokens.get("refresh_token") and not self.tokens.get("access_token"):
            raise JobberError("Jobber is not connected yet.", "auth")
        if force or time.time() > float(self.tokens.get("expires_at") or 0) - 300:
            if not self.tokens.get("refresh_token"):
                raise JobberError("Jobber's sign-in ran out. Connect again on Settings -> Jobber.", "auth")
            self.tokens = refresh(self.client_id, self.client_secret, self.tokens["refresh_token"])
            self.save(self.tokens)
        return self.tokens["access_token"]

    def gql(self, query, variables=None):
        refreshed = False
        for attempt in range(8):
            headers = {"Authorization": "Bearer " + self._fresh(), "Content-Type": "application/json", "Accept": "application/json",
                       "X-JOBBER-GRAPHQL-VERSION": VERSION, "User-Agent": AGENT}
            status, payload = _http("POST", API, headers, json.dumps({"query": query, "variables": variables or {}}).encode())
            self.calls += 1
            data = _json(payload)
            if status == 401 and not refreshed:
                self._fresh(force=True)
                refreshed = True
                continue
            errors = data.get("errors") or []
            if status == 429 or any(((e.get("extensions") or {}).get("code") == "THROTTLED") for e in errors):
                SLEEP(self._wait(data, attempt))
                continue
            if status >= 400 and not errors:
                raise JobberError("Jobber answered %d." % status, "refused")
            if errors and not data.get("data"):
                words = "; ".join(str(e.get("message") or "") for e in errors)[:300]
                raise JobberError('Jobber refused the request: "%s".' % words, "refused")
            return data.get("data") or {}
        raise JobberError("Jobber kept saying it was busy. Try again in a few minutes.", "throttled")

    @staticmethod
    def _wait(data, attempt):
        """Seconds to wait when throttled: Jobber's own cost numbers when it gives them, else back off."""
        try:
            cost = data["extensions"]["cost"]
            throttle = cost["throttleStatus"]
            need = float(cost.get("requestedQueryCost") or 1000) - float(throttle["currentlyAvailable"])
            return max(1.0, min(60.0, need / max(float(throttle["restoreRate"]), 1.0) + 1))
        except (KeyError, TypeError, ValueError):
            return min(60.0, 2.0 * (2 ** attempt))

    # ---- what this API version has
    def fields_of(self, type_name):
        """{field name: (kind, type name)} for one GraphQL type, asked once and remembered."""
        if type_name not in self.types:
            data = self.gql("query($n: String!) { __type(name: $n) { name kind fields { name type { kind name ofType { kind name "
                            "ofType { kind name ofType { kind name } } } } } } }", {"n": type_name})
            found = {}
            for field in ((data.get("__type") or {}).get("fields") or []):
                kind, name = _unwrap(field.get("type") or {})
                found[field["name"]] = (kind, name)
            self.types[type_name] = found
        return self.types[type_name]

    def selection(self, type_name, want, missing=None, path=""):
        """GraphQL selection text for `want` on `type_name`, leaving out what this API version does not have."""
        have = self.fields_of(type_name)
        parts = []
        for key, sub in want.items():
            name = key.split("(", 1)[0].strip()
            where = (path + "." + name) if path else name
            if name not in have:
                if missing is not None:
                    missing.append(where)
                continue
            kind, inner = have[name]
            if sub is None:
                if kind in ("SCALAR", "ENUM"):
                    parts.append(key)
                elif missing is not None:
                    missing.append(where)
                continue
            if kind not in ("OBJECT", "INTERFACE"):
                if missing is not None:
                    missing.append(where)
                continue
            inside = self.selection(inner, sub, missing, where)
            if inside:
                parts.append("%s { %s }" % (key, inside))
        return " ".join(parts)


def _unwrap(type_ref):
    while type_ref.get("kind") in ("NON_NULL", "LIST") and type_ref.get("ofType"):
        type_ref = type_ref["ofType"]
    return type_ref.get("kind"), type_ref.get("name")


# --------------------------------------------------------------------------- what to ask for
ADDRESS = {"street1": None, "street2": None, "city": None, "province": None, "postalCode": None, "country": None}
LINE_ITEMS = {"nodes": {"name": None, "description": None, "quantity": None, "unitPrice": None, "totalPrice": None, "unitCost": None}}
NAME = {"full": None}
AMOUNTS = {"subtotal": None, "total": None, "discountAmount": None, "taxAmount": None, "depositAmount": None}

WANT = {
    "clients": {
        "id": None, "name": None, "firstName": None, "lastName": None, "companyName": None, "isCompany": None, "title": None,
        "emails": {"address": None, "description": None, "primary": None},
        "phones": {"number": None, "description": None, "primary": None, "smsAllowed": None},
        "billingAddress": ADDRESS,
        "clientProperties(first: 20)": {"nodes": {"id": None, "address": ADDRESS}},
        "tags(first: 20)": {"nodes": {"label": None}},
        "sourceAttribution": {"sourceText": None, "source": None},
        "leadSource": None,
        "isLead": None, "isArchived": None, "balance": None, "createdAt": None, "updatedAt": None, "jobberWebUri": None,
    },
    "requests": {
        "id": None, "title": None, "requestStatus": None, "createdAt": None, "updatedAt": None, "jobberWebUri": None,
        "client": {"id": None}, "property": {"id": None, "address": ADDRESS},
        "assessment": {"startAt": None, "endAt": None, "instructions": None},
        "contactName": None, "companyName": None, "email": None, "phone": None,
    },
    "quotes": {
        "id": None, "quoteNumber": None, "title": None, "quoteStatus": None, "message": None, "createdAt": None, "updatedAt": None,
        "sentAt": None, "jobberWebUri": None, "amounts": AMOUNTS,
        "client": {"id": None}, "property": {"id": None, "address": ADDRESS}, "request": {"id": None},
        "salesperson": {"name": NAME},
        "lineItems(first: 40)": LINE_ITEMS,
    },
    "jobs": {
        "id": None, "jobNumber": None, "title": None, "instructions": None, "jobStatus": None, "jobType": None,
        "startAt": None, "endAt": None, "completedAt": None, "createdAt": None, "updatedAt": None, "total": None,
        "jobberWebUri": None,
        "client": {"id": None}, "property": {"id": None, "address": ADDRESS},
        "quote": {"id": None, "quoteNumber": None}, "request": {"id": None},
        "salesperson": {"name": NAME},
        "lineItems(first: 40)": LINE_ITEMS,
        "visits(first: 20)": {"nodes": {"title": None, "startAt": None, "endAt": None, "completedAt": None, "visitStatus": None,
                                        "assignedUsers(first: 10)": {"nodes": {"name": NAME}}}},
        "invoices(first: 10)": {"nodes": {"invoiceNumber": None, "invoiceStatus": None, "subject": None, "issuedDate": None,
                                          "dueDate": None, "total": None, "amounts": AMOUNTS}},
    },
}
ORDER = ("clients", "requests", "quotes", "jobs")


def pull(session, progress=None):
    """Everything WANT asks for, page by page. Returns {account, clients, requests, quotes, jobs, missing, calls}."""
    say = progress or (lambda text: None)
    root = session.fields_of("Query")
    out = {"missing": [], "account": {}}
    try:
        out["account"] = (session.gql("{ account { id name } }") or {}).get("account") or {}
    except JobberError as exc:
        if exc.kind != "refused":
            raise
    for what in ORDER:
        out[what] = []
        if what not in root:
            out["missing"].append(what)
            say("This Jobber account's API has no %s; skipped." % what)
            continue
        connection = root[what][1]
        node_type = session.fields_of(connection).get("nodes", (None, None))[1]
        if not node_type:
            out["missing"].append(what)
            continue
        fields = session.selection(node_type, WANT[what], out["missing"], what)
        query = "query($after: String) { %s(first: %d, after: $after) { nodes { %s } pageInfo { hasNextPage endCursor } } }" % (
            what, PAGE[what], fields)
        after = None
        while True:
            data = session.gql(query, {"after": after})
            block = data.get(what) or {}
            out[what].extend(block.get("nodes") or [])
            say("Read %d %s…" % (len(out[what]), what))
            info = block.get("pageInfo") or {}
            if not info.get("hasNextPage") or not info.get("endCursor"):
                break
            after = info["endCursor"]
    out["calls"] = session.calls
    return out


# --------------------------------------------------------------------------- Jobber -> Power House
def _nodes(value):
    if isinstance(value, dict):
        return [n for n in (value.get("nodes") or []) if isinstance(n, dict)]
    return [n for n in (value or []) if isinstance(n, dict)]


def _address(addr):
    addr = addr or {}
    return {"address_line1": (addr.get("street1") or "").strip()[:180], "address_line2": (addr.get("street2") or "").strip()[:180],
            "city": (addr.get("city") or "").strip()[:100], "state": (addr.get("province") or "").strip()[:40],
            "postal_code": (addr.get("postalCode") or "").strip()[:20]}


def address_text(addr):
    a = _address(addr)
    return " ".join(p for p in [a["address_line1"], a["address_line2"], ", ".join(filter(None, [a["city"], a["state"]])), a["postal_code"]] if p)


def parse_time(text):
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def local_date(text):
    """A Jobber time as Pennsylvania's calendar day."""
    when = parse_time(text)
    if when is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        return when.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    except Exception:  # noqa: BLE001
        return when.date()


def money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def client_fields(raw):
    emails = _nodes(raw.get("emails"))
    phones = _nodes(raw.get("phones"))
    first_email = next((e for e in emails if e.get("primary")), emails[0] if emails else {})
    first_phone = next((p for p in phones if p.get("primary")), phones[0] if phones else {})
    others = ["%s%s: %s" % ("Email", (" (%s)" % e["description"]) if e.get("description") else "", e.get("address"))
              for e in emails if e is not first_email and e.get("address")]
    others += ["%s%s: %s" % ("Phone", (" (%s)" % p["description"]) if p.get("description") else "", p.get("number"))
               for p in phones if p is not first_phone and p.get("number")]
    first, last = (raw.get("firstName") or "").strip(), (raw.get("lastName") or "").strip()
    company = (raw.get("companyName") or "").strip()
    if not first and not last:
        first = company or (raw.get("name") or "").strip() or "Jobber client"
    properties = [dict(_address(p.get("address")), jobber_id=p.get("id") or "") for p in _nodes(raw.get("clientProperties"))]
    billing = _address(raw.get("billingAddress"))
    if not billing["address_line1"] and properties:
        billing = {k: properties[0][k] for k in billing}
    source = raw.get("sourceAttribution") or {}
    return dict(billing, jobber_id=raw.get("id") or "", first_name=first[:100], last_name=last[:100], company_name=company[:180],
                email=(first_email.get("address") or "").strip()[:180], phone=(first_phone.get("number") or "").strip()[:50],
                other_contacts="\n".join(others), properties=properties,
                tags=", ".join(t.get("label", "") for t in _nodes(raw.get("tags")) if t.get("label"))[:255],
                lead_source=(source.get("sourceText") or source.get("source") or raw.get("leadSource") or "")[:120],
                jobber_url=raw.get("jobberWebUri") or "", created_at=parse_time(raw.get("createdAt")),
                is_archived=bool(raw.get("isArchived")))


def _lines(raw):
    return [{"name": i.get("name") or "", "description": i.get("description") or "", "quantity": i.get("quantity"),
             "unit_price": money(i.get("unitPrice")), "total": money(i.get("totalPrice"))} for i in _nodes(raw.get("lineItems"))]


def job_status(raw, now=None):
    """Jobber's job status as Power House's."""
    now = now or datetime.utcnow()
    status = (raw.get("jobStatus") or "").lower()
    if raw.get("completedAt") or status in ("archived", "requires_invoicing", "completed"):
        return "completed"
    if status in ("unscheduled", "on_hold"):
        return "approved"
    start = parse_time(raw.get("startAt"))
    if start is None:
        return "approved"
    if status in ("late", "today", "action_required", "active") or start <= now:
        return "active"
    return "scheduled"


QUOTE_STATUS = {"draft": "working_quote", "awaiting_response": "awaiting_decision", "changes_requested": "awaiting_decision",
                "approved": "approved", "converted": "approved", "archived": "declined"}


def _duration(start, end):
    s, e = local_date(start), local_date(end)
    return max(1, min(365, (e - s).days + 1)) if s and e and e >= s else 1


def job_fields(raw, now=None):
    prop = raw.get("property") or {}
    sales = ((raw.get("salesperson") or {}).get("name") or {}).get("full") or ""
    return dict(_address(prop.get("address")), jobber_id=raw.get("id") or "", jobber_number=str(raw.get("jobNumber") or ""),
                title=(raw.get("title") or "Jobber job").strip()[:180], description=(raw.get("instructions") or "").strip(),
                status=job_status(raw, now), work_start_date=local_date(raw.get("startAt")),
                duration_days=_duration(raw.get("startAt"), raw.get("endAt")), contract_amount=money(raw.get("total")),
                client_jobber_id=(raw.get("client") or {}).get("id") or "", quote_jobber_id=(raw.get("quote") or {}).get("id") or "",
                request_jobber_id=(raw.get("request") or {}).get("id") or "", jobber_url=raw.get("jobberWebUri") or "",
                created_at=parse_time(raw.get("createdAt")), salesperson=sales, job_type=(raw.get("jobType") or "").lower(),
                line_items=_lines(raw),
                visits=[{"title": v.get("title") or "", "start": v.get("startAt"), "end": v.get("endAt"), "completed": v.get("completedAt"),
                         "status": v.get("visitStatus") or "",
                         "crew": ", ".join(((u.get("name") or {}).get("full") or "") for u in _nodes(v.get("assignedUsers")))}
                        for v in _nodes(raw.get("visits"))],
                invoices=[{"number": str(i.get("invoiceNumber") or ""), "status": i.get("invoiceStatus") or "", "subject": i.get("subject") or "",
                           "issued": i.get("issuedDate") or "", "due": i.get("dueDate") or "",
                           "total": money(i.get("total") if i.get("total") is not None else (i.get("amounts") or {}).get("total"))}
                          for i in _nodes(raw.get("invoices"))])


def quote_fields(raw):
    prop = raw.get("property") or {}
    status = (raw.get("quoteStatus") or "").lower()
    sales = ((raw.get("salesperson") or {}).get("name") or {}).get("full") or ""
    return dict(_address(prop.get("address")), jobber_id=raw.get("id") or "", number=str(raw.get("quoteNumber") or ""),
                title=(raw.get("title") or ("Jobber quote #%s" % raw.get("quoteNumber"))).strip()[:180],
                description=(raw.get("message") or "").strip(), quote_status=status, status=QUOTE_STATUS.get(status, "awaiting_decision"),
                total=money((raw.get("amounts") or {}).get("total")), client_jobber_id=(raw.get("client") or {}).get("id") or "",
                request_jobber_id=(raw.get("request") or {}).get("id") or "", jobber_url=raw.get("jobberWebUri") or "",
                created_at=parse_time(raw.get("createdAt")), salesperson=sales, line_items=_lines(raw))


def request_fields(raw):
    prop = raw.get("property") or {}
    assessment = raw.get("assessment") or {}
    status = (raw.get("requestStatus") or "").lower()
    return dict(_address(prop.get("address")), jobber_id=raw.get("id") or "", title=(raw.get("title") or "Jobber request").strip()[:180],
                description=(assessment.get("instructions") or "").strip(), request_status=status,
                skip=status in ("converted", "archived"),
                status="review_scheduled" if assessment.get("startAt") else "new_request",
                review_at=parse_time(assessment.get("startAt")), client_jobber_id=(raw.get("client") or {}).get("id") or "",
                jobber_url=raw.get("jobberWebUri") or "", created_at=parse_time(raw.get("createdAt")))

