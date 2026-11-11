"""Upgrading keeps the data: build a database with the code on main (what is live), put some records in it,
then start this build on that same database. Every new column must be added, every record kept, and the
pages must open. Skipped when there is no main to compare with."""
import os
import shutil
import subprocess
import sys
import io
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ROOT, check, done, skip  # noqa: E402

base = os.environ.get("UPGRADE_FROM", "origin/main")
try:
    archive = subprocess.check_output(["git", "archive", "--format=tar", base], cwd=ROOT, stderr=subprocess.DEVNULL)
except Exception:  # noqa: BLE001
    skip("no %s to upgrade from" % base)
    done()

old_site = os.path.join(tempfile.mkdtemp(prefix="powerhouse-old-"), "site")
os.makedirs(old_site)
tarfile.open(fileobj=io.BytesIO(archive)).extractall(old_site)
seed = r'''
import sys, os
sys.path.insert(0, os.getcwd())
import app as m
with m.app.app_context():
    c = m.Client(first_name="Old", last_name="Client", email="old@example.com", city="Lancaster")
    m.db.session.add(c); m.db.session.flush()
    m.db.session.add(m.Job(client=c, title="Old job", job_number=700, status="scheduled"))
    m.db.session.commit()
print("seeded")
'''
run = subprocess.run([sys.executable, "-c", seed], cwd=old_site, capture_output=True, text=True)
check(run.returncode == 0 and "seeded" in run.stdout, "the live code (%s) builds its database%s" % (base, "" if run.returncode == 0 else ": " + run.stderr[-500:]))

from harness import sign_in  # noqa: E402
os.environ["POWERHOUSE_TEST_DB_FROM"] = os.path.join(old_site, "instance", "app.db")

def make_with_old_db():
    # copy the old database in before the new code starts
    import harness
    tmp_site = harness.tempfile.mkdtemp(prefix="powerhouse-test-")
    site = os.path.join(tmp_site, "site")
    harness.shutil.copytree(ROOT, site, ignore=lambda folder, names: [n for n in names if n in harness.SKIP_DIRS or n.endswith((".pyc", ".db"))])
    os.makedirs(os.path.join(site, "instance"), exist_ok=True)
    shutil.copy(os.environ["POWERHOUSE_TEST_DB_FROM"], os.path.join(site, "instance", "app.db"))
    os.chdir(site)
    sys.path.insert(0, site)
    import releases
    releases.TRANSPORT = lambda *a: (599, b"{}")
    import app as appmod
    appmod.app.config["TESTING"] = True
    return appmod


appmod = make_with_old_db()
with appmod.app.app_context():
    from sqlalchemy import inspect
    insp = inspect(appmod.db.engine)
    for table in ("client", "job"):
        model_cols = {c.name for c in getattr(appmod, table.capitalize()).__table__.columns}
        db_cols = {c["name"] for c in insp.get_columns(table)}
        check(model_cols <= db_cols, "every %s column exists after the upgrade (missing: %s)" % (table, sorted(model_cols - db_cols) or "none"))
    old = appmod.Client.query.filter_by(email="old@example.com").first()
    check(old is not None and old.jobs[0].title == "Old job", "the records made by the live code are all still there")
    check(old.jobs[0].bucket == "active", "and read correctly")
    old_id, job_id = old.id, old.jobs[0].id
client = sign_in(appmod)
for url in ("/", "/jobs", "/clients", "/clients/%d" % old_id, "/jobs/%d" % job_id, "/settings"):
    check(client.get(url).status_code == 200, "%s opens on the upgraded database" % url)
done()
