#!/bin/sh
# Start the Decompathon Steward for thps64 under its watchdog on Linux (the watchdog restarts a hung or dead bot;
# systemd restarts the watchdog). Logs: ~/.decompathon/bot.log, bot.err.log. Usage: start_steward.sh [JUDGE_WS]
# GNU nice runs the command anyway (with a warning) when a negative niceness is not permitted.
set -e
HERE=$(cd "$(dirname "$0")/../.." && pwd)
JUDGE=${1:-${THPS64_JUDGE:-$HOME/thps64-judge}}
cd "$HERE"
[ -x .venv/bin/python ] && PY=.venv/bin/python || PY=python3
exec nice -n -5 "$PY" -u -m steward.watchdog -- --repo andrewnakas/thps64 --app decompathon-steward \
  --ai-maintainer --judge-workspace "$JUDGE"
