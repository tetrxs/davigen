"""UI preview without Resolve: a fake resolve object; scanning works, 'create' will fail at the Resolve step."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davigen import server  # noqa: E402


class _FakePM:
    def GetCurrentProject(self):
        return None


class FakeResolve:
    def GetProjectManager(self):
        return _FakePM()

    def GetProductName(self):
        return "DaVinci Resolve (preview)"

    def GetVersionString(self):
        return "21.0.0"


if __name__ == "__main__":
    server.IDLE_TIMEOUT = 3600
    server.serve(FakeResolve(), open_browser="--open" in sys.argv)
