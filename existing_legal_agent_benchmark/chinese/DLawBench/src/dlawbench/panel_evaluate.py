"""Panel of Judges evaluation with recusal mechanism.

Runs evaluate_session() once per judge, then aggregates scores.
- 3 judges: take median
- 2 judges (after recusal): take mean

Recusal: if the lawyer model and a judge model share the same provider
(via resolve_provider), that judge is excluded.

Config is read from config/benchmark_models.json.
"""
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api_utils import resolve_logical_provider
from .evaluate import evaluate_session
from .paths import CONFIG_DIR


def _load_panel_config(config_path=None):
    """Load judge_panel list from benchmark_models.json."""
    if config_path is None:
        config_path = CONFIG_DIR / "benchmark_models.json"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    infra = cfg.get("infrastructure", {})
    return infra.get("judge_panel", [])


def resolve_judge_panel(lawyer_model, config_path=None):
    """Determine which judges evaluate this lawyer, applying recusal.

    Returns:
        (active_judges, recused_judge_or_None)
    """
    panel = _load_panel_config(config_path)
    if not panel:
        raise ValueError("judge_panel is empty in benchmark_models.json")

    lawyer_provider = resolve_logical_provider(lawyer_model)

    recused = None
    active = []
    for judge in panel:
        judge_provider = resolve_logical_provider(judge)
        if judge_provider == lawyer_provider:
            recused = judge
        else:
            active.append(judge)

    # If no recusal happened (lawyer provider not in panel), use all judges
    if recused is None:
        active = list(panel)

    return active, recused


def _aggregate_scores(per_judge_evals):
    """Aggregate numeric scores across judges.

    3 judges -> median, 2 judges -> mean, 1 judge -> use directly.
    """
    score_keys = [
        "MES", "EAS",
        "discovery_rate", "accuracy_rate", "absolute_accuracy", "grounding_rate",
    ]

    aggregated = {}
    for key in score_keys:
        values = []
        for judge_eval in per_judge_evals.values():
            scores = judge_eval.get("scores", {})
            v = scores.get(key)
            if v is not None:
                values.append(v)

        if len(values) >= 3:
            aggregated[key] = round(statistics.median(values), 4)
        elif len(values) == 2:
            aggregated[key] = round(statistics.mean(values), 4)
        elif len(values) == 1:
            aggregated[key] = round(values[0], 4)
        else:
            aggregated[key] = None

    # FFS is rule-based (identical across judges), take from first
    first_eval = next(iter(per_judge_evals.values()), {})
    aggregated["FFS"] = first_eval.get("scores", {}).get("FFS")

    return aggregated


def evaluate_session_panel(session, case, config_path=None):
    """Evaluate a session using Panel of Judges.

    Returns a dict with the same top-level structure as evaluate_session(),
    plus panel metadata (per_judge scores, recusal info).
    """
    lawyer_model = session.get("lawyer_model", "")
    active_judges, recused = resolve_judge_panel(lawyer_model, config_path)

    def _call_judge(judge):
        try:
            return judge, evaluate_session(session, case, judge)
        except Exception as e:
            sys.stderr.write(f"    {judge}: ERROR {e}\n")
            return judge, {"error": str(e), "scores": {}}

    per_judge = {}
    with ThreadPoolExecutor(max_workers=len(active_judges)) as pool:
        futures = {pool.submit(_call_judge, j): j for j in active_judges}
        for fut in as_completed(futures):
            judge_name, result = fut.result()
            per_judge[judge_name] = result

    aggregated_scores = _aggregate_scores(per_judge)

    # Use structure from first successful judge for non-score fields
    first_eval = next(
        (ev for ev in per_judge.values() if "error" not in ev), {}
    )

    return {
        "case_id": case.get("case_id"),
        "panel_mode": True,
        "judges": active_judges,
        "recused": recused,
        "per_judge": per_judge,
        "process": first_eval.get("process"),
        "format": first_eval.get("format"),
        "scores": aggregated_scores,
    }
