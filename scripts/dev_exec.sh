#!/bin/zsh
# Development only: run Python inside Resolve through scripts/dev_bridge.py. Code as argument or on stdin.
ROOT=${0:A:h:h}
CODE=${1:-$(cat)}
curl -s -m ${DEV_TIMEOUT:-600} -H "X-Token: $(cat $ROOT/data/dev_bridge_token.txt)" --data-binary "$CODE" \
  http://127.0.0.1:8799/exec | "$ROOT/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(d["out"], end=""); print(d["error"], end="", file=sys.stderr)'
