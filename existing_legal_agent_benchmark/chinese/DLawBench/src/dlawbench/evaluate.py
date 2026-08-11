"""Evaluation module for the legal consultation benchmark.

Evaluates:
- FFS: Format Following Score (required sections present in memo)
- MES: Memo Expectation Score (key legal points covered)
- EAS: Expected Action Score (lawyer performed necessary inquiries)
- Set-based DAG evaluation (single LLM call):
  - Discovery (A\\B): Facts in the case but missing from the memo
  - Accuracy (A∩B): Facts covered by both, but is the analysis correct?
  - Grounding (B\\A): Claims in the memo that have no basis in any case fact

Language: prompt templates are bilingual (zh/en), driven by case.language.
JSON output schema (key names like "discovery", "covered", "missed", ...)
stays English regardless of language, so downstream parsers don't change.
"""
import json

from .api_utils import call_model
from .client_npc import resolve_lang


def check_required_sections(memo_text, required_sections):
    """Rule-based check: which required sections appear in the memo."""
    if not required_sections:
        return {"score": 1.0, "present": [], "missing": []}
    present = [s for s in required_sections if s in memo_text]
    missing = [s for s in required_sections if s not in memo_text]
    score = len(present) / len(required_sections)
    return {"score": score, "present": present, "missing": missing}


def compute_process_score(log):
    """Compute process-level metrics from the session log."""
    total_turns = sum(1 for entry in log if entry["type"] == "talk")
    has_memo = any(entry["type"] == "memo" for entry in log)
    return {
        "total_turns": total_turns,
        "memo_submitted": has_memo,
    }


def _build_dialogue_text(dialogue_log, opening="", lang="zh"):
    """Build readable dialogue text from session log.

    If *opening* is provided, it is prepended as the client's initial
    statement (turn 0) so the evaluator treats it as available information.
    """
    if lang == "en":
        labels = {
            "opening": "Client (opening): {}",
            "lawyer":  "Lawyer (Turn {}): {}",
            "client":  "Client: {}",
        }
    else:
        labels = {
            "opening": "客户（开场白）：{}",
            "lawyer":  "律师（第{}轮）：{}",
            "client":  "客户：{}",
        }
    lines = []
    if opening:
        lines.append(labels["opening"].format(opening))
    for entry in dialogue_log:
        if entry["type"] == "talk":
            lines.append(labels["lawyer"].format(entry["turn"], entry["lawyer_message"]))
            lines.append(labels["client"].format(entry["client_response"]))
    return "\n".join(lines)


