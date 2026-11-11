# Caden Labor-Aware Quote Repair Upgrade

This cumulative upgrade is additive. It does not contain or replace the existing database, virtual environment, or uploads.

## New quoting workflow

- The quote assistant has an always-visible **Teach Caden while fixing pricing** box.
- Administrators can describe several components and quantities in plain language, such as four wafer lights and 40 feet of 12/2.
- Caden maps the description to exact existing Services and Pricing Sheet items. It does not invent missing catalog IDs or prices.
- One repair is applied to every affected option instead of repeating the same fix six times.
- Labor lessons such as `0.5 hours per receptacle` or `0.05 hours per foot of 12/2` are saved on the matched Pricing Sheet item for future quotes.
- Every live option displays materials, labor hours, labor cost, and total installed price.
- Detailed line calculations remain visible inside each option.

## Pricing Sheet

Every active item now has:

- Material price per unit
- Unit such as each or foot
- Labor hours per unit
- Cooper part number

The existing company labor rate converts saved labor hours to labor cost. Complete Services continue using their own estimated labor hours so labor is not counted twice.

## Caden settings

The labor-memory and repair instructions are visible in **Settings → AI Agents → Caden → AI Prompt**. Existing Caden prompts receive a clearly labeled `PRICING REPAIR AND LABOR MEMORY` section during the first reload. Administrators can edit it later without changing Python code.

## Install

Upload the ZIP to the PythonAnywhere home folder and run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-caden-labor-upgrade.db
unzip -o powerhouse-app-caden-labor-repair-upgrade.zip
```

Then press **Reload** on the PythonAnywhere Web tab.

## First test

1. Open a draft with missing pricing.
2. Enter: `Use four 6-inch wafer lights and 40 feet of 12/2. Each wafer takes 0.5 hours and 12/2 takes 0.05 hours per foot.`
3. Press **Apply details to pricing**.
4. Confirm that the matching Pricing Sheet rows now contain the saved labor hours and that every affected option shows its complete breakdown.
