# Brendon tasking and editable training upgrade

This additive upgrade keeps the existing database, users, tasks, trainings,
files, agents, clients, jobs, quotes, pricing, services, and time records.

## Install on PythonAnywhere

Upload `powerhouse-app-brendon-training-upgrade.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-brendon.db
unzip -o powerhouse-app-brendon-training-upgrade.zip
source powerhouse-app/venv/bin/activate
pip install -r powerhouse-app/requirements.txt
```

Press **Reload** on the Web tab.

## After reload

- Open any training and select **Edit training**.
- Training descriptions and ongoing notes are stored separately.
- Existing training notes are moved into Description during the one-time
  migration so earlier information is not lost.
- Open Tasking to talk with Brendon.
- Brendon can create, assign, update, and complete tasks. He cannot delete them.
- Brendon only updates existing tasks by an exact database task ID supplied to
  him and only assigns exact usernames from the current Powerhouse user list.
- Brendon appears in Settings with his own editable identity, knowledge, and
  reference files.
