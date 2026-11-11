"""Run every test in tests/ (each file in its own process) and finish with one line.

    python run_tests.py              # everything
    python run_tests.py flow jobs    # only the files whose names contain these words

GitHub runs this on every pull request and every push to main (.github/workflows/tests.yml).
The live site only deploys a build by itself when this passed (Settings -> Releases).
"""
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))


def main(words):
    files = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    if words:
        files = [f for f in files if any(w in os.path.basename(f) for w in words)]
    failed = []
    started = time.time()
    for path in files:
        name = os.path.basename(path)
        print("\n=== %s" % name, flush=True)
        run = subprocess.run([sys.executable, path], cwd=ROOT)
        if run.returncode != 0:
            failed.append(name)
    print("\n" + "=" * 60)
    print("%d test file%s, %d failed%s  (%.0fs)" % (len(files), "" if len(files) == 1 else "s", len(failed),
                                                  (": " + ", ".join(failed)) if failed else "", time.time() - started))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
