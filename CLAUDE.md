# Powerhouse — working notes for Claude

Flask app (single `app.py`, templates in `templates/`, CSS in `static/css/style.css`) hosted on PythonAnywhere.
SQLite at `instance/app.db`; tables are created and columns added at boot (`add_column_if_missing` near the end of `app.py`).
Schema changes must be **additive only** (new tables / nullable or defaulted columns) so a rollback to an older build keeps working.

## Main is live
There is no test site. **Anything merged into `main` goes live automatically** (see `RELEASES.md`).
- Work on a branch; merge to `main` only when the user wants it live.
- Before merging: `python -m py_compile app.py releases.py` at minimum (the site refuses builds that don't compile, but don't rely on it), and run the app locally if the change is risky.
- Bump `VERSION` (minor for a new feature/page, patch for fixes). The commit message's first line is the build's title on the Releases page; the body is its notes — write them for the user, in plain words.
- Never commit `instance/`, databases, uploads, keys or tokens (`.gitignore` covers the usual ones).
