#!/bin/bash
# Double-click in Finder, or run: bash Start-Mac.command
cd -- "$(dirname -- "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  ASTERION_PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  ASTERION_PYTHON="$(command -v python3)"
elif [ -x "/opt/homebrew/bin/python3" ]; then
  ASTERION_PYTHON="/opt/homebrew/bin/python3"
elif [ -x "/usr/local/bin/python3" ]; then
  ASTERION_PYTHON="/usr/local/bin/python3"
else
  echo "Asterion Expedition requires Python 3.10–3.14."
  echo "Install 64-bit Python from https://www.python.org/downloads/ and try again."
  if [ -t 0 ]; then read -r -p "Press Return to close. " ASTERION_REPLY; fi
  exit 1
fi
"$ASTERION_PYTHON" bootstrap.py "$@"
ASTERION_EXIT=$?
if [ "$ASTERION_EXIT" -ne 0 ] && [ -t 0 ]; then
  echo ""
  echo "The game stopped with code $ASTERION_EXIT. See the message above."
  read -r -p "Press Return to close. " ASTERION_REPLY
fi
exit "$ASTERION_EXIT"
