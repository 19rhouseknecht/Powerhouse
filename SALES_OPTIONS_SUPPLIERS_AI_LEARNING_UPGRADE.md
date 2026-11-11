# Power House Flow — Sales, suppliers, and quote-learning upgrade

This package is an additive upgrade for the existing `powerhouse-app` folder. It does not contain a database, virtual environment, credentials, customer uploads, or prior quote files.

## Install on PythonAnywhere

Upload the ZIP to your PythonAnywhere home directory. In a Bash console, run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-sales-options-upgrade.db
unzip -o powerhouse-app-sales-options-suppliers-ai-learning-upgrade.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Then open the PythonAnywhere **Web** tab and press **Reload**.

## What is added

- Onsite inspection home-age context, top-selling opportunities, and multiple jobsite photos
- Multiple supplier and part-number records for each Pricing Sheet item
- Automatic migration of existing Cooper part numbers into the new supplier list
- A three- or six-option selector for regular and quick quotes
- Basic/Gold/Platinum for three-option proposals; the existing six-tier order remains unchanged
- Caden instructions stating that requested scope can belong in any tier and lower real alternatives may be offered
- Cleanup, ordinary setup, labor, and disposal kept inside calculated labor rather than duplicated as quote lines
- A simpler single-column live quote editor
- Clear, safe AI error explanations that preserve the current draft
- Manual quote outcome uploads with an AI-draft-versus-final comparison
- Manual outcomes saved as visible Caden knowledge in Settings

## Database safety

On first reload, the app adds only missing columns and tables. Existing users, roles, clients, jobs, services, pricing links, timesheets, expenses, inspections, and confirmed quotes remain in place. Existing quotes default to six options. Existing Cooper part numbers are copied into supplier records without removing the legacy value.

The backup command above creates a recoverable database copy before the reload.
