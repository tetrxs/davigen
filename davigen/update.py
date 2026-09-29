"""Is there a newer davigen – and install it with the same installer the README uses.

The installer records the commit it installed in <install>/.commit. A git checkout (development) is never updated
from here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import urllib.request

from . import __version__
from .config import ROOT

REPO = "tetrxs/davigen"
BRANCH = "main"
INSTALLER = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/install.sh"


def _get(url: str, accept: str = "application/json", timeout: float = 8.0) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"davigen/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - fixed https URLs
        return res.read()


def installed() -> dict:
    commit_file = ROOT / ".commit"
    commit = commit_file.read_text(encoding="utf-8").strip() if commit_file.exists() else ""
    dev = (ROOT / ".git").exists()
    branch = ""
    if dev:
        res = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                             text=True, check=False)
        branch = res.stdout.strip()
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=False).stdout.strip()
    return {"version": __version__, "commit": commit[:7], "full_commit": commit, "dev": dev, "branch": branch,
            "folder": str(ROOT)}


def latest() -> dict:
    """The newest commit on main and the version it carries."""
    data = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"))
    init = _get(f"https://raw.githubusercontent.com/{REPO}/{data['sha']}/davigen/__init__.py", "text/plain").decode()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    return {"commit": data["sha"][:7], "full_commit": data["sha"],
            "date": data["commit"]["committer"]["date"],
            "message": data["commit"]["message"].splitlines()[0][:140],
            "version": match.group(1) if match else ""}


class Updater:
    def __init__(self):
        self.state: dict = {"checking": False, "running": False, "done": False, "ok": None, "log": [],
                            "latest": None, "error": ""}

    def snapshot(self) -> dict:
        inst = installed()
        lat = self.state["latest"]
        if inst["dev"] or not lat:
            available = None
        elif inst["full_commit"]:
            available = lat["full_commit"] != inst["full_commit"]
        else:                                   # installed before the installer recorded its commit
            available = lat["version"] != inst["version"] or None
        return {**self.state, "installed": inst, "available": available}

    def check(self) -> dict:
        self.state.update(checking=True, error="")
        try:
            self.state["latest"] = latest()
        except Exception as e:  # noqa: BLE001 - shown in the UI
            self.state["error"] = f"Couldn't reach GitHub: {e}"
        finally:
            self.state["checking"] = False
        return self.snapshot()

    def run(self) -> dict:
        if installed()["dev"]:
            return {"ok": False, "error": "This is a development checkout – update it with git"}
        if self.state["running"]:
            return {"ok": True}
        self.state.update(running=True, done=False, ok=None, log=[], error="")

        def work():
            env = {**os.environ, "DAVIGEN_HOME": str(ROOT)}
            try:
                proc = subprocess.Popen(["/bin/zsh", "-c", f"curl -fsSL {INSTALLER} | zsh"], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace")
                for line in proc.stdout:            # type: ignore[union-attr]
                    clean = re.sub(r"\x1b\[[0-9;]*m", "", line.rstrip())
                    if clean:
                        self.state["log"] = (self.state["log"] + [clean])[-40:]
                self.state["ok"] = proc.wait() == 0
                if not self.state["ok"]:
                    self.state["error"] = "The installer stopped – see the log"
            except Exception as e:  # noqa: BLE001
                self.state.update(ok=False, error=str(e))
            finally:
                self.state.update(running=False, done=True)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}
