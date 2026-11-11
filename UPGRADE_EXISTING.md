# Safely upgrade the existing PythonAnywhere app

This ZIP contains no database, virtual environment, or uploaded drawings.

## 1. Upload and install

Upload `powerhouse-app-ai-quote-upgrade.zip` to your PythonAnywhere home folder,
open a Bash console, and run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-quotes.db
unzip -o powerhouse-app-ai-quote-upgrade.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

The final command installs the OpenAI Python package into the existing virtual
environment. It does not recreate or replace that environment.

## 2. Reload

Press **Reload** on PythonAnywhere's Web tab. The new quote tables are created
automatically while existing users, clients, jobs, services, pricing, and
uploads remain unchanged.

## 3. Connect AI when ready

Follow `OPENAI_SETUP.md`. Until an API key is added, the rest of the app works
normally and the Quote Assistant displays a setup notice.
