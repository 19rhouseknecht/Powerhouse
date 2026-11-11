# Final Smart Quoter Workflow Upgrade

This cumulative update does not include or replace the existing database, virtual environment, or uploaded files.

## Missing-price workflow

The global **Teach Caden while fixing pricing** panel has been removed. Every missing-price card now contains four focused choices:

1. Link a complete Service.
2. Link an existing Pricing Sheet item.
3. Create a new Pricing Sheet item with material price and labor hours per unit.
4. Explain that one missing installation to Caden in plain language.

Option 4 only receives the selected missing scope. It applies the repair across every affected tier, uses exact existing catalog pricing, learns explicitly supplied labor, and reports anything it cannot safely match.

All dropdowns, text fields, number inputs, text areas, focus states, and mobile layouts in options 1–4 now match the rest of Power House Flow.

## Manual live-option editing

Every live Basic-through-Platinum card now includes **Edit this option manually**. An administrator can:

- Change the option title
- Change the homeowner description
- Rewrite the client-facing bullets
- Change line names and quantities
- Enter or override material price per unit
- Enter or override labor hours per unit
- Remove a line
- Add a custom manually priced line

A formerly missing line becomes complete when valid manual material or labor pricing is supplied. If a linked Pricing Sheet item has zero labor and the administrator supplies labor, that empty catalog labor value is filled for future quotes.

Manual edits are authoritative. They survive **Rework quote with Caden** and are not overwritten by **Confirm & save**. A manually finished quote can therefore proceed directly to the customer Present page without another AI request.

The new manual-edit rules are appended visibly to **Settings → AI Agents → Caden → AI Prompt** under `MANUAL QUOTE EDITS`.

## Install

Upload the ZIP to the PythonAnywhere home folder and run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-final-quoter-upgrade.db
unzip -o powerhouse-app-final-smart-quoter-upgrade.zip
```

Then press **Reload** on the PythonAnywhere Web tab.
