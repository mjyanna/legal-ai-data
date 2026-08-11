#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Run dialogues
# ═══════════════════════════════════════════════════════════════════════
#
# Runs Lawyer Agent x Client NPC dialogues and saves session JSONs.
# Can be run in parallel with different models; each run produces
# uniquely named files (model prefix + timestamp in filename).
#
# Usage:
#   # Single model, all cases, all personas
#   ./scripts/run_dialogues.sh --lawyer-model gpt-5.2-2025-12-11
#
#   # Single case + persona (for testing)
#   ./scripts/run_dialogues.sh --lawyer-model gpt-5.2-2025-12-11 \
#       --case data/case_backgrounds/cases_us.jsonl --persona P_WD
#
#   # Multiple models (run in parallel in separate terminals)
#   ./scripts/run_dialogues.sh --lawyer-model gpt-5.2-2025-12-11 &
#   ./scripts/run_dialogues.sh --lawyer-model claude-opus-4-6 &
#   wait
#
# Output:
#   runs/session_{case}_{persona}_{model}_{timestamp}.json
#
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH_VALUE="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

BOLD="\033[1m"
GREEN="\033[32m"
RESET="\033[0m"

usage() {
    echo "Usage: $0 --lawyer-model MODEL [options]"
    echo ""
    echo "Required:"
    echo "  --lawyer-model MODEL   Model to benchmark as lawyer"
    echo ""
    echo "Optional:"
    echo "  --case PATH            Single JSON file or first record from a JSONL file"
    echo "  --cases GLOB           Glob pattern (default: data/case_backgrounds/*.jsonl)"
    echo "  --persona KEY          Single persona (e.g. P_WD)"
    echo "  --personas KEYS        Comma-separated (default: P_WD,P_WS,P_CS,P_CD)"
    echo "  --max-turns N          Max dialogue turns (default: 10)"
    echo "  --client-model MODEL   Client NPC model (default: from config)"
    echo "  --output-dir DIR       Output directory (default: runs/)"
    echo "  --case-dir DIR         Case backgrounds directory (default: data/case_backgrounds/)"
    echo "  --workers N            Parallel cases/personas (cloud default: 3; use 1 for serial)"
    echo "  --resume [SUMMARY]     Resume from previous summary (auto-find if omitted)"
    echo "  --skip-existing        Skip if a finished session_*.json already exists under output-dir"
    echo "  --no-client-thinking   Disable reasoning for client"
    echo "  --no-lawyer-thinking   Disable reasoning for lawyer"
    exit 1
}

# ── Parse args ────────────────────────────────────────────────────────

LAWYER_MODEL=""
CASE=""
CASES=""
PERSONA=""
PERSONAS=""
MAX_TURNS=""
CLIENT_MODEL=""
OUTPUT_DIR=""
CASE_DIR_ARG=""
WORKERS=""
RESUME=""
EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lawyer-model)  LAWYER_MODEL="$2"; shift 2 ;;
        --case)          CASE="$2"; shift 2 ;;
        --cases)         CASES="$2"; shift 2 ;;
        --persona)       PERSONA="$2"; shift 2 ;;
        --personas)      PERSONAS="$2"; shift 2 ;;
        --max-turns)     MAX_TURNS="$2"; shift 2 ;;
        --client-model)  CLIENT_MODEL="$2"; shift 2 ;;
        --output-dir)    OUTPUT_DIR="$2"; shift 2 ;;
        --case-dir)      CASE_DIR_ARG="$2"; shift 2 ;;
        --workers)        WORKERS="$2"; shift 2 ;;
        --resume)        
            if [[ $# -ge 2 && ! "$2" == --* ]]; then
                RESUME="$2"; shift 2
            else
                RESUME="auto"; shift
            fi
            ;;
        --skip-existing)      EXTRA_ARGS="$EXTRA_ARGS --skip-existing"; shift ;;
        --no-client-thinking) EXTRA_ARGS="$EXTRA_ARGS --no-client-thinking"; shift ;;
        --no-lawyer-thinking) EXTRA_ARGS="$EXTRA_ARGS --no-lawyer-thinking"; shift ;;
        -h|--help)       usage ;;
        *)               echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$LAWYER_MODEL" ]]; then
    echo "Error: --lawyer-model is required"
    usage
fi

# ── Build command ─────────────────────────────────────────────────────

CMD=(env "PYTHONPATH=$PYTHONPATH_VALUE" python3 -m dlawbench.run_dialogue --lawyer-model "$LAWYER_MODEL")

EFFECTIVE_CASE_DIR="${CASE_DIR_ARG:-data/case_backgrounds}"

if [[ -n "$CASE" ]]; then
    if [[ "$CASE" = /* || "$CASE" == *.json || "$CASE" == *.jsonl ]]; then
        CMD+=(--case "$CASE")
    else
        CMD+=(--case "${EFFECTIVE_CASE_DIR}/${CASE}.json")
    fi
elif [[ -n "$CASES" ]]; then
    CMD+=(--cases "$CASES")
else
    CMD+=(--cases "${EFFECTIVE_CASE_DIR}/*.jsonl")
fi

[[ -n "$PERSONA" ]]      && CMD+=(--persona "$PERSONA")
[[ -n "$PERSONAS" ]]     && CMD+=(--personas "$PERSONAS")
[[ -n "$MAX_TURNS" ]]    && CMD+=(--max-turns "$MAX_TURNS")
[[ -n "$CLIENT_MODEL" ]] && CMD+=(--client-model "$CLIENT_MODEL")
[[ -n "$OUTPUT_DIR" ]]   && CMD+=(--output-dir "$OUTPUT_DIR")
[[ -n "$WORKERS" ]]      && CMD+=(--workers "$WORKERS")
[[ -n "$RESUME" ]]       && CMD+=(--resume "$RESUME")
[[ -n "$EXTRA_ARGS" ]]   && CMD+=($EXTRA_ARGS)

# ── Run ───────────────────────────────────────────────────────────────

echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Running dialogues${RESET}"
echo -e "  Lawyer: ${GREEN}$LAWYER_MODEL${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo ""

"${CMD[@]}"
