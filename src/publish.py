"""Publish the daily report to GitHub Pages.

GitHub Pages serves the ``docs/`` folder on the default branch. So each morning
we:

    1. copy today's report into ``docs/`` (keeping its ``market_report_*.html``
       name so older briefings stay linkable), and
    2. copy it again to ``docs/index.html`` — the file GitHub Pages serves at
       the site root (``https://<user>.github.io/market-monitor/``).

We then stage, commit and push those files with ``git`` (via ``subprocess``) so
the live site updates automatically. Everything is a soft skip: if git is not
available, the repo has no remote, or there is nothing new to commit, we warn
and return without crashing the daily pipeline.
"""

import os
import shutil
import subprocess
from datetime import datetime

# Project root = the folder containing this ``src/`` package.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")


def _run_git(args):
    """Run a git command in the project root.

    Returns a (ok, output) tuple: ok is True when git exits 0, and output is
    the combined stdout/stderr text (stripped) for logging.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _copy_into_docs(report_path):
    """Copy the report into ``docs/`` and as ``docs/index.html``.

    Returns the list of destination paths that were written.
    """
    os.makedirs(DOCS_DIR, exist_ok=True)

    named_copy = os.path.join(DOCS_DIR, os.path.basename(report_path))
    index_copy = os.path.join(DOCS_DIR, "index.html")

    shutil.copy2(report_path, named_copy)
    shutil.copy2(report_path, index_copy)
    return [named_copy, index_copy]


def publish_to_github(report_path):
    """Copy *report_path* into ``docs/`` and push it to GitHub Pages.

    Copies the report to ``docs/index.html`` (what Pages serves) plus a
    dated copy, then runs ``git add`` / ``commit`` / ``push``. Commits with the
    message ``"Daily briefing - YYYY-MM-DD"``.

    Returns True when the push succeeds, False on any soft skip (git missing,
    no remote, nothing to commit, or a failed command). Never raises — the
    daily pipeline should keep going even if publishing fails.
    """
    if not os.path.isfile(report_path):
        print(f"      ! Report not found at {report_path}; cannot publish.")
        return False

    # 1. Copy the report into docs/ (named copy + index.html).
    try:
        written = _copy_into_docs(report_path)
    except OSError as exc:
        print(f"      ! Could not copy report into docs/: {exc}")
        return False
    print(f"      Copied report into docs/ ({', '.join(os.path.basename(p) for p in written)}).")

    # 2. Confirm git is available and this is a git repository.
    try:
        ok, _ = _run_git(["rev-parse", "--is-inside-work-tree"])
    except FileNotFoundError:
        print("      ! git is not installed or not on PATH; skipping publish.")
        return False
    if not ok:
        print("      ! Not a git repository (run 'git init' and add a remote); "
              "skipping publish.")
        return False

    # 3. Stage the docs/ folder.
    ok, out = _run_git(["add", "docs"])
    if not ok:
        print(f"      ! 'git add docs' failed: {out}")
        return False

    # 4. Commit. If nothing changed, git exits non-zero — treat as a soft skip.
    message = f"Daily briefing - {datetime.now().strftime('%Y-%m-%d')}"
    ok, out = _run_git(["commit", "-m", message])
    if not ok:
        if "nothing to commit" in out.lower():
            print("      Nothing new to publish (docs/ already up to date).")
            return False
        print(f"      ! 'git commit' failed: {out}")
        return False
    print(f"      Committed: {message}")

    # 5. Push to the remote so GitHub Pages rebuilds.
    ok, out = _run_git(["push"])
    if not ok:
        print(f"      ! 'git push' failed: {out}")
        print("        Make sure a remote named 'origin' is set and you can "
              "push to it.")
        return False

    print("      Pushed to GitHub — Pages will update shortly.")
    return True
