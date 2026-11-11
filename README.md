# Power House Electrical Solutions — Operations & Quoting

Flask app with login/user management, a reusable service library for materials,
tools, equipment, and drawings, plus one centralized Pricing Sheet shared by
every service. Client records, job workflow, assignments, and scheduling are
connected in the same workspace.

## What's here

- `app.py` — auth, users, service models, image uploads, and service CRUD.
- Pricing items include units, current prices, Cooper part numbers, categories,
  and service usage links. Price updates flow through linked services.
- Clients include contact details and a primary address.
- Jobs link to clients and track site reviews, quote presentations, work dates,
  estimated duration, status, and assigned team members.
- Schedule combines all job appointments and multi-day work on one calendar.
- The AI Quote Assistant starts from an existing job, asks follow-up questions,
  and builds six fixed tiers from real services and quantity multipliers.
- Confirmed quotes are stored with the job and have client-presentation tabs
  both with and without pricing.
- `templates/` — login, first-run setup, dashboard placeholder, settings.
- `static/` — your real logo assets and the stylesheet.
- `requirements.txt` — Flask, Flask-SQLAlchemy, Flask-Login.
- `DEPLOY.md` — first-time PythonAnywhere deployment.
- `UPGRADE_EXISTING.md` — safe update steps that preserve the database and virtualenv.
- `pythonanywhere_wsgi_template.py` — what to paste into PythonAnywhere's WSGI file.

## Test it locally first (optional but recommended)

If you've got Python 3.9+ on your own machine:

```bash
cd powerhouse-app
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000`. A new install starts at **Create the first
account**. Existing installs keep their users; the new service tables are
created automatically without replacing the existing `User` table.

## Then deploy

For an existing live site, follow `UPGRADE_EXISTING.md`. Do not repeat the
first-time virtualenv or database steps.
