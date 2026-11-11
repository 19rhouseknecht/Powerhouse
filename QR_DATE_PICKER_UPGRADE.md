# Review QR and date picker upgrade

This update builds on the Sales and Onsite Checklist release.

## Changes

- Replaces the review-card QR with the supplied clean raw image.
- Verifies the QR and matches the tappable review button to the same Google review destination.
- Adds an obvious Choose date, Choose time, or Choose date & time button to every applicable field.
- Uses the phone, tablet, or desktop browser's native calendar and time controls.
- Uses 15-minute increments for date-time and time fields.
- Covers new/edit job scheduling, task due dates, training dates, and expense dates.
- Automatically enhances future date and time fields added to Power House Flow.
- Preserves all existing date storage, schedule calculations, and database records.

## Install on PythonAnywhere

Upload `powerhouse-app-qr-date-picker-upgrade.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-date-pickers.db
unzip -o powerhouse-app-qr-date-picker-upgrade.zip
```

Press **Reload** on the PythonAnywhere Web tab. The ZIP contains no database,
virtual environment, or uploaded customer files.
