# Cooper Electric browser pricing-sync upgrade

This additive upgrade preserves the existing database, users, supplier items,
services, quotes, agents, tasks, trainings, jobs, clients, uploads, and virtual
environment.

## Before installation

Change any Cooper password that has been shared in chat or another insecure
location. Do not reuse that password in the Powerhouse application.

## Install on PythonAnywhere

Upload the latest Powerhouse Flow upgrade ZIP, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-cooper.db
unzip -o powerhouse-app-cooper-login-search-fix.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Press **Reload** on the Web tab.

This version uses PythonAnywhere's preinstalled Chromium browser at
`/usr/bin/chromium`. Do not run `playwright install chromium`; the supplied
browser is selected automatically.

## Connect and update

1. Open **Pricing Sheet**.
2. Select **Check Cooper prices**. A live window in the bottom-right shows the
   cookie and public catalog activity.
3. Review exact part-number matches, deselect anything unexpected, and select
   **Apply selected prices**.

The app never changes prices during the check. It requires an exact Cooper
Electric part-number match and a separate apply step. Errors and unmatched
items remain unchanged.

## Credential backup

The encrypted password is stored in SQLite. Its encryption key is created as:

```text
powerhouse-app/instance/supplier-credentials.key
```

Keep that key with database backups. It is intentionally excluded from upgrade
ZIP files. Anyone restoring the database to another server must restore this
key too, or re-enter the Cooper password.

The browser clicks Cooper's **Accept All** cookie button when it is shown. It
then opens `/product/search?q=PART_NUMBER` directly for every item, using the
same public request submitted by Cooper's header search form. It does not sign
in and does not depend on locating the visible header search bar. Cooper may
still change its website or
restrict automated server access. When that happens, the live activity window
shows the failing step and no Pricing Sheet price is modified.
