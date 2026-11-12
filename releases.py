"""Releases: the live site takes its builds straight from GitHub.

There is no test site. Whatever lands on the repository's main branch is a build, and the
Releases page (Settings -> Releases) can put any build live: the newest one automatically,
or any older one by hand (a rollback).

This module is the machinery only, with no database or Flask in it, so it can be tested on
its own. app.py owns the Release table, the settings, the page and the order things happen in:

    list_builds / head      what the branch has, newest first
    download                one commit as a zip (GitHub's zipball), cached per commit
    stage                   unpack a zip into a scratch folder, refuse anything unsafe, and
                            compile every .py file so a build with a syntax error never goes live
    backup_database         a consistent copy of app.db (SQLite's own backup API) before each deploy
    sync                    copy the staged build over the live folder; data is never touched
    reload_web_app          touch the PythonAnywhere WSGI file, which reloads the web app

What is never written, removed or packed (PROTECT): instance/ (the database, keys, backups,
these release files), static/uploads/, virtualenvs, .git, and any database file.
"""
import glob
import hashlib
import hmac
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime

API = "https://api.github.com"
AGENT = "Powerhouse releases"
TRANSPORT = None   # tests hand in a callable (method, url, headers, body) -> (status, bytes)

PROTECT_DIRS = ("instance", "static/uploads", "venv", ".venv", "env", ".git", ".github", ".claude", "__pycache__", "node_modules")
PROTECT_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pyc")
PROTECT_NAMES = (".env", ".DS_Store", "Thumbs.db")
ANYWHERE = {"__pycache__", ".git", "node_modules"}      # skipped at any depth
MUST_HAVE = "app.py"
KEEP_ZIPS = 15
KEEP_DB_BACKUPS = 20


class ReleaseError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


# --------------------------------------------------------------------------- GitHub
def _said(payload):
    try:
        data = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload or "{}")
    except (ValueError, UnicodeDecodeError):
        return ""
    return " ".join(str(data.get("message") or "").split())[:160] if isinstance(data, dict) else ""


def gh(method, path, token, repo, body=None, raw=False, timeout=30):
    """One call to GitHub's REST API. Raises ReleaseError with a sentence a person can act on."""
    if not token or not repo:
        raise ReleaseError("GitHub is not connected yet: add the repository and a token under Connection.")
    url = path if path.startswith("https://") else API + path.replace("{repo}", repo)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": AGENT}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    if TRANSPORT is not None:
        status, payload = TRANSPORT(method, url, headers, data)
    else:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:   # a zipball redirects to codeload; followed here
                status, payload = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        except Exception as exc:  # noqa: BLE001
            raise ReleaseError("Could not reach GitHub (%s). Try again in a minute." % exc.__class__.__name__)
    words = _said(payload) if status >= 400 else ""
    quoted = (' GitHub says: "%s".' % words) if words else ""
    if status == 401:
        raise ReleaseError("GitHub did not accept the token (401). Make a new one and save it under Connection." + quoted, 401)
    if status == 403:
        raise ReleaseError("GitHub refused the request (403)." + quoted + " A classic token needs the repo scope (and "
                           "admin:repo_hook for instant deploys); a fine-grained one needs Contents, Actions and Metadata: Read "
                           "(and Webhooks: Read and write).", 403)
    if status == 404:
        raise ReleaseError("GitHub found nothing there (404): check the repository name and that the token can see it." + quoted, 404)
    if status >= 400:
        raise ReleaseError("GitHub answered %d.%s" % (status, quoted), status)
    if raw:
        return payload
    try:
        return json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload or "{}")
    except ValueError:
        raise ReleaseError("GitHub did not answer with data.")


