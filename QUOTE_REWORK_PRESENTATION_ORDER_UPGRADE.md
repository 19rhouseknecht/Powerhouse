# Quote Rework & Presentation Order Upgrade

This update keeps the existing database, users, jobs, quotes, services, pricing, uploads, and virtual environment intact.

## What changed

- Customer presentations now show Platinum first and Basic last.
- Confirmed quote versions have a **Reopen & rework** action on the job and presentation pages.
- Reopening version 1 edits version 1; it does not silently create version 2.
- An untouched auto-created newer draft is removed when an older version is reopened.
- A newer draft containing real work is protected, and the app asks you to finish it first.
- A quote tied to an accepted option and material order is protected from accidental reopening.
- Client-facing bullets use short, professional scope wording.
- The presentation wording standard is appended to Caden's editable AI Prompt in Settings.
- Older saved quotes receive the same professional presentation wording without changing stored pricing.

## Safe PythonAnywhere install

Upload the ZIP to your PythonAnywhere home folder. In a Bash console, run:

```bash
cd ~
cp powerhouse-app/instance/app.db "powerhouse-app/instance/app-before-quote-rework-$(date +%Y%m%d-%H%M%S).db"
unzip -o powerhouse-flow-quote-rework-presentation-order-2026-09-15.zip
```

Then open the PythonAnywhere **Web** tab and press **Reload**.

The ZIP intentionally contains no database and no virtual environment.

## Using it

Open a job and find the saved quote version. Select **Reopen & rework**, make manual edits or ask Caden to rework it, then select **Confirm & save** again. The quote keeps the same version number throughout.
