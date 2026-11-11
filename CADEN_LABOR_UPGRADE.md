# Caden, AI knowledge, and labor upgrade

This upgrade is additive. It creates Caden and the agent knowledge tables,
adds estimated labor hours to existing services, and creates a company labor
rate set to $361.90. It does not replace users, clients, jobs, quotes, pricing
items, drawings, uploads, or the existing database.

## Install on PythonAnywhere

Upload `powerhouse-app-caden-labor-upgrade.zip` to your home folder, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-caden.db
unzip -o powerhouse-app-caden-labor-upgrade.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Press **Reload** on the PythonAnywhere Web tab.

Open **Settings → Caden** to edit his identity, add knowledge, and upload TXT,
MD, CSV, or text-based PDF references. Open **Pricing Sheet** to edit the
company labor rate. Edit each service to add its estimated labor hours.

Reference files are stored locally with the agent. Their readable text is
extracted and supplied only to that agent when it works. Existing confirmed
quotes keep their previously locked totals.