def clean_repo(text):
    """'https://github.com/owner/name.git' -> 'owner/name'; '' when it is not that shape."""
    text = (text or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/", "git@github.com:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    parts = [p.strip() for p in text.split("/") if p.strip()]
    return "/".join(parts[:2]) if len(parts) >= 2 else ""


def _commit_row(item):
    commit = item.get("commit") or {}
    message = (commit.get("message") or "").strip()
    title, _, body = message.partition("\n")
    when = ((commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date") or "")
    try:
        when_dt = datetime.strptime(when[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        when_dt = None
    body = "\n".join(line for line in body.strip().splitlines()
                     if not line.lower().startswith(("co-authored-by:", "claude-session:", "signed-off-by:"))).strip()
    return {"sha": item.get("sha") or "", "short": (item.get("sha") or "")[:7], "title": title[:250], "notes": body[:4000],
            "author": ((commit.get("author") or {}).get("name") or "")[:120], "date": when_dt,
            "url": item.get("html_url") or ""}


def list_builds(token, repo, branch="main", count=30):
    data = gh("GET", "/repos/{repo}/commits?sha=%s&per_page=%d" % (branch, count), token, repo)
    return [_commit_row(item) for item in (data if isinstance(data, list) else [])]


def head(token, repo, branch="main"):
    return _commit_row(gh("GET", "/repos/{repo}/commits/%s" % branch, token, repo))


def commit(token, repo, sha):
    return _commit_row(gh("GET", "/repos/{repo}/commits/%s" % sha, token, repo))


# --------------------------------------------------------------------------- the "tests" gate
CHECK_NAME = "tests"     # the job in .github/workflows/tests.yml; keep the names in step


def _runs(token, repo, sha):
    """The commit's "tests" runs as [{status, conclusion, at}]: its check runs, or, when the token may not
    read checks (GitHub answers 403), the same result from Actions' workflow runs."""
    try:
        data = gh("GET", "/repos/{repo}/commits/%s/check-runs?check_name=%s&per_page=20" % (sha, CHECK_NAME), token, repo)
        return [{"status": r.get("status"), "conclusion": r.get("conclusion"), "at": r.get("started_at") or ""}
                for r in (data.get("check_runs") or [])]
    except ReleaseError as exc:
        if exc.status != 403:
            raise
    data = gh("GET", "/repos/{repo}/actions/runs?head_sha=%s&per_page=20" % sha, token, repo)
    return [{"status": r.get("status"), "conclusion": r.get("conclusion"), "at": r.get("run_started_at") or r.get("created_at") or ""}
            for r in (data.get("workflow_runs") or []) if r.get("name") == CHECK_NAME]


def tests_result(token, repo, sha):
    """'passed', 'failed', 'running' or 'none' for one commit, from its newest "tests" run."""
    runs = _runs(token, repo, sha) if sha else []
    if not runs:
        return "none"
    newest = sorted(runs, key=lambda r: r.get("at") or "", reverse=True)[0]
    if newest.get("status") != "completed":
        return "running"
    return "passed" if newest.get("conclusion") == "success" else "failed"


def passed(token, repo, sha, branch="main"):
    """(ok, why). A build may go live by itself when its own tests passed, or, while its own run is still
    going (or never ran), when the pull request it was merged from passed its tests."""
    own = tests_result(token, repo, sha)
    if own == "passed":
        return True, "its tests passed"
    if own == "failed":
        return False, "its tests failed"
    pulls = gh("GET", "/repos/{repo}/commits/%s/pulls" % sha, token, repo)
    pull = next((p for p in (pulls if isinstance(pulls, list) else [])
                 if p.get("merged_at") and (p.get("base") or {}).get("ref") == branch), None)
    if pull is not None:
        theirs = tests_result(token, repo, (pull.get("head") or {}).get("sha") or "")
        if theirs == "passed":
            return True, "pull request #%s passed its tests" % pull.get("number")
        if theirs == "failed":
            return False, "pull request #%s failed its tests" % pull.get("number")
    if own == "running":
        return False, "its tests are still running"
    return False, "no tests have run on it"


def test_badges(token, repo, branch="main"):
    """{sha: 'passed' | 'failed' | 'running'} for the branch's recent builds, from one call."""
    data = gh("GET", "/repos/{repo}/actions/runs?branch=%s&per_page=100" % branch, token, repo)
    found = {}
    for run in sorted((data.get("workflow_runs") or []), key=lambda r: r.get("run_started_at") or r.get("created_at") or "", reverse=True):
        if run.get("name") != CHECK_NAME or run.get("head_sha") in found:
            continue
        if run.get("status") != "completed":
            found[run.get("head_sha")] = "running"
        else:
            found[run.get("head_sha")] = "passed" if run.get("conclusion") == "success" else "failed"
    return found


def create_hook(token, repo, url, secret, old_id=""):
    if old_id:
        try:
            gh("DELETE", "/repos/{repo}/hooks/%s" % old_id, token, repo)
        except ReleaseError:
            pass
    hook = gh("POST", "/repos/{repo}/hooks", token, repo, {"name": "web", "active": True, "events": ["push", "workflow_run"],
              "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"}})
    return str(hook.get("id") or "")


def upgrade_hook(token, repo, hook_id):
    """Make an older webhook (push only) also send "tests finished", so a build goes live as soon as it passes."""
    gh("PATCH", "/repos/{repo}/hooks/%s" % hook_id, token, repo, {"active": True, "events": ["push", "workflow_run"]})


def delete_hook(token, repo, hook_id):
    if hook_id:
        gh("DELETE", "/repos/{repo}/hooks/%s" % hook_id, token, repo)


def signature_ok(secret, body, signature):
    if not secret or not signature:
        return False
    want = "sha256=" + hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature)


def download(token, repo, sha, folder):
    """The commit as a zip, kept in folder (re-used when it is already there)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s.zip" % sha)
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        os.utime(path, None)
        return path
    payload = gh("GET", "/repos/{repo}/zipball/%s" % sha, token, repo, raw=True, timeout=120)
    tmp = path + ".part"
    with open(tmp, "wb") as handle:
        handle.write(payload)
    if not zipfile.is_zipfile(tmp):
        os.remove(tmp)
        raise ReleaseError("GitHub did not send a zip for %s." % sha[:7])
    os.replace(tmp, path)
    prune(folder, "*.zip", KEEP_ZIPS, keep=("before-github.zip",))
    return path


# --------------------------------------------------------------------------- files
def protected(rel):
    rel = rel.replace("\\", "/").strip("/")
    name = rel.rsplit("/", 1)[-1]
    if name in PROTECT_NAMES or name.endswith(PROTECT_SUFFIXES):
        return True
    if any(part in ANYWHERE for part in rel.split("/")):
        return True
    return any(rel == d or rel.startswith(d + "/") for d in PROTECT_DIRS)


def walk(root):
    """Every file under root that a build owns, as sorted relative paths."""
    found = []
    for folder, dirs, files in os.walk(root):
        rel_folder = os.path.relpath(folder, root).replace("\\", "/")
        rel_folder = "" if rel_folder == "." else rel_folder + "/"
        dirs[:] = [d for d in dirs if not protected(rel_folder + d)]
        for name in files:
            rel = rel_folder + name
            if not protected(rel) and not os.path.islink(os.path.join(folder, name)):
                found.append(rel)
    return sorted(found)


def stage(zip_path, scratch):
    """Unpack the build into a fresh folder under scratch and check it. Returns the folder."""
    os.makedirs(scratch, exist_ok=True)
    target = tempfile.mkdtemp(prefix="stage-", dir=scratch)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [n for n in archive.namelist() if not n.endswith("/")]
            if not names:
                raise ReleaseError("That build is empty.")
            top = names[0].split("/", 1)[0]
            wrapped = MUST_HAVE not in names and all(n.startswith(top + "/") for n in names)
            for name in names:
                rel = name[len(top) + 1:] if wrapped else name
                norm = os.path.normpath(rel).replace("\\", "/")
                if not rel or norm.startswith("../") or norm == ".." or os.path.isabs(norm):
                    raise ReleaseError("The build has a file outside its folder (%s), so it was refused." % name)
                if protected(norm):
                    continue
                info = archive.getinfo(name)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:      # a symlink: never followed or written
                    continue
                dest = os.path.join(target, norm)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with archive.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        if not os.path.isfile(os.path.join(target, MUST_HAVE)):
            raise ReleaseError("That build has no %s, so it is not a Powerhouse build." % MUST_HAVE)
        check_python(target)
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def check_python(tree):
    """Compile every .py file. A syntax error stops the deploy before anything live is touched."""
    out = tempfile.mkdtemp(prefix="pyc-")
    try:
        for rel in walk(tree):
            if rel.endswith(".py"):
                try:
                    py_compile.compile(os.path.join(tree, rel), cfile=os.path.join(out, "x.pyc"), doraise=True)
                except py_compile.PyCompileError as exc:
                    detail = " ".join(str(exc.msg).replace(tree + os.sep, "").replace(tree, "").split())[-300:]
                    raise ReleaseError("The build does not compile, so it was not deployed: %s (%s)" % (rel, detail))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def read_version(tree):
    try:
        with open(os.path.join(tree, "VERSION"), encoding="utf-8") as handle:
            return handle.read().strip()[:40]
    except OSError:
        return ""


def file_hash(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def sync(src, dst, previous_manifest):
    """Copy every file of the staged build over dst. Files the last deployed build had and this one
    does not are removed; nothing else in dst is ever removed. Returns (changed, removed, manifest)."""
    manifest = walk(src)
    changed = 0
    for rel in manifest:
        source, target = os.path.join(src, rel), os.path.join(dst, rel)
        if os.path.isfile(target) and file_hash(target) == file_hash(source):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".deploying"
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
        changed += 1
    removed = 0
    keep = set(manifest)
    for rel in previous_manifest or []:
        if rel in keep or protected(rel):
            continue
        path = os.path.join(dst, rel)
        if os.path.isfile(path) and os.path.realpath(path).startswith(os.path.realpath(dst) + os.sep):
            os.remove(path)
            removed += 1
    return changed, removed, manifest


def snapshot_code(base, zip_path, limit_bytes=300 * 1024 * 1024):
    """Zip the code that is live right now (data left out), so it can be put back."""
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    total = 0
    tmp = zip_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in walk(base):
            if rel.endswith(".zip"):
                continue
            path = os.path.join(base, rel)
            total += os.path.getsize(path)
            if total > limit_bytes:
                break
            archive.write(path, rel)
    os.replace(tmp, zip_path)
    return zip_path


def backup_database(db_path, folder, label):
    """A consistent copy of the SQLite database, taken with SQLite's backup API. Returns its path or ''."""
    if not os.path.isfile(db_path):
        return ""
    os.makedirs(folder, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in label)[:60]
    path = os.path.join(folder, "app-%s-%s.db" % (datetime.now().strftime("%Y%m%d-%H%M%S"), safe))
    source = sqlite3.connect(db_path, timeout=30)
    try:
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    prune(folder, "app-*.db", KEEP_DB_BACKUPS)
    return path


def prune(folder, pattern, keep_count, keep=()):
    files = sorted((p for p in glob.glob(os.path.join(folder, pattern)) if os.path.basename(p) not in keep),
                   key=os.path.getmtime, reverse=True)
    for path in files[keep_count:]:
        try:
            os.remove(path)
        except OSError:
            pass


def pip_install(base, timeout=900):
    """Install requirements.txt into the virtualenv the site runs in. (ok, last lines of output)."""
    candidates = [os.path.join(sys.prefix, "bin", "python"), os.path.join(base, "venv", "bin", "python")]
    python = next((p for p in candidates if os.path.isfile(p)), "")
    if not python:
        return False, "Could not find the site's Python to install requirements with."
    try:
        run = subprocess.run([python, "-m", "pip", "install", "-q", "-r", os.path.join(base, "requirements.txt")],
                             capture_output=True, text=True, timeout=timeout, cwd=base)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "pip could not run (%s)." % exc
    tail = "\n".join((run.stdout + run.stderr).strip().splitlines()[-12:])
    return run.returncode == 0, tail


# --------------------------------------------------------------------------- reload and lock
def find_wsgi(host=""):
    """The PythonAnywhere WSGI file for this site: the one named after the host, or the only one there is."""
    files = sorted(glob.glob("/var/www/*_wsgi.py"))
    if host:
        named = "/var/www/%s_wsgi.py" % host.split(":")[0].lower().replace(".", "_").replace("-", "_")
        if named in files:
            return named
    return files[0] if len(files) == 1 else ""


def reload_web_app(wsgi_path):
    """Touching the WSGI file is PythonAnywhere's own way to reload a web app. (ok, sentence)."""
    if not wsgi_path:
        return False, "Files are updated. Press Reload on PythonAnywhere's Web tab to start using them (the WSGI file was not found)."
    try:
        os.utime(wsgi_path, None)
        return True, "The web app is reloading."
    except OSError as exc:
        return False, "Files are updated, but the reload failed (%s). Press Reload on PythonAnywhere's Web tab." % exc.strerror


class Lock:
    """One deploy at a time across every web worker. A lock older than STALE seconds is abandoned."""
    STALE = 30 * 60

    def __init__(self, path):
        self.path = path
        self.held = False

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            if time.time() - os.path.getmtime(self.path) > self.STALE:
                os.remove(self.path)
        except OSError:
            pass
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        self.held = True
        return True

    def release(self):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False

    def clear(self):
        """Remove a lock left behind by a worker that was stopped before it could let go."""
        try:
            os.remove(self.path)
        except OSError:
            pass
        self.held = False

    def busy(self):
        try:
            return time.time() - os.path.getmtime(self.path) <= self.STALE
        except OSError:
            return False
