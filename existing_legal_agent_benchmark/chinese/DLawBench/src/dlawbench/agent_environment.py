"""Agent environment for the legal consultation benchmark.

Simplified POMDP-style environment:
- Lawyer asks questions (actions) → Client responds (observations filtered by persona)
- Lawyer builds belief state through dialogue → Writes memo (terminal action)
- No documents, no evidence store. Pure inquiry-based fact extraction.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone

from .api_utils import call_model
from .client_npc import ClientNPC, resolve_lang
from .paths import CONFIG_DIR


def _load_lawyer_prompt(lang):
    fname = "lawyer_agent_prompt_en.txt" if lang == "en" else "lawyer_agent_prompt.txt"
    path = CONFIG_DIR / fname
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _find_json_object(text, start):
    """Find a balanced JSON object starting at text[start] == '{'.
    Returns the substring from '{' to the matching '}', or None.
    """
    if start >= len(text) or text[start] != '{':
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _fix_json_newlines(s):
    """Fix unescaped newlines inside JSON string values."""
    result = []
    in_string = False
    escape = False
    for ch in s:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == '\\':
            result.append(ch)
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch == '\n':
            result.append('\\n')
            continue
        if in_string and ch == '\r':
            continue
        result.append(ch)
    return ''.join(result)


def _try_parse_json(s):
    """Try parsing JSON with progressive fixups for LLM-generated output."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_fix_json_newlines(s))
    except json.JSONDecodeError:
        pass
    return None


def _extract_write_memo(text):
    """Extract write_memo tool call by finding content between markers.
    Handles unescaped quotes inside memo content.
    """
    pattern = re.search(
        r'\{\s*"tool"\s*:\s*"write_memo"\s*,\s*"content"\s*:\s*"',
        text
    )
    if not pattern:
        return None
    content_start = pattern.end()
    close_idx = text.rfind('"}')
    if close_idx <= content_start:
        close_idx = text.rfind('" }')
    if close_idx <= content_start:
        return None
    content = text[content_start:close_idx]
    content = content.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
    return {"tool": "write_memo", "content": content}


def parse_tool_call(text):
    """Extract a tool call JSON from the lawyer's response."""
    text = text.strip()
    obj = _try_parse_json(text)
    if obj and "tool" in obj:
        return obj
    pos = 0
    while pos < len(text):
        idx = text.find('{"tool"', pos)
        if idx == -1:
            idx = text.find('{ "tool"', pos)
        if idx == -1:
            break
        candidate = _find_json_object(text, idx)
        if candidate:
            obj = _try_parse_json(candidate)
            if obj and "tool" in obj:
                return obj
        pos = idx + 1
    memo = _extract_write_memo(text)
    if memo:
        return memo
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        obj = _try_parse_json(match.group(1))
        if obj:
            return obj
    raise ValueError(f"Could not parse tool call from: {text[:200]}")


