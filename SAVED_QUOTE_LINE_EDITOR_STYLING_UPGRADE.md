# Saved Quote Line Editor Styling Upgrade

This update preserves the existing database, users, jobs, quotes, services, pricing, uploads, and virtual environment.

## Changes

- All saved-quote manual-editor inputs and text areas now match the Powerhouse Flow interface.
- Added consistent dark backgrounds, borders, spacing, focus highlights, and mobile sizing.
- Every client-facing presentation line now has its own text box.
- Individual lines can be removed.
- **Add line item** inserts another editable presentation line immediately.
- Empty lines are ignored when saving.
- Saving still updates the same confirmed quote version and never contacts AI.

## Safe PythonAnywhere install

Upload the ZIP to your PythonAnywhere home folder, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db "powerhouse-app/instance/app-before-quote-line-editor-$(date +%Y%m%d-%H%M%S).db"
unzip -o powerhouse-flow-saved-quote-line-editor-styling-2026-09-16.zip
```

Open the PythonAnywhere **Web** tab and press **Reload**.

The ZIP contains no database and no virtual environment.
