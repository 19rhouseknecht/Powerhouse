# Powerhouse — working notes for Claude

Flask app (single `app.py`, templates in `templates/`, CSS in `static/css/style.css`, release machinery in `releases.py`)
hosted on PythonAnywhere. SQLite at `instance/app.db`; tables are created and columns added at boot (`add_column_if_missing`
near the end of `app.py`). Schema changes must be **additive only** (new tables / nullable or defaulted columns) so a
rollback to an older build keeps working. **Never delete anything** — data, files, branches or features — unless the user asks.

## How a change ships (do all of it yourself; never ask "should I merge?")
There is no test site: `main` is live. A change reaches `main` only through a pull request whose **tests** check passed.
The user said (Nov 11, 2026): "there is tests you can run that if they pass it merges" — so once the check is green, merge it.
1. **Branch** from the newest main: `git fetch origin main && git checkout -b <what-it-is> origin/main`.
2. **Build it, with tests.** Add or extend a `tests/test_*.py` for what changed (see `tests/harness.py`: `make_site()`,
   `sign_in()`, `check()`, `done()`). A page that changes what it does in the browser gets browser checks
   (`tests/test_browser_flow.py` is the pattern; it skips where no browser is installed).
3. **Bump `VERSION`**: minor (1.4.0) for something new, patch (1.3.1) for a fix. `python tools/check_version.py origin/main`.
4. **Run the tests locally**: `python run_tests.py` must end `0 failed`. In a Claude Code cloud session, point the browser
   test at the preinstalled Chromium: `PW_CHROMIUM=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux*/chrome | head -1) python run_tests.py`.
5. **Commit and push the branch.** The commit/PR title is the build's title on the Releases page; the body is its notes:
   plain words for the user, what changed and why.
6. **Open the pull request** with the REST API (`gh pr create` uses GraphQL, which these sessions refuse):
   `gh api repos/19rhouseknecht/Powerhouse/pulls -f title="1.4.0 — …" -f head=<branch> -f base=main -F body=@notes.md`
7. **Wait for "tests"** (a few minutes):
   `gh api repos/19rhouseknecht/Powerhouse/commits/<head-sha>/check-runs --jq '.check_runs[] | {name, status, conclusion}'`.
   Red: read the log (`gh api repos/19rhouseknecht/Powerhouse/actions/runs?branch=<branch>`), fix, push again.
8. **Merge it yourself** when green and up to date with main (`gh api repos/19rhouseknecht/Powerhouse/compare/main...<branch> --jq .behind_by` = 0;
   otherwise rebase on main, settle any overlap keeping both sides' work, push, wait for tests again):
   `gh api -X PUT repos/19rhouseknecht/Powerhouse/pulls/<n>/merge -f merge_method=squash -f sha=<head-sha> -f commit_title="1.4.0 — … (#<n>)"`.
   Leave the branch. Then tell the user it is merged and will go live by itself.
9. Never push straight to `main`, never force-push `main`, never merge a red or still-running check.

## What the live site does with main (`releases.py`, Settings → Releases, `RELEASES.md`)
Auto-deploy takes the newest `main` only when its own "tests" run passed, or the pull request it came from passed
(`releases.passed`). Every deploy compiles every `.py` first, backs up the database, never touches `instance/`,
`static/uploads/` or the virtualenv, and reloads the web app. Admins can still deploy or roll back to any build by hand.
Keep the workflow and job name `tests` (`releases.CHECK_NAME`).

## Never commit
`instance/`, databases, uploads, keys or tokens (`.gitignore` covers the usual ones; look anyway).
