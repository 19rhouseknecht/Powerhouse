"""The office listener on a shaky connection: a DNS hiccup ("getaddrinfo failed") or the site reloading is tried again
instead of ending the scan; a site that stays unreachable is named with what to check; an answer that says no (401) is not
retried; finished results that cannot be uploaded are kept in a file. No network is used: urlopen is replaced."""
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ROOT, check, done  # noqa: E402

spec = importlib.util.spec_from_file_location("listener_net", os.path.join(ROOT, "tools", "cooper_price_listener.py"))
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
slept, logged = [], []
listener.time.sleep = slept.append                       # this copy of the module only
listener.log = logged.append
config = {"base_url": "https://www.powerhouseflow.com", "token": "t"}
DNS = URLError(socket.gaierror(11001, "getaddrinfo failed"))


class Reply(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def script(*steps):
    queue = list(steps)
    def fake(request, timeout=60):
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return Reply(json.dumps(step).encode())
    listener.urlopen = fake
    return queue


queue = script(DNS, DNS, {"command": None})
check(listener.api_request(config, "/api/local-cooper/commands/next") == {"command": None} and not queue, "two DNS hiccups in a row, then the answer: it gets the answer")
check(slept == list(listener.RETRY_DELAYS[:2]), "it waits a little longer before each try")
check(any("trying again" in line for line in logged), "and says it is trying again")

slept.clear()
script(*[DNS] * (len(listener.RETRY_DELAYS) + 1))
try:
    listener.api_request(config, "/x"); check(False, "a site that never answers fails in the end")
except listener.SiteUnreachable as error:
    words = str(error)
    check("www.powerhouseflow.com" in words and "nslookup" in words and "--reset-config" in words, "the failure names the address and what to check")
check(len(slept) == len(listener.RETRY_DELAYS), "after every retry")

slept.clear()
script(HTTPError("u", 401, "no", {}, io.BytesIO(b'{"error": "Invalid token"}')))
try:
    listener.api_request(config, "/x")
except RuntimeError as error:
    check("401" in str(error) and "Invalid token" in str(error) and not slept, "a 401 (a real answer) is not retried")

script(HTTPError("u", 503, "busy", {}, io.BytesIO(b"<html>")), {"ok": True})
check(listener.api_request(config, "/x") == {"ok": True}, "the site reloading (503) is retried")

listener.CONFIG_PATH = Path(tempfile.mkdtemp()) / "cooper_updater_config.json"
kept = listener.save_unsent(7, {"results": [{"part_number": "QO120"}]})
check(kept.exists() and json.loads(kept.read_text())["results"][0]["part_number"] == "QO120", "results that cannot be uploaded are kept in a file")

source = open(os.path.join(ROOT, "tools", "cooper_price_listener.py")).read()
check("Progress not sent" in source and "delays=RESULT_RETRY_DELAYS" in source, "missed progress never stops a scan; the upload gets the longest retries")
done()
