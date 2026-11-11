"""Settings -> Releases against a pretend GitHub that serves real zip files: connecting, auto-deploy
(only for builds whose tests passed), the checks every deploy makes, the webhook, rollback, resume
and restoring the code that was live before GitHub."""
import hashlib
import hmac
import io
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

appmod = make_site()
import releases as rel  # noqa: E402  (the site's copy, imported by make_site)

SITE = os.getcwd()
REPO = "19rhouseknecht/Powerhouse"
TOKEN = "ghp_TESTTOKEN1234"
COMMITS = []            # newest first: {sha, title, files}
TESTS = {}              # sha -> 'passed' | 'failed' | 'running'   (missing: no run)
PULLS = {}              # main sha -> (pr number, head sha)
CALLS = []


def make_build(title, changes=None, remove=(), tests="passed"):
    files = dict(COMMITS[0]["files"])
    for key in remove:
        files.pop(key, None)
    files.update(changes or {})
    sha = hashlib.sha1((title + str(len(COMMITS))).encode()).hexdigest()
    COMMITS.insert(0, {"sha": sha, "title": title, "files": files})
    if tests:
        TESTS[sha] = tests
    return sha


def zipball(commit):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        top = "19rhouseknecht-Powerhouse-%s/" % commit["sha"][:7]
        for key, value in commit["files"].items():
            archive.writestr(top + key, value)
        archive.writestr(top + "instance/app.db", b"EVIL")          # must never reach the live folder
    return buf.getvalue()


def commit_json(commit):
    return {"sha": commit["sha"], "html_url": "https://github.com/%s/commit/%s" % (REPO, commit["sha"]),
            "commit": {"message": commit["title"] + "\n\nWhat changed, in plain words.\n\nCo-Authored-By: x <y>",
                       "author": {"name": "Reese", "date": "2026-11-11T12:00:00Z"}, "committer": {"date": "2026-11-11T12:00:00Z"}}}


def run_json(sha):
    state = TESTS[sha]
    return {"name": "tests", "head_sha": sha, "status": "in_progress" if state == "running" else "completed",
            "conclusion": None if state == "running" else ("success" if state == "passed" else "failure"),
            "run_started_at": "2026-11-11T12:00:00Z", "started_at": "2026-11-11T12:00:00Z"}


def transport(method, url, headers, body):
    CALLS.append((method, url))
    assert headers["Authorization"] == "Bearer " + TOKEN
    path = url.replace(rel.API, "")
    find = lambda sha: next((c for c in COMMITS if c["sha"] == sha), None)  # noqa: E731
    if path == "/repos/" + REPO and method == "GET":
        return 200, json.dumps({"full_name": REPO, "private": True}).encode()
    if path.endswith("/hooks") and method == "POST":
        return 201, b'{"id": 77}'
    if "/hooks/" in path and method == "DELETE":
        return 204, b""
    if "/check-runs" in path:
        sha = path.split("/commits/")[1].split("/")[0]
        return 200, json.dumps({"check_runs": [run_json(sha)] if sha in TESTS else []}).encode()
    if "/actions/runs?branch=" in path:
        return 200, json.dumps({"workflow_runs": [run_json(c["sha"]) for c in COMMITS if c["sha"] in TESTS]}).encode()
    if path.endswith("/pulls") and "/commits/" in path:
        sha = path.split("/commits/")[1].split("/")[0]
        if sha in PULLS:
            number, head = PULLS[sha]
            return 200, json.dumps([{"number": number, "merged_at": "2026-11-11T12:00:00Z", "base": {"ref": "main"}, "head": {"sha": head}}]).encode()
        return 200, b"[]"
    if "/commits?sha=main" in path:
        return 200, json.dumps([commit_json(c) for c in COMMITS]).encode()
    if path.endswith("/commits/main"):
        return 200, json.dumps(commit_json(COMMITS[0])).encode()
    if "/commits/" in path:
        commit = find(path.rsplit("/", 1)[1])
        return (200, json.dumps(commit_json(commit)).encode()) if commit else (404, b'{"message":"Not Found"}')
    if "/zipball/" in path:
        return 200, zipball(find(path.rsplit("/", 1)[1]))
    return 404, b'{"message":"Not Found"}'


