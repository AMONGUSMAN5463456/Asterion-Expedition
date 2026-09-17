#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  ASTERION_PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  ASTERION_PYTHON="$(command -v python3)"
else
  echo "Asterion Expedition requires Python 3.10–3.14 and its venv module."
  echo "Install Python for your distribution, then launch this script again."
  exit 1
fi
"$ASTERION_PYTHON" bootstrap.py "$@"
exit $?
