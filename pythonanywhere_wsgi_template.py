# This is a TEMPLATE for the WSGI configuration file PythonAnywhere gives you
# on the "Web" tab. Don't upload this file itself — instead, open the file
# PythonAnywhere already created for you (something like
# /var/www/YOURUSERNAME_pythonanywhere_com_wsgi.py), delete everything in it,
# and paste this in — then replace YOURUSERNAME with your actual username in
# both places below.

import sys
import os

project_home = "/home/YOURUSERNAME/powerhouse-app"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set a real secret key here instead of the default in app.py.
# Generate one locally with: python3 -c "import secrets; print(secrets.token_hex(32))"
os.environ["SECRET_KEY"] = "PASTE-A-LONG-RANDOM-STRING-HERE"

# Quote assistant (add these to your live WSGI file when ready).
# Keep the API key out of app.py and never share or commit it.
# os.environ["OPENAI_API_KEY"] = "YOUR-OPENAI-API-KEY"
# os.environ["OPENAI_MODEL"] = "gpt-5-mini"

from app import app as application
