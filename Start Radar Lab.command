#!/bin/zsh
cd "$(dirname "$0")"
PYTHON="$(command -v python3)"
if [[ -z "$PYTHON" ]]; then
  echo "Python 3 is needed. Install Python 3, then open this launcher again."
  read -r
  exit 1
fi
(sleep 1; open 'http://127.0.0.1:8765') &
"$PYTHON" server.py
