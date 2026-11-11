# Delete, Duplicate Protection, and Caden Prompt Upgrade

This is an additive upgrade for the existing Power House Flow installation. It does not include or replace `instance/app.db`, the virtual environment, or uploaded files.

## What changed

- Jobs can be deleted from the job detail page. Their quote drafts, confirmed quotes, and onsite checklists are removed with the job. Payroll clock sessions are preserved, expenses are detached from the deleted job, and unassigned quick quotes remain available.
- Pricing Sheet items can be removed even when they are used by services. Their current service material lines are removed, while confirmed quote history retains its locked name and price.
- Pricing items are checked by a punctuation-insensitive name and by Cooper part number. Existing items are reused instead of creating duplicates.
- A **Clean up duplicates** action merges existing exact-name or exact-part-number duplicates, combines repeated quantities inside the same service, and keeps confirmed quote history intact.
- Repeated missing-price issues are grouped once and a repair is applied across every affected tier.
- Caden rejects duplicate adjacent options. Every tier from Basic through Platinum must add scope, quantity, or quality compared with the tier below it.
- Caden's working instructions are visible under **Settings → AI Agents → Caden → AI Prompt** and can be edited without changing Python code. Structural pricing and validation safeguards remain enforced by the application.

## Install on PythonAnywhere

Upload the ZIP to the PythonAnywhere home folder, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-dedupe-upgrade.db
unzip -o powerhouse-app-delete-dedupe-ai-prompt-upgrade.zip
```

Open the PythonAnywhere **Web** tab and press **Reload**.

## Recommended first check

1. Open **Quoting → Pricing Sheet** and run **Clean up duplicates** once.
2. Open **Settings → AI Agents → Caden** and review the new **AI Prompt** field.
3. Generate a test quote and confirm every successive tier contains a meaningful improvement.
