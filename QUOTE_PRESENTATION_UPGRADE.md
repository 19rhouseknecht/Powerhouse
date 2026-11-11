# Simple homeowner quote presentation

This update changes only the confirmed quote presentation opened from a job.

- Keeps the With pricing and Without pricing tabs.
- Shows Basic through Platinum in one clean vertical list.
- Shows each option as professional scope-of-work bullets.
- Removes decorative tier cards, colors, shadows, and marketing subtitles.
- Improves phone and tablet spacing, text size, and tab controls.
- Keeps printing available.

The quote builder, saved quotes, pricing calculations, users, database, and all
other application pages are unchanged.

## Install on PythonAnywhere

Upload the ZIP to your home directory, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-presentation.db
unzip -o powerhouse-app-simple-quote-presentation-upgrade.zip
```

Press **Reload** on the PythonAnywhere Web tab. The ZIP does not contain a
database, uploaded files, or a virtual environment.
