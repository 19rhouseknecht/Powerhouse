# Saved Quote Editor & Economy Tier Upgrade

This update preserves the existing database, users, jobs, quotes, services, pricing, uploads, and virtual environment.

## New quote tiers

Six-option quotes now use:

1. Economy
2. Basic
3. Bronze
4. Silver
5. Gold
6. Platinum

Customer presentations continue to show the highest option first: Platinum through Economy. Existing quote scope and pricing stay in their original positions while the old tier names are migrated. Three-option quotes use Economy, Gold, and Platinum.

## Manual saved-quote editor

Every confirmed quote on a job now has a **Manual edit** button. Its separate editor can change:

- Quote title and introduction
- Option titles and descriptions
- Client-facing bullet points
- Final customer price for every option

Saving edits updates that exact quote version. It does not contact Caden, create a new version, or reopen the quote as a draft. The original pricing breakdown remains visible for reference.

The separate **Autoquoter** button remains available when a saved quote truly needs to be reopened and reworked with Caden.

## Safe PythonAnywhere install

Upload the ZIP to your PythonAnywhere home folder, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db "powerhouse-app/instance/app-before-saved-quote-editor-$(date +%Y%m%d-%H%M%S).db"
unzip -o powerhouse-flow-saved-quote-editor-economy-tiers-2026-09-15.zip
```

Open the PythonAnywhere **Web** tab and press **Reload**.

The ZIP contains no database and no virtual environment.
