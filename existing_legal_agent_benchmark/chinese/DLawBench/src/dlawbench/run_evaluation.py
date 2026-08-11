#!/usr/bin/env python3
"""Evaluate existing session files (no dialogue).

Reads session JSONs produced by run_dialogue.py or run_benchmark.py,
runs evaluation, and writes updated session files.

The case file is auto-detected from the session's case_id field.
Use --case to override if needed.

Usage:
    # Auto-detect case files from session metadata
    python3 -m dlawbench.run_evaluation --sessions "runs/session_*.json"

    # Specify case explicitly (all sessions must be from this case)
    python3 -m dlawbench.run_evaluation --sessions "runs/session_*.json" \
        --case data/case_backgrounds/overtime_wage_dispute.json

    # Use a different eval model
    python3 -m dlawbench.run_evaluation --sessions "runs/session_*.json" \
        --eval-model claude-opus-4-6
"""
import argparse
import collections
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import zip_longest

from .api_utils import get_model
from .evaluate import evaluate_session
from .panel_evaluate import evaluate_session_panel, resolve_judge_panel
from .case_utils import normalize_case
from .paths import CASE_BACKGROUNDS_DIR, CONFIG_DIR, ENV_FILE, PROJECT_ROOT
from .paths import project_path

CASE_DIR = str(CASE_BACKGROUNDS_DIR)  # default, overridable via --case-dir
DEFAULT_EVAL_WORKERS = 3


def _memo_explicitly_null(session: dict) -> bool:
    """True when session JSON has ``\"memo\": null`` (key present, value null)."""
    return "memo" in session and session["memo"] is None


def _shell_round_accepts_null_memo(shell_retry_round: int) -> bool:
    """Retry rounds 2-10: ``memo: null`` is treated as done."""
    return shell_retry_round >= 2


def _eval_is_complete(evaluation: dict) -> bool:
    """Return True if the evaluation has no judge errors and no null scores."""
    # Check per-judge errors (panel mode)
    per_judge = evaluation.get("per_judge", {})
    for judge_name, jdata in per_judge.items():
        if isinstance(jdata, dict) and "error" in jdata:
            return False
    # Check final scores for None
    scores = evaluation.get("scores", {})
    if not scores:
        return False
    for key in ("MES", "EAS", "discovery_rate", "accuracy_rate",
                "absolute_accuracy", "grounding_rate", "FFS"):
        if key in scores and scores[key] is None:
            return False
    return True


def _metric_value(value):
    return 0.0 if value is None else float(value)


def _public_metric_scores(scores: dict) -> dict:
    fact_coverage = _metric_value(scores.get("discovery_rate"))
    inquiry = _metric_value(scores.get("EAS"))
    fact_resolution = _metric_value(scores.get("absolute_accuracy"))
    issue_resolution = _metric_value(scores.get("MES"))
    fidelity = _metric_value(scores.get("grounding_rate"))
    return {
        "fact_coverage": round(fact_coverage, 4),
        "inquiry": round(inquiry, 4),
        "elicitation": round((fact_coverage + inquiry) / 2, 4),
        "fact_resolution": round(fact_resolution, 4),
        "issue_resolution": round(issue_resolution, 4),
        "resolution": round((fact_resolution + issue_resolution) / 2, 4),
        "fidelity": round(fidelity, 4),
    }


def _format_public_metrics(scores: dict) -> str:
    public = _public_metric_scores(scores)
    return (
        f"Fact Coverage={public['fact_coverage']} "
        f"Inquiry={public['inquiry']} "
        f"Elicitation={public['elicitation']} "
        f"Fact Resolution={public['fact_resolution']} "
        f"Issue Resolution={public['issue_resolution']} "
        f"Resolution={public['resolution']} "
        f"Fidelity={public['fidelity']}"
    )

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"


def load_env_file():
    env_path = ENV_FILE
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value


