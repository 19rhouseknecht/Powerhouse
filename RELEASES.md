# Releases: GitHub → the live site

Powerhouse has no test site. **Whatever lands on `main` is a build, and the live site can run any build.**
Admins manage it at **Settings → Releases** (`/settings/releases`).

## What the page does
- **Builds on GitHub** — every commit on `main`, newest first. The one running is marked **Live**.
  - **Deploy** a newer build, or **Roll back to this** on an older one.
- **Auto-deploy** — when on, the newest build on `main` goes live by itself:
  instantly through GitHub's webhook, or within ~5 minutes while anyone is using the site.
  **Rolling back pauses auto-deploy** (otherwise it would jump straight forward again). "Resume" turns it back on and goes to the newest build.
- **Deploy history** — every deploy, who/what started it, files changed, and its log.

## What every deploy does, in order (nothing live changes until step 3 passes)
1. Downloads the commit from GitHub (zips are kept in `instance/releases/builds/`, newest 15).
2. Unpacks it into a scratch folder; refuses paths outside the folder and anything without `app.py`.
3. **Compiles every `.py` file.** A syntax error stops the deploy and the live site is untouched.
4. First deploy only: saves the code that was uploaded by hand (`before-github.zip`), restorable from the history.
5. **Backs up the database** to `instance/backups/` (SQLite's own backup API; newest 20 kept).
6. If `requirements.txt` changed, runs `pip install -r requirements.txt` into the site's virtualenv. A failure stops the deploy.
7. Copies the build over the site folder. Files the previous deploy had and this one doesn't are removed. **Never touched:** `instance/` (database, keys, backups), `static/uploads/`, `venv/`, `.git`, any `.db` file.
8. Writes `instance/release.json` and touches the PythonAnywhere WSGI file, which reloads the web app.

Rollbacks keep all data. New tables/columns from a newer build stay in the database; older code ignores them.
If data itself ever needs restoring, the backups in `instance/backups/` are complete copies of `app.db`.

## One-time setup
1. Upload this version by hand once (as before), `pip install -r requirements.txt` if needed, Reload.
2. On GitHub: Settings → Developer settings → Fine-grained tokens → new token for **19rhouseknecht/Powerhouse** only:
   **Contents: Read**, **Metadata: Read**, **Webhooks: Read and write**.
3. Site → Settings → Releases → Connection: repository `19rhouseknecht/Powerhouse`, branch `main`, paste the token → Connect.
   The site creates its own webhook. The token is stored encrypted (key in `instance/`).
4. From then on, pushing/merging to `main` is deploying.
