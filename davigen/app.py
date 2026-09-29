"""Entry point. From Resolve's Workspace → Scripts menu (Free + Studio) or, with Studio, from a terminal:

    python -m davigen.app
"""

from __future__ import annotations

from .resolve_api import connect
from .server import serve


def main(injected_resolve=None, start: str = "") -> None:
    serve(connect(injected_resolve), start=start)


if __name__ == "__main__":
    main()
