"""Shared set-up for the tests in this folder.

Every test file runs in its own process (run_tests.py does that) against a fresh copy of the
site in a temporary folder, so it gets its own empty database and never touches this checkout.
Nothing here reaches the internet: GitHub, the Census map lookup and OpenAI are replaced.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".github", "instance", "tests", "__pycache__", "venv", ".venv", "node_modules"}

FAILS = []
COUNTS = {"pass": 0, "fail": 0, "skip": 0}


def check(ok, what):
    COUNTS["pass" if ok else "fail"] += 1
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        FAILS.append(what)
    return ok


def skip(what):
    COUNTS["skip"] += 1
    print("SKIP  " + what, flush=True)


def done():
    print("\n%d passed, %d failed, %d skipped" % (COUNTS["pass"], COUNTS["fail"], COUNTS["skip"]))
    sys.exit(1 if FAILS else 0)


def make_site():
    """Copy the site to a temporary folder, import its app from there, and return the app module."""
    tmp = tempfile.mkdtemp(prefix="powerhouse-test-")
    site = os.path.join(tmp, "site")
    shutil.copytree(ROOT, site, ignore=lambda folder, names: [n for n in names if n in SKIP_DIRS or n.endswith((".pyc", ".db"))])
    os.environ.pop("OPENAI_API_KEY", None)
    os.chdir(site)
    sys.path.insert(0, site)
    import releases
    releases.TRANSPORT = lambda method, url, headers, body: (599, b'{"message": "tests never call GitHub"}')
    import app as appmod
    appmod.geocode_us_address = lambda address: (40.0379, -76.3055)       # no Census calls in tests
    appmod.app.config["TESTING"] = True
    return appmod


def sign_in(appmod, username="owner", admin=True):
    """A test client signed in as a new user (an admin by default)."""
    app, db = appmod.app, appmod.db
    with app.app_context():
        role = appmod.Role.query.filter_by(is_admin=admin).first()
        if role is None:
            role = appmod.Role(name="Crew", is_admin=False)
            db.session.add(role)
            db.session.flush()
        user = appmod.User(username=username, role=role)
        user.set_password("a-long-password-1")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["csrf_token"] = "test-token"
    return client


CSRF = {"csrf_token": "test-token"}