def _load_case_jsonl(path, case_id=None, line_no=None):
    """Load one case from a JSONL file."""
    with open(path, "r", encoding="utf-8") as f:
        for current_line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            if line_no is not None and current_line_no != line_no:
                continue
            data = normalize_case(json.loads(line))
            if case_id is None or data.get("case_id") == case_id:
                return data
    return None


def _load_case_path(path, case_id=None, line_no=None):
    """Load a case from JSON or JSONL."""
    if path.endswith(".jsonl"):
        return _load_case_jsonl(path, case_id=case_id, line_no=line_no)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data = normalize_case(data)
    if case_id and data.get("case_id") != case_id:
        return None
    return data


def _iter_case_paths():
    """Yield JSON and JSONL case files from CASE_DIR."""
    if not os.path.isdir(CASE_DIR):
        return
    for fname in os.listdir(CASE_DIR):
        if fname.endswith(".json") or fname.endswith(".jsonl"):
            yield os.path.join(CASE_DIR, fname)


def _find_case(session):
    """Find and load the case object for a session.

    Tries in order:
    1. session["case_file"] and optional session["case_line"] (set by run_dialogue.py)
    2. session["case_id"] + ".json" in data/case_backgrounds/
    3. Scan all case JSON/JSONL files for matching case_id
    """
    case_id = session.get("case_id", "")

    # Try case_file field first
    case_file = session.get("case_file", "")
    if case_file:
        path = os.path.join(CASE_DIR, case_file)
        if os.path.isfile(path):
            case = _load_case_path(path, case_id=case_id, line_no=session.get("case_line"))
            if case:
                return case

    # Try case_id as filename
    if case_id:
        path = os.path.join(CASE_DIR, f"{case_id}.json")
        if os.path.isfile(path):
            case = _load_case_path(path, case_id=case_id)
            if case:
                return case

    # Fallback: scan all case files for matching case_id
    if case_id:
        for fpath in _iter_case_paths() or []:
            try:
                case = _load_case_path(fpath, case_id=case_id)
                if case:
                    return case
            except (json.JSONDecodeError, OSError):
                continue

    return None


