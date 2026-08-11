#!/usr/bin/env python3
"""Run dialogue only (no evaluation).

Saves session JSONs that can later be evaluated with run_evaluation.py.

Usage:
    # Single run from a JSON file
    python3 -m dlawbench.run_dialogue \
        --case data/case_backgrounds/overtime_wage_dispute.json \
        --persona P_CD \
        --lawyer-model gpt-5.2-2025-12-11

    # Full matrix from JSONL files, one case per line
    python3 -m dlawbench.run_dialogue \
        --cases "data/case_backgrounds/*.jsonl" \
        --personas "P_WD,P_WS,P_CS,P_CD" \
        --lawyer-models "gpt-5.2-2025-12-11,claude-opus-4-6"
"""
import argparse
import glob
import json
import os
import sys
import time
from itertools import zip_longest
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .api_utils import get_model
from .api_utils import LOCALHOST_CONCURRENT, LOCALHOST_WORKERS, LOCALHOST_MODELS
from .api_utils import _use_localhost
from .agent_environment import AgentEnvironment
from .client_npc import load_persona
from .case_utils import (
    case_name as _case_name,
    expand_case_inputs,
    load_case,
    split_jsonl_ref,
)
from .paths import CONFIG_DIR, ENV_FILE, RUNS_DIR as DEFAULT_RUNS_DIR
from .paths import project_path

# Parallel dialogue tasks across cases/personas (cloud API default; override with --workers).
CLOUD_DEFAULT_WORKERS = 3


def _is_localhost_model(model_name):
    """Check if a model is in the LOCALHOST_MODELS list (case-insensitive prefix match)."""
    lower = (model_name or "").strip().lower()
    return any(lower.startswith(e.lower()) for e in LOCALHOST_MODELS)


def _build_task_list(case_paths, lawyer_models, persona_keys):
    """One task = (case_path, case_name, lawyer_model, persona_key).

    With multiple cases, **round-robin across cases** so parallel workers are spread
    across different cases (otherwise the queue is depth-first: all personas of case 1
    before case 2 starts — looks like «cases not parallel»).
    """
    tasks_by_case = []
    for case_path in case_paths:
        case_name = _case_name(case_path)
        row = []
        for lawyer_model in lawyer_models:
            for persona_key in persona_keys:
                row.append((case_path, case_name, lawyer_model, persona_key))
        tasks_by_case.append(row)

    if len(tasks_by_case) <= 1:
        return tasks_by_case[0] if tasks_by_case else []

    tasks = []
    for batch in zip_longest(*tasks_by_case, fillvalue=None):
        for item in batch:
            if item is not None:
                tasks.append(item)
    return tasks

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
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


def _find_reusable_session_path(out_root, case_name, persona_key, lawyer_model):
    """Under out_root (recursive), find newest session_* for this case/persona/lawyer prefix.

    Reuse only when JSON loads, ``lawyer_model`` matches, and dialogue finished with
    ``memo_submitted`` or ``max_turns_reached`` (skip ``api_error`` / incomplete so they can retry).
    """
    want_lm = (lawyer_model or "").strip().lower()
    if not want_lm or not os.path.isdir(out_root):
        return None
    prefix = (lawyer_model or "").strip().split("-")[0]
    stem = f"session_{case_name}_{persona_key}_{prefix}_"
    best_path = None
    best_mtime = -1.0
    for dirpath, _dirs, filenames in os.walk(out_root):
        for fn in filenames:
            if not fn.endswith(".json") or not fn.startswith(stem):
                continue
            path = os.path.join(dirpath, fn)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime <= best_mtime:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if (data.get("lawyer_model") or "").strip().lower() != want_lm:
                continue
            if data.get("status") not in ("memo_submitted", "max_turns_reached"):
                continue
            best_path, best_mtime = path, mtime
    return best_path


def run_single(case_path, persona_key, lawyer_model, client_model,
               max_turns=10, client_kwargs=None, lawyer_kwargs=None):
    """Run one consultation (dialogue only, no evaluation)."""
    case = load_case(case_path)
    persona = load_persona(persona_key)

    sys.stderr.write(f"  Running: {lawyer_model} × {persona_key}\n")

    env = AgentEnvironment(
        case=case,
        persona=persona,
        lawyer_model=lawyer_model,
        client_model=client_model,
        max_turns=max_turns,
        client_kwargs=client_kwargs,
        lawyer_kwargs=lawyer_kwargs,
    )
    session = env.run()

    # Record which case source was used (for run_evaluation.py auto-lookup)
    source_path, line_no, _case_id = split_jsonl_ref(case_path)
    session["case_file"] = os.path.basename(source_path)
    if line_no is not None:
        session["case_line"] = line_no

    return session


