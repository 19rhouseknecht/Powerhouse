# Deploying to PythonAnywhere

You've got a paid PythonAnywhere account already, so this is the "Manual
configuration" Flask path. About 15 minutes.

## 1. Get the code onto PythonAnywhere

Easiest way with no git involved:

1. Zip up the `powerhouse-app` folder on your computer (you should already
   have it as a .zip from this chat — `powerhouse-app.zip`).
2. On PythonAnywhere, go to the **Files** tab.
3. Upload `powerhouse-app.zip` to your home directory (`/home/YOURUSERNAME/`).
4. Open a **Bash console** (Consoles tab → Bash) and run:
   ```bash
   cd ~
   unzip powerhouse-app.zip
   ```
   You should now have `/home/YOURUSERNAME/powerhouse-app/`.

## 2. Create a virtualenv and install dependencies

Still in the Bash console:

```bash
cd ~/powerhouse-app
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

(If `python3.10` isn't available, run `python3 --version` to see what you've
got and use that instead — anything 3.9+ is fine.)

## 3. Set up the web app

1. Go to the **Web** tab → **Add a new web app**.
2. Pick your domain (the free `YOURUSERNAME.pythonanywhere.com` is fine to
   start).
3. Choose **Manual configuration** (not "Flask" — the Flask option scaffolds
   a different file layout than what we're using).
4. Pick the Python version that matches the venv you just made (3.10).

## 4. Point it at your virtualenv

On the Web tab, find the **Virtualenv** section and enter:
```
/home/YOURUSERNAME/powerhouse-app/venv
```

## 5. Edit the WSGI file

Still on the Web tab, click the link near the top that looks like
`/var/www/YOURUSERNAME_pythonanywhere_com_wsgi.py` — this opens PythonAnywhere's
editor.

Delete everything in that file and replace it with the contents of
`pythonanywhere_wsgi_template.py` (included in this folder) — just swap in
your actual username in both spots, and generate a real secret key:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```
Paste that output in place of `PASTE-A-LONG-RANDOM-STRING-HERE`.

## 6. Set the source code directory

Back on the Web tab, under **Code**, set:
- **Source code**: `/home/YOURUSERNAME/powerhouse-app`
- **Working directory**: `/home/YOURUSERNAME/powerhouse-app`

## 7. Map static files

Still under the Web tab, find **Static files** and add:
| URL | Directory |
|---|---|
| `/static/` | `/home/YOURUSERNAME/powerhouse-app/static` |

This makes the logo and stylesheet load fast without going through Flask.

## 8. Reload

Hit the big green **Reload** button at the top of the Web tab.

## 9. First login

Visit your site. Since there are no users yet, you'll land on a **Create the
first account** page — that's expected, not a bug. Set up your own login,
then go to **Settings** once you're in and add your second team member.

## Notes for later

- The user database is a SQLite file at `powerhouse-app/instance/app.db`.
  PythonAnywhere's disk is persistent, so this survives reloads and restarts
  — but it's still worth occasionally downloading a copy as a backup
  (Files tab → download the file).
- Whenever you upload new code, hit **Reload** on the Web tab again — Flask
  apps on PythonAnywhere don't auto-restart on file changes.
- If something looks broken after a deploy, check the **Error log** link on
  the Web tab first — it'll usually tell you exactly what's wrong.