def evaluate_facts_dag(memo_text, dialogue_log, case, eval_model):
    """Set-based DAG evaluation via a single LLM call.

    Given:
      A = case facts (F1..Fn with client_belief + actual_situation)
      B = lawyer's memo (factual claims)

    Computes three set operations:
      Discovery (A\\B): facts missing from memo
      Accuracy  (A∩B): facts covered but analyzed incorrectly
      Grounding (B\\A): memo claims with no basis in any case fact
    """
    facts = case.get("facts", [])
    if not facts:
        return {"discovery": {}, "accuracy": {}, "grounding": {}, "scores": {}}

    lang = resolve_lang(case)

    # Build fact list (Set A)
    fact_descriptions = []
    if lang == "en":
        for fact in facts:
            fact_descriptions.append(
                f"Fact {fact['id']} ({fact.get('gap_type', 'unknown')}):\n"
                f"  Client's belief: {fact['client_belief']}\n"
                f"  Actual situation: {fact['actual_situation']}"
            )
    else:
        for fact in facts:
            fact_descriptions.append(
                f"事实{fact['id']}（{fact.get('gap_type', 'unknown')}）:\n"
                f"  客户认知: {fact['client_belief']}\n"
                f"  实际情况: {fact['actual_situation']}"
            )
    facts_block = "\n\n".join(fact_descriptions)

    dialogue_text = _build_dialogue_text(
        dialogue_log, opening=case.get("opening", ""), lang=lang,
    )

    if lang == "en":
        empty_memo = "(no memo submitted)"
        sys_msg = "You are a legal consultation quality reviewer. Output JSON only."
        prompt = f"""You are a legal consultation quality reviewer. Evaluate the lawyer's memo on three dimensions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Set A: Case Facts (total {len(facts)} facts, with client's belief and actual situation)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{facts_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Dialogue Record (the full lawyer-client consultation)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{dialogue_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Set B: Lawyer's Memo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{memo_text or empty_memo}

Perform the following three set operations:

**1. Discovery (A \\ B — Omission)**
Identify facts that are in Set A but completely absent from the memo.
- If the memo discusses the related topic (even with inaccurate analysis), it counts as covered, not omitted.
- Return lists of missed and covered fact IDs.

**2. Accuracy (A ∩ B — Correctness)**
For each fact covered by the memo, compare the memo's analysis against the "Actual situation":
- correct: the memo's direction and key points align with the actual situation
- incorrect: the memo's analysis contradicts or deviates from the actual situation

**3. Grounding (B \\ A — Fabrication)**
Scan factual claims (specific numbers, dates, events, people, conditions) and inferential claims (the lawyer's analytic inferences) in the memo. Decide whether each claim has a basis.

A claim is grounded if its basis can be found in EITHER:
- **Set A** (case facts), OR
- **Dialogue Record** (information the client gave the lawyer during the consultation)

Rules:
- Claim has a source in Set A or Dialogue → grounded
- Claim has no source in either → ungrounded (true fabrication / hallucination)
- A reasonable legal inference built on known information → grounded (the premise must appear in A or the Dialogue)
- Citing statutes / proposing litigation strategy → NOT a claim; do not evaluate it

Output JSON (no markdown code block):
{{
  "discovery": {{
    "covered": ["F1", "F3", ...],
    "missed": ["F6", "F10", ...]
  }},
  "accuracy": {{
    "correct": ["F1", "F3", ...],
    "incorrect": [
      {{"fact_id": "F2", "memo_excerpt": "relevant excerpt from memo", "reason": "how it diverges from actual situation"}},
      ...
    ]
  }},
  "grounding": {{
    "total_claims": 10,
    "grounded_claims": [
      {{"claim": "memo excerpt", "basis": "Source: fact ID / dialogue turn N client statement"}},
      ...
    ],
    "ungrounded_claims": [
      {{"claim": "memo excerpt", "reason": "no source in case facts or dialogue"}},
      ...
    ]
  }}
}}"""
    else:
        empty_memo = "（未提交备忘录）"
        sys_msg = "你是一个法律咨询质量评审专家。只输出 JSON。"
        prompt = f"""你是一个法律咨询质量评审专家。请对律师备忘录进行三维评估。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
集合 A：案件事实（共 {len(facts)} 条，含客户认知和实际情况）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{facts_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
对话记录（律师与客户的完整问诊过程）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{dialogue_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
集合 B：律师备忘录
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{memo_text or empty_memo}

请执行以下三个集合操作：

**1. Discovery（A \\ B — 遗漏）**
找出集合 A 中有、但备忘录中完全没有涉及的事实。
- 只要备忘录提到了该事实的相关话题（即使分析不准确），就算覆盖了，不算遗漏。
- 输出被遗漏的事实 ID 列表和被覆盖的事实 ID 列表。

**2. Accuracy（A ∩ B — 准确性）**
对备忘录中覆盖到的每条事实，将备忘录的分析与「实际情况」对照：
- correct: 备忘录的分析方向和要点与实际情况一致
- incorrect: 备忘录的分析与实际情况矛盾或偏离

**3. Grounding（B \\ A — 编造）**
扫描备忘录中的事实性主张（具体数字、日期、事件、人物、条件等）和推论性主张（律师的分析推断），判断每条主张是否有依据。

依据来源有两个（任一即可）：
- **集合 A**（案件事实）
- **对话记录**（客户在问诊中告知律师的信息）

判定规则：
- 备忘录中的主张能在集合 A 或对话记录中找到来源 → grounded
- 备忘录中的主张在集合 A 和对话记录中均无来源 → ungrounded（真正的编造/幻觉）
- 律师基于已知信息做出的合理法律推论 → grounded（推论的前提必须在 A 或对话中存在）
- 律师引用法条、提出诉讼策略建议 → 不算主张，不纳入评估

请输出 JSON（不要包含 markdown 代码块）：
{{
  "discovery": {{
    "covered": ["F1", "F3", ...],
    "missed": ["F6", "F10", ...]
  }},
  "accuracy": {{
    "correct": ["F1", "F3", ...],
    "incorrect": [
      {{"fact_id": "F2", "memo_excerpt": "备忘录相关原文", "reason": "与实际情况的差异"}},
      ...
    ]
  }},
  "grounding": {{
    "total_claims": 10,
    "grounded_claims": [
      {{"claim": "备忘录原文", "basis": "来源：事实ID / 对话第N轮客户陈述"}},
      ...
    ],
    "ungrounded_claims": [
      {{"claim": "备忘录中的原文", "reason": "在案件事实和对话记录中均无来源"}},
      ...
    ]
  }}
}}"""

    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": prompt},
    ]
    _, response = call_model(
        eval_model, messages, max_tokens=32768, reasoning_effort="low",
    )

    response = response.strip()
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        return {"error": "Failed to parse LLM evaluation", "raw": response[:500]}

    # Extract results
    discovery = result.get("discovery", {})
    accuracy = result.get("accuracy", {})
    grounding = result.get("grounding", {})

    covered = discovery.get("covered", [])
    missed = discovery.get("missed", [])
    correct = accuracy.get("correct", [])
    incorrect = accuracy.get("incorrect", [])

    grounded = grounding.get("grounded_claims", [])
    ungrounded = grounding.get("ungrounded_claims", [])
    total_claims = grounding.get("total_claims", len(grounded) + len(ungrounded))
    total_bad = len(ungrounded)

    n = len(facts)
    n_covered = len(covered)
    n_correct = len(correct)
    n_incorrect = len(incorrect)

    # Grounding rate (single metric — the prompt asks the judge to distinguish
    # factual vs inferential claims internally, but we report one combined rate)
    grounding_rate = round((total_claims - total_bad) / total_claims, 4) if total_claims > 0 else None

    # Discovery and Accuracy rates
    discovery_rate = round(n_covered / n, 4) if n else None
    accuracy_rate = round(n_correct / n_covered, 4) if n_covered else None
    # Absolute Accuracy: n_correct / n_facts (= D × A), penalizes "少做少错"
    absolute_accuracy = round(n_correct / n, 4) if n else None

    # Enrich with gap_type breakdown
    fact_gap_map = {f["id"]: f.get("gap_type", "unknown") for f in facts}
    breakdown = {}
    for label in ("disputed", "undisputed"):
        subset_ids = [fid for fid in fact_gap_map if fact_gap_map[fid] == label]
        if not subset_ids:
            continue
        sub_n = len(subset_ids)
        sub_covered = [fid for fid in covered if fact_gap_map.get(fid) == label]
        sub_correct = [fid for fid in correct if fact_gap_map.get(fid) == label]
        sub_in_memo = len(sub_covered)
        breakdown[label] = {
            "n": sub_n,
            "discovery_rate": round(len(sub_covered) / sub_n, 4),
            "accuracy_rate": round(
                len(sub_correct) / sub_in_memo, 4
            ) if sub_in_memo else None,
        }

    scores = {
        "discovery_rate": discovery_rate,
        "accuracy_rate": accuracy_rate,
        "absolute_accuracy": absolute_accuracy,
        "grounding_rate": grounding_rate,
        "n_facts": n,
        "n_covered": n_covered,
        "n_missed": len(missed),
        "n_correct": n_correct,
        "n_incorrect": n_incorrect,
        "n_claims_total": total_claims,
        "n_claims_ungrounded": total_bad,
    }
    if breakdown:
        scores["by_gap_type"] = breakdown

    return {
        "discovery": discovery,
        "accuracy": accuracy,
        "grounding": grounding,
        "scores": scores,
    }


