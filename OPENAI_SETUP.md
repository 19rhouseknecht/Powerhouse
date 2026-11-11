# Connect the quote assistant on PythonAnywhere

The application is fully installed without an API key, but the chat Send
button remains disabled until the key is configured.

## 1. Create an API key

Create a secret key from your OpenAI API account. API usage is billed separately
from a ChatGPT subscription. Do not paste the key into `app.py`, a client form,
or the database.

## 2. Add the key to the PythonAnywhere WSGI file

Open PythonAnywhere's **Web** tab and click the WSGI configuration-file link.
Add these lines after the existing `import os` line and before
`from app import app as application`:

```python
os.environ["OPENAI_API_KEY"] = "PASTE-YOUR-SECRET-KEY-HERE"
os.environ["OPENAI_MODEL"] = "gpt-5-mini"
```

The model is configurable. Changing `OPENAI_MODEL` later does not require an
application-code update.

## 3. Reload and test

Press **Reload** on the Web tab. Open **Generate Quote**, select a job, and send
a message. If the key is connected, the amber setup notice disappears.

If a request fails, check the PythonAnywhere error log. The chat conversation
is preserved, so you can correct the configuration and try again.
