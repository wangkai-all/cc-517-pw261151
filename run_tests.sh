#!/usr/bin/env bash
# Run the whole test suite: solver/calibration/API unit tests + frontend smoke test.
set -euo pipefail
cd "$(dirname "$0")"

echo "== python unit tests =="
python3 -m unittest discover -s tests

echo "== frontend smoke test =="
DB=$(mktemp -u /tmp/planner-smoke-XXXXXX.db)
python3 run.py --port 8765 --db "$DB" >/tmp/planner-smoke.log 2>&1 &
SRV=$!
cleanup() { kill "$SRV" 2>/dev/null || true; rm -f "$DB"; }
trap cleanup EXIT
sleep 1.5
node tests/frontend_smoke.mjs 8765
echo "== all tests passed =="
