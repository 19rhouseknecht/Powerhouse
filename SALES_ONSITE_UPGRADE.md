# Sales, onsite checklist, and reviews upgrade

This additive update adds a Sales section without replacing existing data.

## Included

- Sales sidebar with Onsite Checklist and Reviews.
- Active-job selection with the original job request visible onsite.
- Eight checklist areas and all inspection opportunities from the supplied SLE PDF.
- Expandable, tap-friendly checklist groups with a live selection count.
- Technician observations and client-priority notes.
- Caden-generated Powerhouse inspection tip based on the job description.
- Saved inspection record attached to the job.
- Saved findings included as verified context in Caden's six-option quote generation.
- Job-page inspection summary and direct checklist link.
- Customer-facing Google review card based on the supplied Power House card.
- Verified scannable QR and tappable Google review link.
- Sales access checkbox on the Roles page.
- Guided-tour steps for both new Sales pages.

## Safe installation on PythonAnywhere

Upload `powerhouse-app-sales-onsite-upgrade.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-sales.db
unzip -o powerhouse-app-sales-onsite-upgrade.zip
```

Press **Reload** on the PythonAnywhere Web tab.

The ZIP contains no database, virtual environment, or uploaded customer files.
The new inspection table and Sales permission are added automatically. Existing
users, jobs, quotes, services, pricing, time records, and settings remain intact.
