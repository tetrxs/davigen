"""Development only: run Python inside Resolve from a terminal (Resolve Free has no external scripting).

Start it once from Resolve's Workspace → Scripts menu through a launcher like

    exec(open("/Users/<you>/Desktop/davigen/scripts/dev_bridge.py", encoding="utf-8").read())

It listens on 127.0.0.1:8799 and writes its token to data/dev_bridge_token.txt. Then, from a terminal:

    scripts/dev_exec.sh 'print(resolve.GetProjectManager().GetCurrentProject().GetName())'

Requests run one at a time (Resolve's API isn't thread-safe) in one shared namespace, so names defined by one
request are there for the next. It ends itself after two idle hours, or with POST /quit.
"""

import contextlib
import io
import json
import os
import secrets
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.join(os.path.expanduser("~"), "Desktop", "davigen")
PORT = 8799
IDLE = 7200

_resolve = globals().get("resolve") or app.GetResolve()  # noqa: F821 - injected by Resolve
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_token = secrets.token_urlsafe(16)
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
with open(os.path.join(ROOT, "data", "dev_bridge_token.txt"), "w", encoding="utf-8") as _f:
    _f.write(_token)
_ns = {"resolve": _resolve}
_state = {"last": time.time(), "stop": False}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, data):
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        _state["last"] = time.time()
        if self.headers.get("X-Token") != _token:
            self.send_response(403)
            self.end_headers()
            return
        code = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        if self.path == "/quit":
            _state["stop"] = True
            return self._reply({"ok": True})
        out = io.StringIO()
        error = ""
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                exec(compile(code, "<dev>", "exec"), _ns)
            except BaseException:  # noqa: BLE001 - reported back
                error = traceback.format_exc()
        self._reply({"out": out.getvalue(), "error": error})


_httpd = HTTPServer(("127.0.0.1", PORT), _Handler)
_httpd.timeout = 1
print(f"dev bridge on 127.0.0.1:{PORT}")
while not _state["stop"] and time.time() - _state["last"] < IDLE:
    _httpd.handle_request()
_httpd.server_close()
