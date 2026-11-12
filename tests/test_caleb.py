"""Ask Caleb: the help agent under Power House Flow. Every signed-in person can open it; Caleb looks records up only through
read-only tools gated by the asker's role (prices and clients stay hidden from a role that cannot see them), the history is
per person, "New conversation" keeps the old lines (cleared, never deleted), and failures answer in words. Nothing calls OpenAI."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db = m.app, m.db
SCRIPT, SEEN = [], []


class FakeOpenAI:
    """Each call answers with the next scripted reply: a list of tool calls, a text answer, or an exception."""
    def __init__(self, *args, **kwargs):
        self.responses = self

    def create(self, **kwargs):
        SEEN.append(json.loads(json.dumps(kwargs, default=lambda o: o.__dict__)))
        reply = SCRIPT.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, list):
            calls = [SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id="call_%d" % i)
                     for i, (name, args) in enumerate(reply)]
            return SimpleNamespace(output=calls, output_text="")
        return SimpleNamespace(output=[SimpleNamespace(type="message")], output_text=reply)


m.OpenAI = FakeOpenAI

with app.app_context():
    person = m.Client(first_name="Dana", last_name="Smith", phone="717-555-0101", email="dana@example.com", city="Lititz")
    db.session.add(person); db.session.flush()
    job = m.Job(client=person, title="Generator hookup", job_number=812, description="Whole-home standby generator.",
                address_line1="9 Oak Ln", city="Lititz", state="PA", postal_code="17543", internal_notes="Gas meter on the left side.")
    db.session.add(job); db.session.flush()
    quote = m.Quote(job=job, status="confirmed", title="Generator options", option_count=3)
    db.session.add(quote); db.session.flush()
    db.session.add(m.QuoteOption(quote=quote, tier="Gold", title="Standby generator", bullets_json='["Premium installation of a 20 kW standby generator"]',
                                 position=1, manual_total_override=14250))
    db.session.add(m.PriceItem(name="200A automatic transfer switch", lookup_key="200a ats", unit="ea", price=899, cooper_part_number="RXT200"))
    db.session.commit()
    job_id = job.id
    caleb = m.caleb_agent()
    check(caleb is not None and caleb.active and "2020" in caleb.prompt, "Caleb is made at boot, with the 2020 NEC in his prompt")

# 1. the page: in the sidebar right under Power House Flow, open to every signed-in person
owner = sign_in(m)
page = owner.get("/caleb").get_data(as_text=True)
check("Ask Caleb</a>" in page and page.index("Power House Flow</a>") < page.index("Ask Caleb</a>") < page.index('class="side-section">Quoting'),
      "Ask Caleb sits in the sidebar right below Power House Flow")
check("Not connected" in page and "disabled" in page.split('id="caleb-send"')[1][:40], "without an OpenAI key the page says Not connected and Ask is off")
check("What does Premium mean on our quotes?" in page, "the empty page offers example questions")

crew = sign_in(m, "crew", admin=False)
with app.app_context():
    role = m.User.query.filter_by(username="crew").first().role
    role.view_pricing = False; role.view_clients = False; role.view_quoting = False
    db.session.commit()
crew_page = crew.get("/caleb")
check(crew_page.status_code == 200 and "Ask Caleb</a>" in crew_page.get_data(as_text=True), "a crew member (not an admin) opens Ask Caleb too")
check("Power House Flow" not in crew_page.get_data(as_text=True), "Power House Flow stays admins only")

# 2. not connected: the question is kept and the answer says to call Caleb
owner.post("/caleb/ask", data=dict(CSRF, question="Is anyone there?"))
with app.app_context():
    rows = m.CalebMessage.query.order_by(m.CalebMessage.id).all()
    check([r.role for r in rows] == ["user", "assistant"] and "call Caleb" in rows[1].content, "not connected: the question is saved and the answer says to call Caleb")

os.environ["OPENAI_API_KEY"] = "test-key-not-used"

# 3. a job question: Caleb finds the job, reads it, answers; the answer links Job #812 and lists what he looked at
SCRIPT[:] = [[("find_jobs", {"query": "Smith", "group": "any"})], [("job_details", {"job_id": job_id})],
             "Job #812 (Generator hookup for Dana Smith) has a confirmed quote; the Gold option is $14,250."]
SEEN.clear()
page = owner.post("/caleb/ask", data=dict(CSRF, question="Where are we on the Smith job?"), follow_redirects=True).get_data(as_text=True)
check('href="/jobs/%d">Job #812</a>' % job_id in page, "the answer links Job #812 to its page")
check("Searched jobs: Smith" in page and "Read a job" in page, "the answer shows what Caleb looked at")
outputs = [item for item in SEEN[1]["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]
found = json.loads(outputs[0]["output"])
check(found["jobs"][0]["job_number"] == 812 and found["jobs"][0]["client"] == "Dana Smith", "find_jobs finds the job by the client's last name")
details = json.loads([item for item in SEEN[2]["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"][-1]["output"])
check(details["quotes"][0]["options"][0]["total"] == 14250 and "Gas meter" in details["internal_notes"], "job_details carries the quote's options, totals and notes")
names = {tool.get("name") for tool in SEEN[0]["tools"]}
check({"find_clients", "find_jobs", "job_details", "find_services", "find_price_items", "size_generator"} <= names, "an admin's Caleb has every look-up")
check(any(tool.get("type") == "web_search" for tool in SEEN[0]["tools"]), "and web search, for current code and Kohler documents")
check("Premium installation of" in SEEN[0]["instructions"] and "Platinum" in SEEN[0]["instructions"], "Caleb knows what Premium and the tiers mean here")
check("14RCA" in SEEN[0]["instructions"], "and the Kohler models on the Sizing page")
check(SEEN[0]["input"][-1] == {"role": "user", "content": "Where are we on the Smith job?"}, "the question is the last thing he reads")

# 4. a crew member's Caleb cannot see clients or prices
SCRIPT[:] = [[("find_clients", {"query": "Smith"}), ("job_details", {"job_id": job_id})], "I can't see clients for your role."]
SEEN.clear()
crew.post("/caleb/ask", data=dict(CSRF, question="What's Dana Smith's phone number?"))
names = {tool.get("name") for tool in SEEN[0]["tools"]}
check("find_clients" not in names and "find_price_items" not in names and "find_jobs" in names, "a role without clients or pricing gets no look-up for them")
outs = [json.loads(item["output"]) for item in SEEN[1]["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]
check("error" in outs[0], "a look-up the role does not allow is refused even when asked for")
check(outs[1]["quotes"][0]["options"][0]["total"] == "hidden for this role" and outs[1]["client_phone"] == "", "job_details hides totals and the phone from that role")

# 5. generator sizing uses the Sizing page's math
SCRIPT[:] = [[("size_generator", {"square_feet": 2600, "ac_count": 1, "ac_tons": 4, "fuel": "ng", "ev_kw": None})], "A 26RCA covers it."]
SEEN.clear()
owner.post("/caleb/ask", data=dict(CSRF, question="Generator for 2600 sq ft with a 4 ton AC?"))
sized = json.loads([item for item in SEEN[1]["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"][0]["output"])
expected = m.gen.size({"square_feet": 2600, "ac_count": 1, "ac_tons": 4, "fuel": "ng"})
check(sized["needs_kw"] == expected["required_kw"] and sized["page"] == "/generators/sizing", "size_generator gives the same answer as the Sizing page")

# 6. history is per person, and a failure is answered in words
with app.app_context():
    crew_id = m.User.query.filter_by(username="crew").first().id
    check(all(row.user_id == crew_id for row in m.CalebMessage.query.filter_by(user_id=crew_id)), "each person keeps their own conversation")
SCRIPT[:] = [RuntimeError("boom")]
page = owner.post("/caleb/ask", data=dict(CSRF, question="Anything"), follow_redirects=True).get_data(as_text=True)
check("I couldn&#39;t answer that just now" in page or "I couldn't answer that just now" in page, "an OpenAI failure answers in words, nothing breaks")
SCRIPT[:] = [Exception("Unsupported tool type: web_search"), "Answer without the web."]
SEEN.clear()
owner.post("/caleb/ask", data=dict(CSRF, question="Code question"))
check(len(SEEN) == 2 and not any(t.get("type") == "web_search" for t in SEEN[1]["tools"]), "an account without web search is asked again without it")

# 7. answers are escaped; "New conversation" clears the screen and keeps the rows
SCRIPT[:] = ["<script>alert(1)</script> Job #999 does not exist"]
page = owner.post("/caleb/ask", data=dict(CSRF, question="x"), follow_redirects=True).get_data(as_text=True)
check("<script>alert(1)</script>" not in page and "&lt;script&gt;" in page, "an answer is escaped, never drawn as HTML")
check("Job #999 does not" in page and 'Job #999</a>' not in page, "a job number that does not exist is not linked")
with app.app_context():
    before = m.CalebMessage.query.count()
owner.post("/caleb/new", data=CSRF)
page = owner.get("/caleb").get_data(as_text=True)
with app.app_context():
    check(m.CalebMessage.query.count() == before, "New conversation deletes nothing")
check("Ask anything you" in page, "and the page starts fresh")
check(owner.post("/caleb/ask", data={"question": "no token"}).status_code in (400, 403), "asking needs the CSRF token")

done()
