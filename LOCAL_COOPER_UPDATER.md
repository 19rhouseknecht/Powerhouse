# Local Cooper Updater

This additive upgrade preserves the existing SQLite database, users, Pricing
Sheet, services, quotes, uploads, AI agents, and virtual environment.

## Install the PythonAnywhere update

Upload `powerhouse-app-local-cooper-updater.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-local-cooper.db
unzip -o powerhouse-app-local-cooper-updater.zip
```

Press **Reload** on the PythonAnywhere Web tab.

## Create the secure connection

1. Sign in to Power House Flow as an administrator.
2. Open **Settings → Local Cooper Updater**.
3. Select **Generate connection token**.
4. Copy the token immediately. Only its password hash is stored on the server,
   so the original token cannot be displayed again.

Generating a replacement token disconnects the previous scanner. Revoking the
token disables local updater access immediately.

## Run the Mac scanner

Place `cooper_local_updater.py` in its own folder, then run:

```bash
python3 -m pip install playwright
python3 cooper_local_updater.py
```

On the first run, enter the HTTPS address of Power House Flow and paste the
connection token. The scanner stores them in `cooper_updater_config.json` with
owner-only permissions when the operating system supports them. The reusable
visible Chrome session is stored in `cooper_chrome_profile`.

To forget the saved website and token:

```bash
python3 cooper_local_updater.py --reset-config
```

## Pricing safeguards

- Only Pricing Sheet rows with Cooper part numbers are returned to the scanner.
- The scanner searches in visible Chrome and pauses for manual verification.
- Only exact Cooper `data-product-id` matches are accepted.
- Cooper's source price, package quantity, and unit are sent to the server.
- The server validates the exact part number and performs the division.
- `$2,899.33 / 1,000 ft` becomes `$2.89933 / ft`.
- `$240.00 / 100 ea` becomes `$2.40 / ea`.
- Unit mismatches become errors instead of changing a price.
- Every scan creates a preview; no Pricing Sheet price changes until an
  authorized user selects and applies rows.

The upgrade ZIP excludes the database, uploads, API keys, updater tokens,
supplier credentials, encryption keys, and virtual environment.