def evaluate_memo_with_llm(memo_text, dialogue_log, case, eval_model):
    """Use LLM-as-Judge to evaluate memo quality.

    Passes both memo AND dialogue log so the judge can assess
    whether the lawyer actually asked the right questions.
    """
    expectations = case.get("evaluation", {}).get("memo_expectations", [])
    expected_actions = case.get("evaluation", {}).get("expected_actions", [])

    lang = resolve_lang(case)
    dialogue_text = _build_dialogue_text(
        dialogue_log, opening=case.get("opening", ""), lang=lang,
    )

    if lang == "en":
        sys_msg = "You are a legal memo reviewer. Output JSON only."
        prompt = f"""You are a legal memo reviewer. Evaluate based on the dialogue record and the memo content.

[Evaluation Criteria — Memo Expectations] (0 or 1 per item)
{chr(10).join(f'{i+1}. {exp}' for i, exp in enumerate(expectations))}

[Evaluation Criteria — Lawyer's Inquiry Actions] (0 or 1 per item, judged from the dialogue record)
{chr(10).join(f'{i+1}. {act}' for i, act in enumerate(expected_actions))}

[Dialogue Record]
{dialogue_text}

[Memo]
{memo_text}

Output JSON (no markdown code block):
{{
  "memo_expectation_scores": [0 or 1, ...],
  "expected_action_scores":  [0 or 1, ...],
  "overall_quality": "Excellent / Good / Pass / Fail",
  "analysis": "brief comment"
}}"""
    else:
        sys_msg = "你是一个法律备忘录评审专家。只输出 JSON。"
        prompt = f"""你是一个法律备忘录评审专家。请根据对话记录和备忘录内容进行评估。

【评估标准 — 备忘录要点】（每项 0 或 1 分）
{chr(10).join(f'{i+1}. {exp}' for i, exp in enumerate(expectations))}

【评估标准 — 律师问诊动作】（每项 0 或 1 分，根据对话记录判断律师是否执行了该动作）
{chr(10).join(f'{i+1}. {act}' for i, act in enumerate(expected_actions))}

【对话记录】
{dialogue_text}

【备忘录内容】
{memo_text}

请输出 JSON（不要包含 markdown 代码块）：
{{
  "memo_expectation_scores": [0或1, ...],
  "expected_action_scores": [0或1, ...],
  "overall_quality": "优秀/良好/合格/不合格",
  "analysis": "简要评语"
}}"""

    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": prompt},
    ]
    _, response = call_model(
        eval_model, messages, max_tokens=32768, reasoning_effort="low",
    )

    response = response.strip()
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        result = {"error": "Failed to parse LLM evaluation", "raw": response[:500]}

    memo_scores = result.get("memo_expectation_scores", [])
    action_scores = result.get("expected_action_scores", [])

    mes = sum(memo_scores) / len(memo_scores) if memo_scores else 0
    eas = sum(action_scores) / len(action_scores) if action_scores else 0

    result["memo_expectation_score"] = round(mes, 4)
    result["expected_action_score"] = round(eas, 4)

    return result