class AgentEnvironment:
    """POMDP-style legal consultation environment.

    The lawyer's only actions are talk_to_client and write_memo.
    The client's persona acts as the observation function — filtering
    how facts are disclosed (cooperative → clear, defensive → vague/evasive).
    """

    def __init__(self, case, persona, lawyer_model, client_model=None, max_turns=10,
                 client_kwargs=None, lawyer_kwargs=None):
        self.case = case
        self.persona = persona
        self.lawyer_model = lawyer_model
        self.client_model = client_model or lawyer_model
        self.max_turns = max_turns
        self.lawyer_kwargs = lawyer_kwargs or {}

        self.client = ClientNPC(case, persona, self.client_model, **(client_kwargs or {}))

        self.turn = 0
        self.status = "active"
        self.log = []
        self.memo_content = None

    def build_lawyer_system_prompt(self):
        """Build the lawyer's system prompt."""
        lang = resolve_lang(self.case)
        template = _load_lawyer_prompt(lang)
        evl = self.case.get("evaluation", {})
        doc_fmt = evl.get("document_format", {})

        sep = ", " if lang == "en" else "、"
        return template.format(
            opening=self.case.get("opening", ""),
            max_turns=self.max_turns,
            required_sections=sep.join(doc_fmt.get("required_sections", [])),
        )

    def execute_tool(self, tool_call):
        """Execute a tool call and return the result."""
        tool = tool_call.get("tool")
        timestamp = datetime.now(timezone.utc).isoformat()

        if tool == "talk_to_client":
            return self._handle_talk(tool_call, timestamp)
        elif tool == "write_memo":
            return self._handle_memo(tool_call, timestamp)
        else:
            return {"error": f"Unknown tool: {tool}. Available: talk_to_client, write_memo"}

    def _handle_talk(self, tool_call, timestamp):
        """Handle talk_to_client tool call."""
        if self.status != "active":
            return {"error": "Consultation has ended"}
        if self.turn >= self.max_turns:
            self.status = "max_turns_reached"
            return {"error": "Maximum turns reached", "status": "max_turns_reached"}

        message = tool_call.get("message", "")
        self.turn += 1

        response = self.client.respond(message)

        self.log.append({
            "type": "talk",
            "turn": self.turn,
            "lawyer_message": message,
            "client_response": response,
            "timestamp": timestamp,
        })

        return {
            "client_response": response,
            "turn": self.turn,
            "remaining_turns": self.max_turns - self.turn,
        }

    def _handle_memo(self, tool_call, timestamp):
        """Handle write_memo tool call."""
        self.memo_content = tool_call.get("content", "")
        self.status = "memo_submitted"
        self.log.append({
            "type": "memo",
            "content_length": len(self.memo_content),
            "timestamp": timestamp,
        })
        return {"status": "memo_submitted", "length": len(self.memo_content)}

    def run(self):
        """Run the full consultation loop."""
        lawyer_history = []
        system_prompt = self.build_lawyer_system_prompt()
        t_start = time.time()

        while self.status == "active":
            remaining = self.max_turns - self.turn
            urgency = ""
            if remaining <= 2:
                urgency = " ⚠️ 轮次即将用完，请立即调用 write_memo 提交备忘录！"
            state_msg = (
                f"[系统] 当前第 {self.turn}/{self.max_turns} 轮，"
                f"剩余 {remaining} 轮。{urgency}"
            )

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(lawyer_history)
            messages.append({"role": "user", "content": state_msg})

            # Dialogue-level retry: retry the API call up to 3 times
            # if the model returns an empty response.
            _DIAL_RETRIES = 6
            _DIAL_BACKOFF = 5.0
            lawyer_response = ""
            for _attempt in range(1, _DIAL_RETRIES + 1):
                try:
                    _, lawyer_response = call_model(
                        self.lawyer_model, messages, **self.lawyer_kwargs)
                except Exception as exc:
                    lawyer_response = ""
                    sys.stderr.write(
                        f"  [Turn {self.turn}] API exception on attempt "
                        f"{_attempt}/{_DIAL_RETRIES}: {exc}\n")
                if lawyer_response.strip():
                    break
                if _attempt < _DIAL_RETRIES:
                    wait = _DIAL_BACKOFF * (2 ** (_attempt - 1))
                    sys.stderr.write(
                        f"  [Turn {self.turn}] Empty response, "
                        f"dialogue-retry {_attempt}/{_DIAL_RETRIES}, "
                        f"wait {wait:.0f}s\n")
                    time.sleep(wait)

            sys.stderr.write(
                f"  [Turn {self.turn}] ({len(lawyer_response)} chars): "
                f"{lawyer_response[:120]!r}\n")

            # Empty response after all retries = API failure, terminate gracefully
            if not lawyer_response.strip():
                sys.stderr.write(
                    f"  [Turn {self.turn}] Empty response after "
                    f"{_DIAL_RETRIES} attempts, terminating.\n")
                self.status = "api_error"
                break

            try:
                tool_call = parse_tool_call(lawyer_response)
            except ValueError:
                tool_call = {"tool": "talk_to_client", "message": lawyer_response}

            result = self.execute_tool(tool_call)

            lawyer_history.append({"role": "assistant", "content": lawyer_response})
            lawyer_history.append({"role": "user", "content": json.dumps(result, ensure_ascii=False)})

            if self.status != "active":
                break

        elapsed = time.time() - t_start
        return {
            "case_id": self.case.get("case_id"),
            "persona": self.persona.get("name_cn", "unknown"),
            "lawyer_model": self.lawyer_model,
            "client_model": self.client_model,
            "turns_used": self.turn,
            "max_turns": self.max_turns,
            "status": self.status,
            "memo": self.memo_content,
            "log": self.log,
            "elapsed_seconds": round(elapsed, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
