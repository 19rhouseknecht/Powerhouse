# Time Tracking, Tasking, Training, and Darwin upgrade

This is an additive upgrade for the existing Powerhouse app. It does not
replace the SQLite database, users, clients, jobs, quotes, services, pricing,
drawings, agent knowledge, uploaded files, or virtual environment.

## Install on PythonAnywhere

Upload `powerhouse-app-operations-upgrade.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-operations.db
unzip -o powerhouse-app-operations-upgrade.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Press **Reload** on the Web tab.

## What appears after reload

- Time Tracking → Time Clock and Timesheet
- Bookkeeping → Tasking and Training
- Settings → Darwin, with his own identity, knowledge, and agent references

The general clock represents the complete paid shift. A job visit is a
classification inside that shift and is not added a second time. General/drive
time equals total paid time minus job-visit time.

Training uploads are saved under `static/uploads/training/`. Text is extracted
from TXT, MD, CSV, and text-based PDF files. Darwin can also view up to five
uploaded training photos when creating a summary or answering a question.
