#!/usr/bin/env python3
"""Report DLawBench public metrics from evaluated session JSON files."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METRIC_KEYS = [
    "fact_coverage",
    "inquiry",
    "elicitation",
    "fact_resolution",
    "issue_resolution",
    "resolution",
    "fidelity",
]
BASE_METRIC_KEYS = [
    "fact_coverage",
    "inquiry",
    "fact_resolution",
    "issue_resolution",
    "fidelity",
]
DISPLAY_NAMES = {
    "fact_coverage": "Fact Coverage",
    "inquiry": "Inquiry",
    "elicitation": "Elicitation",
    "fact_resolution": "Fact Resolution",
    "issue_resolution": "Issue Resolution",
    "resolution": "Resolution",
    "fidelity": "Fidelity",
}


def project_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def expand_session_patterns(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            matches = glob.glob(str(project_path(pattern)), recursive=True)
        if not matches and Path(pattern).is_file():
            matches = [pattern]
        files.extend(Path(match) for match in matches)

    unique: dict[str, Path] = {}
    for path in files:
        unique[str(path.resolve())] = path
    return [unique[key] for key in sorted(unique)]


def load_case_jurisdiction_index(case_dir: Path) -> tuple[dict[tuple[str, int], str], dict[str, set[str]]]:
    by_file_line: dict[tuple[str, int], str] = {}
    by_case_id: dict[str, set[str]] = {}

    if not case_dir.is_dir():
        return by_file_line, by_case_id

    for path in sorted(case_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                jurisdiction = record.get("jurisdiction")
                case_id = record.get("case_id") or record.get("id")
                if jurisdiction:
                    by_file_line[(path.name, line_no)] = jurisdiction
                if jurisdiction and case_id:
                    by_case_id.setdefault(case_id, set()).add(jurisdiction)

    for path in sorted(case_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
        jurisdiction = record.get("jurisdiction")
        case_id = record.get("case_id") or record.get("id")
        if jurisdiction and case_id:
            by_case_id.setdefault(case_id, set()).add(jurisdiction)

    return by_file_line, by_case_id


def resolve_jurisdiction(
    session: dict,
    by_file_line: dict[tuple[str, int], str],
    by_case_id: dict[str, set[str]],
) -> str:
    if session.get("jurisdiction"):
        return str(session["jurisdiction"])

    case_file = session.get("case_file")
    case_line = session.get("case_line")
    if case_file and case_line:
        jurisdiction = by_file_line.get((Path(str(case_file)).name, int(case_line)))
        if jurisdiction:
            return jurisdiction

    case_id = session.get("case_id")
    if case_id:
        jurisdictions = by_case_id.get(str(case_id), set())
        if len(jurisdictions) == 1:
            return next(iter(jurisdictions))

    raise ValueError(
        f"Could not resolve jurisdiction for session case_id={session.get('case_id')!r}. "
        "Pass --case-dir pointing to the release case backgrounds."
    )


def score_value(scores: dict, key: str, session_path: Path) -> float:
    if key not in scores:
        raise ValueError(f"{session_path}: evaluation is missing a required metric input")
    value = scores.get(key)
    if value is None:
        return 0.0
    return float(value)


def session_metrics(path: Path, session: dict, jurisdiction: str) -> dict:
    evaluation = session.get("evaluation")
    if not isinstance(evaluation, dict) or not isinstance(evaluation.get("scores"), dict):
        raise ValueError(f"{path}: no evaluation scores found; run evaluation before metrics.py")

    scores = evaluation["scores"]
    fact_coverage = score_value(scores, "discovery_rate", path)
    inquiry = score_value(scores, "EAS", path)
    fact_resolution = score_value(scores, "absolute_accuracy", path)
    issue_resolution = score_value(scores, "MES", path)
    fidelity = score_value(scores, "grounding_rate", path)

    return {
        "model": session.get("lawyer_model") or session.get("model") or "unknown_model",
        "jurisdiction": jurisdiction,
        "session_file": path.name,
        "fact_coverage": fact_coverage,
        "inquiry": inquiry,
        "elicitation": (fact_coverage + inquiry) / 2,
        "fact_resolution": fact_resolution,
        "issue_resolution": issue_resolution,
        "resolution": (fact_resolution + issue_resolution) / 2,
        "fidelity": fidelity,
    }


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def aggregate_group(rows: list[dict]) -> dict:
    result = {key: mean([row[key] for row in rows]) for key in BASE_METRIC_KEYS}
    result["elicitation"] = (result["fact_coverage"] + result["inquiry"]) / 2
    result["resolution"] = (result["fact_resolution"] + result["issue_resolution"]) / 2
    return result


def build_summary_rows(session_rows: list[dict], by_jurisdiction: bool) -> list[dict]:
    model_jurisdiction_groups: dict[tuple[str, str], list[dict]] = {}
    for row in session_rows:
        key = (str(row["model"]), str(row["jurisdiction"]))
        model_jurisdiction_groups.setdefault(key, []).append(row)

    jurisdiction_rows: list[dict] = []
    for (model, jurisdiction), rows in sorted(model_jurisdiction_groups.items()):
        metrics = aggregate_group(rows)
        jurisdiction_rows.append({
            "model": model,
            "jurisdiction": jurisdiction,
            "jurisdictions": jurisdiction,
            "sessions": len(rows),
            **metrics,
        })

    if by_jurisdiction:
        return sorted(jurisdiction_rows, key=lambda row: (-row["resolution"], row["model"], row["jurisdiction"]))

    by_model: dict[str, list[dict]] = {}
    for row in jurisdiction_rows:
        by_model.setdefault(row["model"], []).append(row)

    summary_rows: list[dict] = []
    for model, rows in sorted(by_model.items()):
        metrics = {key: mean([row[key] for row in rows]) for key in BASE_METRIC_KEYS}
        metrics["elicitation"] = (metrics["fact_coverage"] + metrics["inquiry"]) / 2
        metrics["resolution"] = (metrics["fact_resolution"] + metrics["issue_resolution"]) / 2
        summary_rows.append({
            "model": model,
            "jurisdictions": ",".join(sorted(row["jurisdiction"] for row in rows)),
            "sessions": sum(int(row["sessions"]) for row in rows),
            **metrics,
        })

    return sorted(summary_rows, key=lambda row: (-row["resolution"], row["model"]))


def rounded_row(row: dict, precision: int) -> dict:
    out = dict(row)
    for key in METRIC_KEYS:
        out[key] = round(float(out[key]), precision)
    return out


def markdown_table(rows: list[dict], precision: int, by_jurisdiction: bool) -> str:
    label_columns = ["model"]
    if by_jurisdiction:
        label_columns.append("jurisdiction")
    else:
        label_columns.append("jurisdictions")
    label_columns.append("sessions")

    headers = {
        "model": "Model",
        "jurisdiction": "Jurisdiction",
        "jurisdictions": "Jurisdictions",
        "sessions": "Sessions",
        **DISPLAY_NAMES,
    }
    columns = label_columns + METRIC_KEYS

    lines = [
        "| " + " | ".join(headers[col] for col in columns) + " |",
        "| " + " | ".join("---" if col in label_columns else "---:" for col in columns) + " |",
    ]
    for row in rows:
        values = []
        for col in columns:
            if col in METRIC_KEYS:
                values.append(f"{float(row[col]):.{precision}f}")
            else:
                values.append(str(row[col]))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_csv(rows: list[dict], handle, precision: int, by_jurisdiction: bool) -> None:
    columns = ["model"]
    if by_jurisdiction:
        columns.append("jurisdiction")
    else:
        columns.append("jurisdictions")
    columns += ["sessions"] + METRIC_KEYS

    fieldnames = []
    for col in columns:
        if col == "model":
            fieldnames.append("Model")
        elif col == "jurisdiction":
            fieldnames.append("Jurisdiction")
        elif col == "jurisdictions":
            fieldnames.append("Jurisdictions")
        elif col == "sessions":
            fieldnames.append("Sessions")
        else:
            fieldnames.append(DISPLAY_NAMES[col])

    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        rounded = rounded_row(row, precision)
        writer.writerow({
            fieldnames[index]: rounded[col]
            for index, col in enumerate(columns)
        })


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize evaluated DLawBench sessions with the public "
            "Consultation Quality metrics."
        )
    )
    parser.add_argument(
        "--sessions",
        nargs="+",
        required=True,
        help='Session JSON file or glob, for example "runs/demo/session_*.json".',
    )
    parser.add_argument(
        "--case-dir",
        default="data/case_backgrounds",
        help="Case background directory used to resolve jurisdictions.",
    )
    parser.add_argument(
        "--by-jurisdiction",
        action="store_true",
        help="Report model metrics separately for each jurisdiction.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "csv", "json"],
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output file. Defaults to stdout.",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=4,
        help="Decimal places for reported metric values.",
    )
    args = parser.parse_args()

    session_files = expand_session_patterns(args.sessions)
    if not session_files:
        raise SystemExit("No session files matched.")

    case_dir = project_path(args.case_dir)
    by_file_line, by_case_id = load_case_jurisdiction_index(case_dir)

    session_rows = []
    for path in session_files:
        with path.open("r", encoding="utf-8") as handle:
            session = json.load(handle)
        jurisdiction = resolve_jurisdiction(session, by_file_line, by_case_id)
        session_rows.append(session_metrics(path, session, jurisdiction))

    rows = [
        rounded_row(row, args.precision)
        for row in build_summary_rows(session_rows, args.by_jurisdiction)
    ]

    if args.format == "markdown":
        output_text = markdown_table(rows, args.precision, args.by_jurisdiction) + "\n"
    elif args.format == "json":
        output_text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    else:
        output_text = None

    if args.output:
        output_path = project_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            if args.format == "csv":
                write_csv(rows, handle, args.precision, args.by_jurisdiction)
            else:
                handle.write(output_text)
    else:
        if args.format == "csv":
            write_csv(rows, sys.stdout, args.precision, args.by_jurisdiction)
        else:
            sys.stdout.write(output_text)


if __name__ == "__main__":
    main()
