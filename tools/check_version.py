"""Run by GitHub on every pull request: VERSION must be higher than main's, so every build that goes
live has its own number on the Releases page.

    python tools/check_version.py [base]      base: what to compare with (default origin/main)
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(text):
    found = re.match(r"^(\d+)\.(\d+)\.(\d+)$", (text or "").strip())
    return tuple(int(x) for x in found.groups()) if found else None


def main(base="origin/main"):
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as handle:
        mine = handle.read().strip()
    try:
        theirs = subprocess.check_output(["git", "show", "%s:VERSION" % base], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:  # noqa: BLE001 — nothing to compare with
        theirs = ""
    if parse(mine) is None:
        print("FAIL  VERSION must look like 1.2.0 (it says %r)." % mine)
        return 1
    if theirs and parse(theirs) and parse(mine) <= parse(theirs):
        print("FAIL  VERSION is %s but main is already at %s: give this change the next number "
              "(minor for something new, patch for a fix)." % (mine, theirs))
        return 1
    print("PASS  VERSION %s is above main's %s" % (mine, theirs or "(none)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
