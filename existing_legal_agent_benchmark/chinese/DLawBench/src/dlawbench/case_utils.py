"""Case loading and schema normalization utilities."""

from __future__ import annotations

import json
import os


JSONL_REF_SEP = "::jsonl::"


def make_jsonl_ref(path, line_no, case_id):
    return f"{path}{JSONL_REF_SEP}{line_no}{JSONL_REF_SEP}{case_id}"


def split_jsonl_ref(ref):
    parts = str(ref).split(JSONL_REF_SEP)
    if len(parts) == 3:
        return parts[0], int(parts[1]), parts[2]
    return ref, None, None


def case_source_path(case_ref):
    path, _line_no, _case_id = split_jsonl_ref(case_ref)
    return path


def case_name(case_ref):
    path, line_no, case_id = split_jsonl_ref(case_ref)
    if line_no is not None:
        return case_id
    return os.path.splitext(os.path.basename(path))[0]


def normalize_case(raw_case):
    """Normalize publication JSONL schema to the runner's internal schema."""
    case = dict(raw_case)
    case_id = case.get("case_id") or case.get("id")
    if case_id:
        case["case_id"] = case_id
    case.setdefault("title", case_id or "untitled_case")

    normalized_facts = []
    for index, fact in enumerate(case.get("facts", []), start=1):
        item = dict(fact)
        fact_id = item.get("id") or item.get("fact_id") or f"F{index}"
        item["id"] = fact_id
        if "actual_situation" not in item and "court_record" in item:
            item["actual_situation"] = item["court_record"]
        item.setdefault("gap_type", "disputed")
        normalized_facts.append(item)
    case["facts"] = normalized_facts

    if "evaluation" not in case and isinstance(case.get("evaluation_criteria"), dict):
        criteria = case["evaluation_criteria"]
        case["evaluation"] = {
            "expected_actions": criteria.get("expected_actions")
            or criteria.get("expected_inquiries")
            or [],
            "memo_expectations": criteria.get("memo_expectations") or [],
            "document_format": {
                "type": "legal_memo",
                "required_sections": [
                    "Case Overview",
                    "Key Facts",
                    "Legal Issues",
                    "Risk Assessment",
                    "Recommended Next Steps",
                ] if case.get("language") == "en" else [
                    "案情概要",
                    "关键事实",
                    "法律问题",
                    "风险评估",
                    "下一步建议",
                ],
            },
        }

    return case


def load_case(case_ref):
    """Load one case from a JSON file, JSONL file, or JSONL reference."""
    source_path, line_no, _case_id = split_jsonl_ref(case_ref)
    if line_no is not None:
        with open(source_path, "r", encoding="utf-8") as handle:
            for current_line_no, line in enumerate(handle, start=1):
                if current_line_no == line_no:
                    return normalize_case(json.loads(line))
        raise FileNotFoundError(f"JSONL line not found: {source_path}:{line_no}")

    with open(source_path, "r", encoding="utf-8") as handle:
        if source_path.endswith(".jsonl"):
            for line in handle:
                line = line.strip()
                if line:
                    return normalize_case(json.loads(line))
            raise ValueError(f"JSONL file has no case records: {source_path}")
        return normalize_case(json.load(handle))


def load_jsonl_case_refs(path):
    refs = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            case = normalize_case(json.loads(line))
            case_id = case.get("case_id") or f"{os.path.splitext(os.path.basename(path))[0]}_{line_no}"
            refs.append(make_jsonl_ref(path, line_no, case_id))
    return refs


def expand_case_inputs(paths):
    refs = []
    for path in paths:
        if path.endswith(".jsonl"):
            refs.extend(load_jsonl_case_refs(path))
        else:
            refs.append(path)
    return refs


def find_case_in_dir(case_dir, session):
    """Find a case for a session in a directory containing JSON or JSONL files."""
    case_id = session.get("case_id")
    case_file = session.get("case_file")
    case_line = session.get("case_line")

    if case_file:
        path = os.path.join(case_dir, case_file)
        if os.path.isfile(path):
            if path.endswith(".jsonl") and case_line:
                return load_case(make_jsonl_ref(path, case_line, case_id))
            case = load_case(path)
            if not case_id or case.get("case_id") == case_id:
                return case

    if case_id:
        json_path = os.path.join(case_dir, f"{case_id}.json")
        if os.path.isfile(json_path):
            return load_case(json_path)

    if not os.path.isdir(case_dir):
        return None

    for fname in os.listdir(case_dir):
        if not (fname.endswith(".json") or fname.endswith(".jsonl")):
            continue
        path = os.path.join(case_dir, fname)
        if fname.endswith(".json"):
            case = load_case(path)
            if case.get("case_id") == case_id:
                return case
            continue
        for ref in load_jsonl_case_refs(path):
            case = load_case(ref)
            if case.get("case_id") == case_id:
                return case
    return None
