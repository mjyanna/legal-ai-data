#!/usr/bin/env python3
"""Validate the public DLawBench release dataset."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "case_backgrounds"
METADATA_DIR = ROOT / "metadata"

CASE_FILES = {
    "CN": DATA_DIR / "cases_cn.jsonl",
    "US": DATA_DIR / "cases_us.jsonl",
}

EXPECTED_LANGUAGES = {
    "CN": "zh",
    "US": "en",
}

REQUIRED_TOP_LEVEL_KEYS = {
    "id",
    "jurisdiction",
    "language",
    "opening",
    "facts",
    "evaluation_criteria",
    "sha256",
}

REQUIRED_FACT_KEYS = {
    "fact_id",
    "client_belief",
    "court_record",
    "gap_type",
}

REQUIRED_CRITERIA_KEYS = {
    "expected_inquiries",
    "memo_expectations",
}

STAT_KEYS = [
    "cases",
    "case_style_cells",
    "fact_pairs",
    "disputed_fact_pairs",
    "undisputed_fact_pairs",
    "inquiry_rubrics",
    "issue_rubrics",
]


def canonical_sha256(record: dict) -> str:
    public_record = dict(record)
    public_record.pop("sha256", None)
    payload = json.dumps(
        public_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_jsonl(path: Path, expected_jurisdiction: str) -> list[dict]:
    if not path.is_file():
        fail(f"missing case file: {path.relative_to(ROOT)}")

    records: list[dict] = []
    seen_ids: set[str] = set()
    expected_language = EXPECTED_LANGUAGES[expected_jurisdiction]

    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"{path.name}:{line_no}: invalid JSON: {exc}")

            if set(record) != REQUIRED_TOP_LEVEL_KEYS:
                extra = sorted(set(record) - REQUIRED_TOP_LEVEL_KEYS)
                missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(record))
                fail(f"{path.name}:{line_no}: top-level keys mismatch; missing={missing}, extra={extra}")

            if record["jurisdiction"] != expected_jurisdiction:
                fail(f"{path.name}:{line_no}: jurisdiction must be {expected_jurisdiction}")
            if record["language"] != expected_language:
                fail(f"{path.name}:{line_no}: language must be {expected_language}")
            if record["id"] in seen_ids:
                fail(f"{path.name}:{line_no}: duplicate public id {record['id']}")
            seen_ids.add(record["id"])

            if not isinstance(record["facts"], list) or not record["facts"]:
                fail(f"{path.name}:{line_no}: facts must be a non-empty list")
            for fact_index, fact in enumerate(record["facts"], start=1):
                if set(fact) != REQUIRED_FACT_KEYS:
                    extra = sorted(set(fact) - REQUIRED_FACT_KEYS)
                    missing = sorted(REQUIRED_FACT_KEYS - set(fact))
                    fail(
                        f"{path.name}:{line_no}: fact {fact_index} keys mismatch; "
                        f"missing={missing}, extra={extra}"
                    )
                if fact["gap_type"] not in {"disputed", "undisputed"}:
                    fail(f"{path.name}:{line_no}: fact {fact_index} has invalid gap_type")

            criteria = record["evaluation_criteria"]
            if set(criteria) != REQUIRED_CRITERIA_KEYS:
                extra = sorted(set(criteria) - REQUIRED_CRITERIA_KEYS)
                missing = sorted(REQUIRED_CRITERIA_KEYS - set(criteria))
                fail(f"{path.name}:{line_no}: evaluation_criteria keys mismatch; missing={missing}, extra={extra}")
            if not isinstance(criteria["expected_inquiries"], list):
                fail(f"{path.name}:{line_no}: expected_inquiries must be a list")
            if not isinstance(criteria["memo_expectations"], list):
                fail(f"{path.name}:{line_no}: memo_expectations must be a list")

            expected_sha = canonical_sha256(record)
            if record["sha256"] != expected_sha:
                fail(f"{path.name}:{line_no}: sha256 mismatch for {record['id']}")

            records.append(record)

    return records


def stats_for(records: list[dict]) -> dict[str, int]:
    fact_pairs = 0
    disputed = 0
    inquiry_rubrics = 0
    issue_rubrics = 0

    for record in records:
        facts = record["facts"]
        criteria = record["evaluation_criteria"]
        fact_pairs += len(facts)
        disputed += sum(1 for fact in facts if fact["gap_type"] == "disputed")
        inquiry_rubrics += len(criteria["expected_inquiries"])
        issue_rubrics += len(criteria["memo_expectations"])

    cases = len(records)
    return {
        "cases": cases,
        "case_style_cells": cases * 4,
        "fact_pairs": fact_pairs,
        "disputed_fact_pairs": disputed,
        "undisputed_fact_pairs": fact_pairs - disputed,
        "inquiry_rubrics": inquiry_rubrics,
        "issue_rubrics": issue_rubrics,
    }


def load_manifest() -> dict[str, dict[str, str]]:
    manifest_path = METADATA_DIR / "manifest.csv"
    if not manifest_path.is_file():
        fail("missing metadata/manifest.csv")

    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {row["id"]: row for row in rows}


def validate_manifest(records: list[dict], manifest: dict[str, dict[str, str]]) -> None:
    if len(manifest) != len(records):
        fail(f"manifest row count mismatch: expected {len(records)}, got {len(manifest)}")

    for record in records:
        row = manifest.get(record["id"])
        if row is None:
            fail(f"manifest missing id {record['id']}")

        facts = record["facts"]
        criteria = record["evaluation_criteria"]
        expected = {
            "jurisdiction": record["jurisdiction"],
            "language": record["language"],
            "n_facts": str(len(facts)),
            "n_disputed_facts": str(sum(1 for fact in facts if fact["gap_type"] == "disputed")),
            "n_undisputed_facts": str(sum(1 for fact in facts if fact["gap_type"] == "undisputed")),
            "n_inquiry_rubrics": str(len(criteria["expected_inquiries"])),
            "n_issue_rubrics": str(len(criteria["memo_expectations"])),
            "sha256": record["sha256"],
        }
        for key, value in expected.items():
            if row.get(key) != value:
                fail(f"manifest mismatch for {record['id']} column {key}: expected {value!r}, got {row.get(key)!r}")


def validate_stats(actual: dict[str, dict[str, int]]) -> None:
    summary_path = METADATA_DIR / "dataset_summary.json"
    if not summary_path.is_file():
        fail("missing metadata/dataset_summary.json")

    with summary_path.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)

    expected = summary.get("statistics")
    if not isinstance(expected, dict):
        fail("metadata/dataset_summary.json missing statistics object")

    for jurisdiction in ["CN", "US", "TOTAL"]:
        for key in STAT_KEYS:
            if expected.get(jurisdiction, {}).get(key) != actual[jurisdiction][key]:
                fail(
                    f"statistics mismatch for {jurisdiction}.{key}: "
                    f"expected {expected.get(jurisdiction, {}).get(key)!r}, got {actual[jurisdiction][key]!r}"
                )


def main() -> None:
    records_by_jurisdiction = {
        jurisdiction: load_jsonl(path, jurisdiction)
        for jurisdiction, path in CASE_FILES.items()
    }
    all_records = records_by_jurisdiction["CN"] + records_by_jurisdiction["US"]

    manifest = load_manifest()
    validate_manifest(all_records, manifest)

    actual = {
        "CN": stats_for(records_by_jurisdiction["CN"]),
        "US": stats_for(records_by_jurisdiction["US"]),
    }
    actual["TOTAL"] = {
        key: actual["CN"][key] + actual["US"][key]
        for key in STAT_KEYS
    }

    validate_stats(actual)

    print("DLawBench release dataset validation passed.")
    for jurisdiction in ["CN", "US", "TOTAL"]:
        stats = actual[jurisdiction]
        print(
            f"{jurisdiction}: "
            f"cases={stats['cases']}, "
            f"case_style_cells={stats['case_style_cells']}, "
            f"fact_pairs={stats['fact_pairs']}, "
            f"disputed_fact_pairs={stats['disputed_fact_pairs']}, "
            f"inquiry_rubrics={stats['inquiry_rubrics']}, "
            f"issue_rubrics={stats['issue_rubrics']}"
        )


if __name__ == "__main__":
    main()
