# Tools Library + Cooper Listener Upgrade

This update is additive. The ZIP does not contain `instance/app.db`, uploaded files, or a virtual environment.

## Install on PythonAnywhere

Upload the ZIP to your PythonAnywhere home folder. In a Bash console, run:

```bash
cd ~
cp powerhouse-app/instance/app.db "powerhouse-app/instance/app-before-tools-$(date +%Y%m%d-%H%M%S).db"
unzip -o powerhouse-flow-tools-cooper-listener-2026-09-03.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Then use the Web tab to reload the site. Existing services and tools are retained. Missing tool-library tables and columns are created automatically.

## Start the office listener

Download `powerhouse_cooper_price_listener_2026-09-03.py` to the office computer and run:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 powerhouse_cooper_price_listener_2026-09-03.py
```

The first launch asks for the Local Cooper Updater token from Powerhouse Flow Settings and the Cooper username/password. Those values are stored only in `cooper_updater_config.json` beside the listener on the office computer. Leave the terminal open. Future launches need no input; the program checks for commands every five seconds and keeps Chrome closed until a request arrives.

In Powerhouse Flow, open Pricing Sheet and click **Update prices**. Only one update can be active. The page shows live messages from the office listener. When scanning completes, open the review and approve the price changes you want.

Use `Ctrl+C` to stop the listener. Use `--reset-config` if its token changes.
