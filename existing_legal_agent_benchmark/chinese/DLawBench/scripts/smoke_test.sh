#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/runs/smoke"

rm -rf "$OUT_DIR"

"$ROOT_DIR/scripts/run_dialogues.sh" \
  --case "$ROOT_DIR/data/case_backgrounds/cases_us.jsonl" \
  --persona P_WD \
  --lawyer-model mock-lawyer \
  --client-model mock-client \
  --max-turns 4 \
  --workers 1 \
  --output-dir "$OUT_DIR"

LATEST_SESSION="$(find "$OUT_DIR" -type f -name 'session_*.json' -print | sort | tail -n 1)"

"$ROOT_DIR/scripts/evaluate_sessions.sh" \
  --sessions "$LATEST_SESSION" \
  --case-dir "$ROOT_DIR/data/case_backgrounds" \
  --eval-model mock-judge \
  --workers 1

python3 - "$LATEST_SESSION" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    session = json.load(handle)

talks = [item for item in session.get("log", []) if item.get("type") == "talk"]
memo = session.get("memo") or ""

assert session.get("status") == "memo_submitted", session.get("status")
assert len(talks) >= 1, "No dialogue turns were recorded."
assert len(memo.strip()) > 0, "Memo is empty."
assert "evaluation" in session, "Evaluation was not written."

print(f"Smoke test passed: {path}")
print(f"Dialogue turns: {len(talks)}")
print(f"Memo characters: {len(memo)}")
print("Evaluation present: yes")
PY