def _session_case_key(path: str) -> str:
    """Best-effort case key for round-robin scheduling across cases."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = json.load(f)
        return (s.get("case_id") or s.get("case_file") or os.path.basename(path)).strip()
    except Exception:
        return os.path.basename(path)


def _round_robin_by_case(session_files):
    """Interleave files by case so workers spread across different cases first."""
    buckets = collections.OrderedDict()
    for p in session_files:
        key = _session_case_key(p)
        buckets.setdefault(key, []).append(p)
    groups = list(buckets.values())
    if len(groups) <= 1:
        return session_files
    out = []
    for batch in zip_longest(*groups, fillvalue=None):
        for item in batch:
            if item is not None:
                out.append(item)
    return out


def main():
    load_env_file()

    parser = argparse.ArgumentParser(description="Evaluate existing session files")
    parser.add_argument("--sessions", required=True,
                        help="Glob pattern for session JSON files")
    parser.add_argument("--case", default=None,
                        help="Case background JSON (auto-detected if omitted)")
    parser.add_argument("--eval-model", default=None,
                        help="Evaluation model (default: from config/models.json)")
    parser.add_argument("--panel", action="store_true",
                        help="Use Panel of Judges evaluation (3 judges with recusal)")
    parser.add_argument("--case-dir", default=None,
                        help="Directory containing case background JSONs (default: data/case_backgrounds/)")
    parser.add_argument("--output-suffix", default=None,
                        help="If set, write to new files with this suffix instead of in-place")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Only re-evaluate sessions that have judge errors or null scores")
    parser.add_argument(
        "--shell-retry-round",
        type=int,
        default=1,
        metavar="N",
        help="Set by evaluate_sessions.sh outer retry loop (1=first pass). When N>=2, sessions "
        "with explicit JSON null memo (\\\"memo\\\": null) are skipped and not counted incomplete.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_EVAL_WORKERS,
        help=f"Parallel session evaluations (default: {DEFAULT_EVAL_WORKERS})",
    )
    args = parser.parse_args()

    # Override CASE_DIR if --case-dir is provided
    global CASE_DIR
    if args.case_dir:
        CASE_DIR = str(project_path(args.case_dir))

    eval_model = args.eval_model or get_model("eval", str(CONFIG_DIR))

    # Load explicit case if provided
    explicit_case = None
    if args.case:
        case_path = str(project_path(args.case))
        explicit_case = _load_case_path(case_path)
        if not explicit_case:
            raise ValueError(f"Could not load case from {case_path}")

    session_files = sorted(glob.glob(args.sessions))
    if not session_files:
        pattern = str(project_path(args.sessions))
        session_files = sorted(glob.glob(pattern))
    if not session_files:
        print(f"No files matching: {args.sessions}")
        sys.exit(1)

    # Pre-scan for --retry-errors to show stats upfront
    need_eval_count = len(session_files)
    pre_skip_count = 0
    if args.retry_errors:
        for p in session_files:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    s = json.load(f)
                ev = s.get("evaluation", {})
                if ev and _eval_is_complete(ev):
                    pre_skip_count += 1
                elif _shell_round_accepts_null_memo(args.shell_retry_round) and _memo_explicitly_null(s):
                    pre_skip_count += 1
            except Exception:
                pass
        need_eval_count = len(session_files) - pre_skip_count

    sys.stderr.write(f"\n{_BOLD}{'═'*70}{_RESET}\n")
    sys.stderr.write(f"{_BOLD}  EVALUATION ONLY{_RESET}\n")
    if args.panel:
        sys.stderr.write(f"  {len(session_files)} sessions, mode: Panel of Judges\n")
    else:
        sys.stderr.write(f"  {len(session_files)} sessions, eval model: {eval_model}\n")
    if args.retry_errors:
        sys.stderr.write(f"  {_GREEN}{pre_skip_count} already OK (skip){_RESET}, "
                         f"{_YELLOW}{need_eval_count} to re-evaluate{_RESET}\n")
    sys.stderr.write(f"  workers={max(1, int(args.workers))} (round-robin by case)\n")
    sys.stderr.write(f"{_BOLD}{'═'*70}{_RESET}\n\n")

    t_global = time.time()
    all_scores = []
    skip_count = 0
    workers = max(1, int(args.workers))
    scheduled_files = _round_robin_by_case(session_files)

    def _eval_one(path):
        name = os.path.basename(path)

        try:
            with open(path, "r", encoding="utf-8") as f:
                session = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {
                "log": f"  {name} ... {_RED}SKIP (corrupted: {e}){_RESET}\n",
                "score": None,
                "skip_ok": False,
            }

        # --retry-errors: skip sessions whose evaluation is already complete
        if args.retry_errors:
            existing_eval = session.get("evaluation", {})
            if existing_eval and _eval_is_complete(existing_eval):
                scores = existing_eval.get("scores", {})
                return {
                    "log": None,
                    "score": {
                        "file": name,
                        "case_id": session.get("case_id"),
                        "persona": session.get("persona"),
                        "model": session.get("lawyer_model"),
                        **_public_metric_scores(scores),
                    },
                    "skip_ok": True,
                }
            if _shell_round_accepts_null_memo(args.shell_retry_round) and _memo_explicitly_null(session):
                return {
                    "log": (
                        f"  {name} ... {_YELLOW}skip (shell round≥2, memo is null → treated done){_RESET}\n"
                    ),
                    "score": None,
                    "skip_ok": True,
                }

        log_prefix = f"  {name} ... "

        # Resolve case
        if explicit_case:
            case = explicit_case
        else:
            case = _find_case(session)
            if not case:
                return {
                    "log": (
                        log_prefix
                        + f"{_RED}SKIP (case not found for case_id={session.get('case_id')}){_RESET}\n"
                    ),
                    "score": None,
                    "skip_ok": False,
                }

        panel_msg = ""
        if args.panel:
            active, recused = resolve_judge_panel(session.get("lawyer_model", ""))
            recusal_info = f" (recused: {recused})" if recused else ""
            panel_msg = f"\n    Panel: {', '.join(active)}{recusal_info}\n    "
            evaluation = evaluate_session_panel(session, case)
        else:
            evaluation = evaluate_session(session, case, eval_model)
        session["evaluation"] = evaluation

        # Write output
        if args.output_suffix:
            base, ext = os.path.splitext(path)
            out_path = f"{base}_{args.output_suffix}{ext}"
        else:
            out_path = path
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        scores = evaluation.get("scores", {})
        ok_log = (
            log_prefix
            + (panel_msg if panel_msg else "")
            + f"{_GREEN}✓{_RESET} {_format_public_metrics(scores)}\n"
        )
        return {
            "log": ok_log,
            "score": {
                "file": name,
                "case_id": session.get("case_id"),
                "persona": session.get("persona"),
                "model": session.get("lawyer_model"),
                **_public_metric_scores(scores),
            },
            "skip_ok": False,
        }

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_eval_one, p) for p in scheduled_files]
            for fut in as_completed(futures):
                item = fut.result()
                if item["skip_ok"]:
                    skip_count += 1
                if item["score"] is not None:
                    all_scores.append(item["score"])
                if item["log"]:
                    sys.stderr.write(item["log"])
    else:
        for path in scheduled_files:
            item = _eval_one(path)
            if item["skip_ok"]:
                skip_count += 1
            if item["score"] is not None:
                all_scores.append(item["score"])
            if item["log"]:
                sys.stderr.write(item["log"])

    total_elapsed = time.time() - t_global

    # Write evaluation summary
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_dir = os.path.dirname(session_files[0]) if session_files else str(PROJECT_ROOT)
    summary_path = os.path.join(summary_dir, f"eval_summary_{timestamp}.json")
    summary_meta = {
        "timestamp": datetime.now().isoformat(),
        "total_elapsed_seconds": round(total_elapsed, 1),
    }
    if args.panel:
        summary_meta["eval_mode"] = "panel"
    else:
        summary_meta["eval_model"] = eval_model
    summary_meta["results"] = all_scores
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_meta, f, ensure_ascii=False, indent=2)

    sys.stderr.write(f"\n{_BOLD}{'═'*70}{_RESET}\n")
    sys.stderr.write(f"  Evaluation complete in {total_elapsed:.0f}s\n")
    if args.retry_errors:
        sys.stderr.write(f"  {skip_count} skipped (already OK), "
                         f"{len(all_scores) - skip_count} re-evaluated\n")
    sys.stderr.write(f"  {len(all_scores)} sessions total\n")
    sys.stderr.write(f"  Summary \u2192 {summary_path}\n")
    
    # Post-check: count sessions still incomplete
    incomplete = 0
    for path in session_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                s = json.load(f)
            ev = s.get("evaluation", {})
            if not ev or not _eval_is_complete(ev):
                if _shell_round_accepts_null_memo(args.shell_retry_round) and _memo_explicitly_null(s):
                    continue
                incomplete += 1
        except Exception:
            incomplete += 1
    if incomplete:
        sys.stderr.write(f"  {_YELLOW}{incomplete} sessions still incomplete{_RESET}\n")
    else:
        sys.stderr.write(f"  {_GREEN}All sessions complete{_RESET}\n")
    sys.stderr.write(f"{_BOLD}{'═'*70}{_RESET}\n")
    
    # Exit 42 if incomplete, 0 if all done
    sys.exit(42 if incomplete else 0)


if __name__ == "__main__":
    main()
