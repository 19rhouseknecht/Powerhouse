# Power House Flow bulk operations upgrade

This upgrade is additive and does not include or replace the live SQLite
database, virtual environment, uploaded service drawings, agent files, training
files, receipts, or supplier credential key.

## Install on PythonAnywhere

Upload `powerhouse-app-flow-bulk-upgrade.zip` to the home folder, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-flow-bulk.db
unzip -o powerhouse-app-flow-bulk-upgrade.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Press **Reload** on the PythonAnywhere Web tab.

## Automatic additive upgrade

On first reload the app:

- creates Admin, Office, Foreman, and Installer roles;
- assigns existing users to Admin;
- adds hourly pay rates with a safe default of `$0.00`;
- gives existing jobs sequential job numbers beginning at 700;
- adds job finance fields, expenses, quick quotes, and role permissions;
- preserves all existing clients, jobs, quotes, services, pricing, time, tasks,
  trainings, users, passwords, and uploads.

After reload, open **Settings** as an administrator to set each employee's role
and hourly rate. Review **Settings → Roles & permissions** before rolling the app
out to employees.

## Payroll anchor

Pay periods run for 14 days from Thursday through the second Wednesday. The
calendar is anchored to Thursday, August 13, 2026 through Wednesday, August 26,
2026, matching the supplied last completed pay period.

## Mobile app

Adding the site to a phone home screen uses the name **Power House Flow** and
the Powerhouse icon. The navigation drawer is collapsed on phones and opens
with the menu button.
