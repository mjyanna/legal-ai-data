#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Evaluate existing session files
# ═══════════════════════════════════════════════════════════════════════
#
# Reads session JSONs from dialogue runs, runs LLM-as-Judge evaluation,
# and writes scores back into each session file.
#
# Case files are auto-detected from session metadata (case_id / case_file).
# Can be run in parallel on different batches of sessions.
#
# Usage:
#   # Evaluate all sessions from a specific run
#   ./scripts/evaluate_sessions.sh --sessions "runs/session_*_20260407_*.json"
#
#   # Evaluate sessions for a specific model
#   ./scripts/evaluate_sessions.sh --sessions "runs/session_*_gpt_*.json"
#
#   # Evaluate with a different judge model
#   ./scripts/evaluate_sessions.sh --sessions "runs/session_*.json" \
#       --eval-model claude-opus-4-6
#
#   # Write to new files instead of in-place update
#   ./scripts/evaluate_sessions.sh --sessions "runs/session_*.json" \
#       --output-suffix reeval
#
#   # Run in parallel on different model batches
#   ./scripts/evaluate_sessions.sh --sessions "runs/session_*_gpt_*.json" &
#   ./scripts/evaluate_sessions.sh --sessions "runs/session_*_claude_*.json" &
#   wait
#
# Output:
#   Updates session JSONs in-place (adds/replaces "evaluation" key)
#   Writes runs/eval_summary_{timestamp}.json
#
# ═══════════════════════════════════════════════════════════════════════

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH_VALUE="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

BOLD="\033[1m"
GREEN="\033[32m"
RESET="\033[0m"

usage() {
    echo "Usage: $0 --sessions GLOB [options]"
    echo ""
    echo "Required:"
    echo "  --sessions GLOB        Glob pattern for session JSON files"
    echo ""
    echo "Optional:"
    echo "  --eval-model MODEL     Judge model (default: from config)"
    echo "  --case PATH            Case JSON override (auto-detected if omitted)"
    echo "  --case-dir DIR         Case backgrounds directory (default: data/case_backgrounds/)"
    echo "  --output-suffix STR    Write new files instead of in-place"
    echo "  --workers N            Parallel evaluations (default: 3)"
    exit 1
}

# ── Parse args ────────────────────────────────────────────────────────

SESSIONS=""
EVAL_MODEL=""
CASE=""
OUTPUT_SUFFIX=""
CASE_DIR_ARG=""
PANEL=""
RETRY_ERRORS=""
WORKERS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sessions)       SESSIONS="$2"; shift 2 ;;
        --eval-model)     EVAL_MODEL="$2"; shift 2 ;;
        --case)           CASE="$2"; shift 2 ;;
        --output-suffix)  OUTPUT_SUFFIX="$2"; shift 2 ;;
        --case-dir)       CASE_DIR_ARG="$2"; shift 2 ;;
        --panel)          PANEL="1"; shift ;;
        --retry-errors)   RETRY_ERRORS="1"; shift ;;
        --workers)        WORKERS="$2"; shift 2 ;;
        -h|--help)        usage ;;
        *)                echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$SESSIONS" ]]; then
    echo "Error: --sessions is required"
    usage
fi

# ── Build command ─────────────────────────────────────────────────────

CMD=(env "PYTHONPATH=$PYTHONPATH_VALUE" python3 -m dlawbench.run_evaluation --sessions "$SESSIONS")

[[ -n "$EVAL_MODEL" ]]    && CMD+=(--eval-model "$EVAL_MODEL")
[[ -n "$CASE" ]]          && CMD+=(--case "$CASE")
[[ -n "$OUTPUT_SUFFIX" ]] && CMD+=(--output-suffix "$OUTPUT_SUFFIX")
[[ -n "$CASE_DIR_ARG" ]]  && CMD+=(--case-dir "$CASE_DIR_ARG")
[[ -n "$PANEL" ]]         && CMD+=(--panel)
[[ -n "$RETRY_ERRORS" ]]  && CMD+=(--retry-errors)
[[ -n "$WORKERS" ]]       && CMD+=(--workers "$WORKERS")

YELLOW="\033[33m"
RED="\033[31m"

# ── Run with self-check loop ──────────────────────────────────────────

echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Evaluating sessions${RESET}"
echo -e "  Sessions: ${GREEN}$SESSIONS${RESET}"
[[ -n "$EVAL_MODEL" ]] && echo -e "  Judge: ${GREEN}$EVAL_MODEL${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo ""

# Determine output directory for ok file
SESSION_DIR=$(dirname "$SESSIONS")
[[ -z "$SESSION_DIR" || "$SESSION_DIR" == "." ]] && SESSION_DIR="$ROOT_DIR"
[[ "$SESSION_DIR" != /* ]] && SESSION_DIR="$ROOT_DIR/$SESSION_DIR"

MAX_ROUNDS=10
ROUND=1
# In retry rounds >= 2, sessions with JSON null memo are treated as complete.

while [[ $ROUND -le $MAX_ROUNDS ]]; do
    echo -e "\n${BOLD}  [─ Round $ROUND / $MAX_ROUNDS ─]${RESET}"

    RETRY_CMD=("${CMD[@]}")
    if [[ ! " ${RETRY_CMD[*]} " =~ " --retry-errors " ]]; then
        RETRY_CMD+=(--retry-errors)
    fi
    RETRY_CMD+=(--shell-retry-round "$ROUND")
    "${RETRY_CMD[@]}" || RC=$?
    RC=${RC:-0}

    if [[ $RC -eq 0 ]]; then
        echo -e "\n${GREEN}${BOLD}  All sessions complete!${RESET}"
        touch "${SESSION_DIR}/ok"
        echo -e "  Created: ${SESSION_DIR}/ok"
        exit 0
    elif [[ $RC -eq 42 ]]; then
        echo -e "\n${YELLOW}  Some sessions still incomplete, retrying...${RESET}"
        ROUND=$((ROUND + 1))
        RC=0
        sleep 5
    else
        echo -e "\n${RED}  Unexpected error (exit code $RC), aborting${RESET}"
        exit $RC
    fi
done

echo -e "\n${RED}  Reached max rounds ($MAX_ROUNDS), some sessions may still be incomplete${RESET}"
exit 1