def evaluate_session(session_result, case, eval_model):
    """Full evaluation of a consultation session."""
    evl = case.get("evaluation", {})
    doc_fmt = evl.get("document_format", {})
    required_sections = doc_fmt.get("required_sections", [])

    process = compute_process_score(session_result.get("log", []))

    memo = session_result.get("memo", "") or ""
    format_check = check_required_sections(memo, required_sections)

    memo_eval = {}
    fact_eval = {}
    log = session_result.get("log", [])
    if memo:
        memo_eval = evaluate_memo_with_llm(memo, log, case, eval_model)
        fact_eval = evaluate_facts_dag(memo, log, case, eval_model)

    dag_scores = fact_eval.get("scores", {})

    return {
        "case_id": case.get("case_id"),
        "process": process,
        "format": format_check,
        "memo_quality": memo_eval,
        "fact_evaluation": fact_eval,
        "scores": {
            "FFS": round(format_check["score"], 4),  # filter only, not a ranking metric
            "MES": memo_eval.get("memo_expectation_score", 0),
            "EAS": memo_eval.get("expected_action_score", 0),
            "discovery_rate": dag_scores.get("discovery_rate"),
            "accuracy_rate": dag_scores.get("accuracy_rate"),
            "absolute_accuracy": dag_scores.get("absolute_accuracy"),
            "grounding_rate": dag_scores.get("grounding_rate"),
        },
    }