def main():
    load_env_file()

    parser = argparse.ArgumentParser(description="Run dialogue only (no evaluation)")
    parser.add_argument("--case", help="Single case JSON path or JSONL path")
    parser.add_argument("--cases", help="Glob pattern for JSON/JSONL cases")
    parser.add_argument("--persona", help="Single persona key (e.g. P_CD)")
    parser.add_argument("--personas", default="P_WD,P_WS,P_CS,P_CD",
                        help="Comma-separated persona keys")
    parser.add_argument("--lawyer-model", help="Single lawyer model")
    parser.add_argument("--lawyer-models", help="Comma-separated lawyer models")
    parser.add_argument("--client-model", default=None)
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--no-client-thinking", action="store_true",
                        help="Disable thinking/reasoning for client model")
    parser.add_argument("--no-lawyer-thinking", action="store_true",
                        help="Disable thinking/reasoning for lawyer model")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--resume", nargs="?", const="auto", default=None, metavar="SUMMARY",
                        help="Resume from previous summary. Pass a file path, or omit value to auto-find latest in output-dir")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip tasks when a reusable session_*.json already exists under --output-dir "
            "(recursive): same case/persona/lawyer_model, status memo_submitted or max_turns_reached. "
            "Useful when dialogue_summary was never written but session files were saved."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Parallel dialogue tasks (different cases/personas). "
            f"Cloud default: {CLOUD_DEFAULT_WORKERS}; localhost concurrent default: api_utils.LOCALHOST_WORKERS. "
            "Use 1 for strictly sequential."
        ),
    )
    args = parser.parse_args()

    params_dir = str(CONFIG_DIR)

    # Resolve case paths
    if args.case:
        raw_case_paths = [str(project_path(args.case))]
    elif args.cases:
        pattern = str(project_path(args.cases))
        raw_case_paths = sorted(glob.glob(pattern))
    else:
        parser.error("Must specify --case or --cases")
    case_paths = expand_case_inputs(raw_case_paths)
    if args.case and len(case_paths) > 1:
        case_paths = case_paths[:1]

    # Resolve models
    if args.lawyer_model:
        lawyer_models = [args.lawyer_model]
    elif args.lawyer_models:
        lawyer_models = [m.strip() for m in args.lawyer_models.split(",")]
    else:
        lawyer_models = [get_model("lawyer", params_dir)]

    client_model = args.client_model or get_model("client", params_dir)

    client_kwargs = {}
    if args.no_client_thinking:
        client_kwargs["reasoning_effort"] = "low"
    lawyer_kwargs = {}
    if args.no_lawyer_thinking:
        lawyer_kwargs["reasoning_effort"] = "low"

    # Resolve personas
    if args.persona:
        persona_keys = [args.persona]
    else:
        persona_keys = [p.strip() for p in args.personas.split(",")]

    # Output directory
    output_dir = str(project_path(args.output_dir)) if args.output_dir else str(DEFAULT_RUNS_DIR)
    os.makedirs(output_dir, exist_ok=True)
    out_root = output_dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Resume: load previous summary and identify successful entries ────
    prev_ok = {}       # (case, persona, model) -> result dict
    if args.resume:
        resume_path = None
        resume_arg_path = None if args.resume == "auto" else project_path(args.resume)
        if args.resume == "auto" or os.path.isdir(resume_arg_path):
            search_dir = str(resume_arg_path) if resume_arg_path else output_dir
            # Scan all summaries (newest last), pick the latest one containing target model
            all_summaries = sorted(glob.glob(os.path.join(search_dir, "dialogue_summary_*.json")))
            target_models = set(m.lower() for m in lawyer_models)
            for sf in reversed(all_summaries):
                try:
                    with open(sf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    models_in_file = set(r.get("model", "").lower() for r in data.get("results", []))
                    if target_models & models_in_file:
                        resume_path = sf
                        sys.stderr.write(f"  {_CYAN}Auto-detected summary: {os.path.basename(sf)}{_RESET}\n")
                        break
                except Exception:
                    continue
            if not resume_path and all_summaries:
                sys.stderr.write(f"  {_YELLOW}Warning: no summary found for model {lawyer_models}, starting fresh{_RESET}\n")
            elif not all_summaries:
                sys.stderr.write(f"  {_YELLOW}Warning: no dialogue_summary found in {search_dir}, starting fresh{_RESET}\n")
        else:
            resume_path = str(project_path(args.resume))

        if resume_path:
            missing_count = 0
            with open(resume_path, "r", encoding="utf-8") as f:
                prev_summary = json.load(f)
            for r in prev_summary.get("results", []):
                if "error" not in r and "output" in r and r.get("status") not in ("api_error", "error"):
                    out_path = r["output"]
                    if not os.path.isabs(out_path):
                        out_path = str(project_path(out_path))
                    if not os.path.isfile(out_path):
                        missing_count += 1
                        continue
                    key = (r["case"], r["persona"], r["model"])
                    prev_ok[key] = r
            msg = f"\n  {_CYAN}Resume:{_RESET} loaded {len(prev_ok)} successful entries from {os.path.basename(resume_path)}"
            if missing_count:
                msg += f" ({_YELLOW}{missing_count} skipped — file missing{_RESET})"
            sys.stderr.write(msg + "\n")

    total = len(case_paths) * len(persona_keys) * len(lawyer_models)
    skip_count = 0
    sys.stderr.write(f"\n{_BOLD}{'═'*70}{_RESET}\n")
    sys.stderr.write(f"{_BOLD}  DIALOGUE ONLY (no evaluation){_RESET}\n")
    sys.stderr.write(f"  {len(case_paths)} cases × {len(persona_keys)} personas × {len(lawyer_models)} models = {total} runs\n")
    if prev_ok:
        sys.stderr.write(f"  {_CYAN}Skipping up to {len(prev_ok)} already-successful entries{_RESET}\n")
    if args.skip_existing:
        sys.stderr.write(
            f"  {_CYAN}--skip-existing:{_RESET} skip tasks when a finished session JSON "
            f"already exists under {out_root} (recursive)\n"
        )
    sys.stderr.write(f"{_BOLD}{'═'*70}{_RESET}\n")

    all_results = []
    run_idx = 0
    t_global = time.time()

    # ── Build task list ────────────────────────────────────────────────
    tasks = _build_task_list(case_paths, lawyer_models, persona_keys)

    # ── Check if localhost concurrency is enabled ─────────────────────
    use_concurrent = (
        LOCALHOST_CONCURRENT
        and _use_localhost()
        and all(_is_localhost_model(m) for m in lawyer_models)
    )
    if args.workers is not None:
        workers = max(1, int(args.workers))
    elif use_concurrent:
        workers = LOCALHOST_WORKERS
    else:
        workers = CLOUD_DEFAULT_WORKERS

    if workers > 1:
        label = "Localhost concurrent" if use_concurrent else "Parallel (cloud)"
        sys.stderr.write(f"  {_CYAN}{label}: {workers} workers{_RESET}\n")
        if len(case_paths) > 1:
            sys.stderr.write(
                f"  {_CYAN}Schedule: round-robin across {len(case_paths)} cases "
                f"(workers spread across cases){_RESET}\n"
            )
        sys.stderr.write(f"{_BOLD}{'═'*70}{_RESET}\n")

    def _run_one(idx, case_path, case_name, lawyer_model, persona_key):
        """Run a single dialogue and return (idx, result_dict)."""
        prev_key = (case_name, persona_key, lawyer_model)
        if prev_key in prev_ok:
            return idx, "skip", prev_ok[prev_key]

        if args.skip_existing:
            reuse = _find_reusable_session_path(out_root, case_name, persona_key, lawyer_model)
            if reuse:
                try:
                    with open(reuse, "r", encoding="utf-8") as f:
                        _sess = json.load(f)
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    _sess = {}
                return idx, "skip", {
                    "case": case_name,
                    "persona": persona_key,
                    "model": lawyer_model,
                    "output": reuse,
                    "turns": _sess.get("turns_used"),
                    "status": _sess.get("status"),
                }

        out_file = os.path.join(
            output_dir,
            f"session_{case_name}_{persona_key}_{lawyer_model.split('-')[0]}_{timestamp}.json"
        )
        try:
            result = run_single(
                case_path, persona_key, lawyer_model,
                client_model, args.max_turns,
                client_kwargs=client_kwargs,
                lawyer_kwargs=lawyer_kwargs,
            )
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            return idx, "ok", {
                "case": case_name, "persona": persona_key,
                "model": lawyer_model, "output": out_file,
                "turns": result["turns_used"],
                "status": result["status"],
                "_session": result,
            }
        except Exception as exc:
            return idx, "error", {
                "case": case_name, "persona": persona_key,
                "model": lawyer_model, "error": str(exc),
            }

    # ── Execute (sequential or concurrent) ────────────────────────────
    results_by_idx = {}

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for i, (cp, cn, lm, pk) in enumerate(tasks):
                fut = pool.submit(_run_one, i, cp, cn, lm, pk)
                futures[fut] = (i, cn, lm, pk)
            for fut in as_completed(futures):
                idx, status, data = fut.result()
                results_by_idx[idx] = (status, data)
                cn, lm, pk = futures[fut][1], futures[fut][2], futures[fut][3]
                if status == "skip":
                    skip_count += 1
                    sys.stderr.write(f"  [{idx+1}/{total}] {_CYAN}SKIP{_RESET} "
                                     f"{lm} × {pk} × {cn} (already OK)\n")
                elif status == "ok":
                    sess = data.pop("_session", {})
                    sys.stderr.write(f"  [{idx+1}/{total}] {_GREEN}✓{_RESET} "
                                     f"{lm} × {pk} × {cn} — "
                                     f"{sess.get('turns_used','')} turns, "
                                     f"status={sess.get('status','')}, "
                                     f"memo={'yes' if sess.get('memo') else 'no'}\n")
                else:
                    sys.stderr.write(f"  [{idx+1}/{total}] {_RED}ERROR{_RESET} "
                                     f"{lm} × {pk} × {cn} — {data.get('error','')}\n")
    else:
        for i, (cp, cn, lm, pk) in enumerate(tasks):
            sys.stderr.write(f"\n  [{i+1}/{total}] {_GREEN}{lm}{_RESET} × "
                             f"{_CYAN}{pk}{_RESET} × {_YELLOW}{cn}{_RESET}\n")
            idx, status, data = _run_one(i, cp, cn, lm, pk)
            results_by_idx[idx] = (status, data)
            if status == "skip":
                skip_count += 1
            elif status == "ok":
                sess = data.pop("_session", {})
                sys.stderr.write(f"  {_GREEN}✓{_RESET} {sess.get('turns_used','')} turns, "
                                 f"status={sess.get('status','')}, "
                                 f"memo={'yes' if sess.get('memo') else 'no'}\n")
            else:
                sys.stderr.write(f"  {_RED}ERROR: {data.get('error','')}{_RESET}\n")

    # Collect results in original order
    consumed_keys = set()
    for i in range(len(tasks)):
        status, data = results_by_idx[i]
        all_results.append(data)
        if status == "skip":
            consumed_keys.add((data["case"], data["persona"], data["model"]))

    # Carry forward prev_ok entries not covered by current task matrix
    if prev_ok:
        carry_count = 0
        for key, r in prev_ok.items():
            if key not in consumed_keys:
                all_results.append(r)
                carry_count += 1
        if carry_count:
            sys.stderr.write(f"  {_CYAN}Carried forward {carry_count} entries from previous summary{_RESET}\n")

    total_elapsed = time.time() - t_global
    summary_path = os.path.join(output_dir, f"dialogue_summary_{timestamp}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "mode": "dialogue_only",
            "total_elapsed_seconds": round(total_elapsed, 1),
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)

    sys.stderr.write(f"\n{_BOLD}{'═'*70}{_RESET}\n")
    ok_count = sum(1 for r in all_results if "error" not in r)
    err_count = len(all_results) - ok_count
    sys.stderr.write(f"  Dialogue complete in {total_elapsed:.0f}s\n")
    sys.stderr.write(f"  Results: {ok_count} OK, {err_count} errors"
                     f"{f', {skip_count} skipped (resumed)' if skip_count else ''}\n")
    sys.stderr.write(f"  Summary → {summary_path}\n")
    sys.stderr.write(
        f"  To evaluate: python3 -m dlawbench.run_evaluation "
        f"--sessions \"{output_dir}/session_*_{timestamp}.json\"\n"
    )
    sys.stderr.write(f"{_BOLD}{'═'*70}{_RESET}\n")


if __name__ == "__main__":
    main()