rel.TRANSPORT = transport
initial = {r: open(os.path.join(SITE, r), "rb").read() for r in rel.walk(SITE)}
COMMITS.append({"sha": "a" * 40, "title": "Initial import", "files": initial})
TESTS["a" * 40] = "passed"

app, db, Release = appmod.app, appmod.db, appmod.Release


def settle(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        with app.app_context():
            db.session.remove()
            if not Release.query.filter_by(status="running").first():
                return
        time.sleep(0.2)
    raise SystemExit("a deploy did not finish")


def live():
    with app.app_context():
        db.session.remove()
        row = appmod.release_live()
        return (row.sha, row.source, row.version) if row else (None, None, None)


def auto(label="test"):
    with app.app_context():
        said = appmod.release_check(label)
    settle()
    return said


client = sign_in(appmod)
os.makedirs(os.path.join(SITE, "static", "uploads", "receipts"), exist_ok=True)
with open(os.path.join(SITE, "static", "uploads", "receipts", "r.jpg"), "wb") as handle:
    handle.write(b"RECEIPT")
db_path = os.path.join(SITE, "instance", "app.db")

page = client.get("/settings/releases")
check(page.status_code == 200 and b"Not connected" in page.data, "the page opens before GitHub is connected")

r = client.post("/settings/releases/connect", data=dict(CSRF, repo="https://github.com/19rhouseknecht/Powerhouse.git", branch="main", token=TOKEN), follow_redirects=True)
check(b"Connected to 19rhouseknecht/Powerhouse" in r.data, "connects with a repository address and a classic token")
with app.app_context():
    check(TOKEN not in db.session.get(appmod.CompanySetting, "releases_token").value, "the token is stored encrypted")
    hook_secret = appmod.release_secret("hook_secret")
check(any(m == "POST" and u.endswith("/hooks") for m, u in CALLS), "it makes its own webhook")

# --- a passing build goes live by itself
sha2 = make_build("Add hello page", {"templates/hello.html": b"<p>hello</p>", "VERSION": b"9.0.0\n"})
said = auto()
check("Deploying" in said, "a newer build whose tests passed is deployed by itself")
check(live()[:3:2] == (sha2, "9.0.0"), "it is live, with the version from its VERSION file")
with app.app_context():
    row = appmod.release_live()
    snap = Release.query.filter_by(source="snapshot").first()
    check(snap is not None and os.path.isfile(snap.zip_path), "the hand-uploaded code was saved before the first deploy")
    check(bool(row.db_backup) and os.path.isfile(row.db_backup), "the database was backed up first")
check(os.path.isfile(os.path.join(SITE, "templates", "hello.html")), "the new file is in place")
check(open(os.path.join(SITE, "static/uploads/receipts/r.jpg"), "rb").read() == b"RECEIPT", "uploads are never touched")
check(open(db_path, "rb").read()[:4] != b"EVIL", "the database is never overwritten by a build")
check(json.load(open(os.path.join(SITE, "instance", "release.json")))["sha"] == sha2, "release.json says what is live")
check("already runs" in auto(), "the same build is not deployed twice")

# --- the tests gate
sha3 = make_build("Untested change", {"templates/untested.html": b"x"}, tests="running")
said = auto()
check("waiting" in said and "still running" in said, "a build whose tests are still running waits")
check(live()[0] == sha2, "and the live site keeps the build it has")
TESTS[sha3] = "failed"
said = auto()
check("waiting" in said and "failed" in said, "a build whose tests failed never goes live by itself")
page = client.get("/settings/releases?refresh=1").get_data(as_text=True)
check("Tests failed" in page and "Tests passed" in page, "the page shows each build's tests")
check("Waiting:" in page, "the page says why the newest build is waiting")
TESTS[sha3] = "running"
PULLS[sha3] = (7, "f" * 40)
TESTS["f" * 40] = "passed"
said = auto()
check("Deploying" in said and live()[0] == sha3, "a merged pull request whose tests passed goes live without waiting for main's own run")
TESTS[sha3] = "passed"

# --- a build that does not compile is refused even when deployed by hand
sha4 = make_build("Broken", {"app.py": COMMITS[0]["files"]["app.py"] + b"\ndef oops(:\n"}, tests="passed")
auto()
with app.app_context():
    bad = Release.query.filter_by(sha=sha4).first()
    check(bad.status == "failed" and "compile" in bad.message, "a build that does not compile is refused")
    check("will not be retried" in appmod.release_check("test"), "and not retried by itself")
check(live()[0] == sha3, "the live site is unchanged")
check(b"def oops(:" not in open(os.path.join(SITE, "app.py"), "rb").read(), "app.py was not touched")

sha5 = make_build("Fix, drop hello", {"app.py": initial["app.py"]}, remove=["templates/hello.html"])
auto()
check(live()[0] == sha5, "the fixed build goes live")
check(not os.path.exists(os.path.join(SITE, "templates", "hello.html")), "a file the new build dropped is removed")

# --- the webhook
body = json.dumps({"ref": "refs/heads/main"}).encode()
r = client.post("/releases/github-hook", data=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"})
check(r.status_code == 403, "an unsigned webhook is refused")
sha6 = make_build("Webhook build", {"templates/hook.html": b"hook"}, tests="running")
sign = lambda raw: "sha256=" + hmac.new(hook_secret.encode(), raw, hashlib.sha256).hexdigest()  # noqa: E731
r = client.post("/releases/github-hook", data=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(body)})
check(r.status_code == 202, "a signed push is accepted")
time.sleep(1)
settle()
check(live()[0] == sha5, "but its build waits while its tests run")
TESTS[sha6] = "passed"
done_body = json.dumps({"action": "completed", "workflow_run": {"name": "tests", "head_branch": "main", "head_sha": sha6}}).encode()
r = client.post("/releases/github-hook", data=done_body, headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": sign(done_body)})
check(r.status_code == 202, "GitHub saying the tests finished is accepted")
time.sleep(1)
settle()
check(live()[0] == sha6, "and the build goes live the moment its tests pass")

# --- rolling back by hand pauses auto-deploy; resuming goes back to the newest
r = client.post("/settings/releases/deploy", data=dict(CSRF, sha=sha2))
settle()
check(live()[:2] == (sha2, "rollback"), "rolling back puts an older build live")
with app.app_context():
    check(not appmod.release_auto_on(), "rolling back pauses auto-deploy")
    check("paused" in appmod.release_check("test").lower(), "a newer build waits while paused")
check(os.path.isfile(os.path.join(SITE, "templates", "hello.html")) and not os.path.exists(os.path.join(SITE, "templates", "hook.html")), "the files are the older build's")
page = client.get("/settings/releases").get_data(as_text=True)
check("Paused" in page and "Roll back to this" in page, "the page says auto-deploy is paused")
client.post("/settings/releases/auto", data=dict(CSRF, on="1"))
settle()
with app.app_context():
    check(appmod.release_auto_on(), "resuming switches auto-deploy back on")
check(live()[0] == sha6, "and goes to the newest build")

# --- the untested build can still be deployed by hand
r = client.post("/settings/releases/deploy", data=dict(CSRF, sha=sha3))
settle()
check(live()[0] == sha3, "any build can still be put live by hand")
client.post("/settings/releases/auto", data=dict(CSRF, on="1"))
settle()

# --- restoring the pre-GitHub code
with app.app_context():
    snap_id = Release.query.filter_by(source="snapshot").first().id
client.post("/settings/releases/%d/restore" % snap_id, data=CSRF)
settle()
check(live()[1] == "snapshot", "the code from before GitHub can be restored")
check(not os.path.exists(os.path.join(SITE, "templates", "hook.html")), "with only its own files")
check(client.get("/settings/releases/status").json["running"] is False, "the status poll answers")
done()
