"""API call utilities for multi-provider LLM access.

Provider routing is manually configured:
  - PROVIDERS: registry of API platforms (env var names only — no hardcoded keys/URLs)
  - MODEL_ROUTING: maps concrete model snapshot names to provider names
  - MODEL_PARAMS: per-model custom API parameters (thinking mode, reasoning effort, etc.)

All API keys and base URLs are read exclusively from environment variables (.env).

To add a new API platform:
  1. Add an entry to PROVIDERS (api_key_env + base_url_env)
  2. Add model snapshot → provider mappings to MODEL_ROUTING
  3. (Optional) Add per-model params to MODEL_PARAMS
  4. Add the corresponding env vars to .env
  5. If the platform doesn't follow OpenAI chat completions format,
     implement a call_*() and extract_content_from_*() pair
"""

import json
import os
import socket
import time
from urllib import request, error

from .paths import CONFIG_DIR


# ── Default call parameters ──────────────────────────────────────────────────

DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT = 600
DEFAULT_RETRIES = 15


def default_max_tokens_for_model(model_name: str) -> int:
    """Default max_completion budget."""
    return DEFAULT_MAX_TOKENS

# ── Provider registry ────────────────────────────────────────────────────────
# Each entry only declares which env vars hold the API key and base URL.
# Actual values live in .env — no hardcoded keys or URLs here.

PROVIDERS = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
    },
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_BASE_URL",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
    },
    "alibaba": {
        "api_key_env": "ALIBABA_API_KEY",
        "base_url_env": "ALIBABA_BASE_URL",
    },
    "xai": {
        "api_key_env": "XAI_API_KEY",
        "base_url_env": "XAI_BASE_URL",
    },
    "moonshot": {
        "api_key_env": "MOONSHOT_API_KEY",
        "base_url_env": "MOONSHOT_BASE_URL",
    },
    "zhipu": {
        "api_key_env": "ZHIPU_API_KEY",
        "base_url_env": "ZHIPU_BASE_URL",
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
    },
    "ai2": {
        "api_key_env": "AI2_API_KEY",
        "base_url_env": "AI2_BASE_URL",
    },
    "minimax": {
        "api_key_env": "MINIMAX_API_KEY",
        "base_url_env": "MINIMAX_BASE_URL",
    },
    "volcengine": {
        "api_key_env": "VOLCENGINE_API_KEY",
        "base_url_env": "VOLCENGINE_BASE_URL",
    },
    "localhost": {
        "api_key_env": "LOCALHOST_API_KEY",
        "base_url_env": "LOCALHOST_BASE_URL",
    },
}




# ── Model routing table ──────────────────────────────────────────────────────
# Maps concrete model snapshot name → provider.  First match wins (prefix match).


MODEL_ROUTING = {

    "gpt-5.5":"openai",
    "gpt-5.4-2026-03-05":"openai",
    "gpt-5.2-2025-12-11":"openai",
    "gpt-5.1-2025-11-13":"openai",
    "gpt-5-mini-2025-08-07":"openai",
    "gpt-5-2025-08-07":"openai",

    "gemini-3.1-pro-preview":"gemini",
    "gemini-3.1-flash-lite-preview":"gemini",
    "gemini-3.1-pro":"gemini",

    "kimi-k2.6":"moonshot",
    "kimi-k2.5":"moonshot",
    "kimi-k2-thinking":"moonshot",
    "kimi-k2-0905-preview":"moonshot",

    "claude-opus-4-6":"anthropic",
    "claude-sonnet-4-6":"anthropic",
    "claude-4.6-opus":"anthropic",
    "claude-opus-4-6-20260205":"anthropic",
    "claude-sonnet-4-6-20260217":"anthropic",
    "claude-opus-4-7":"anthropic",

    "deepseek-v3.2-thinking":   "deepseek",
    "deepseek-v4-pro":          "deepseek",
    "deepseek-r1-250528":       "deepseek",
    "deepseek-r1":              "deepseek",

    "qwen3-max":     "alibaba",
    "qwen-3.5-thinking-max": "alibaba",
    "qwen-3.5":      "alibaba",
    
    "MiniMax-M2.5":   "minimax",
    "MiniMax-M2.7":   "minimax",
    
    "glm-5":"zhipu",
    "glm-5.1":"zhipu",
    
    "grok-4-1-fast":"xai",
    "grok-4":"xai",
    
    "doubao-seed-2-0-pro-260215":"volcengine",
    
    "Olmo-3-32B-Think":"localhost",
    "Olmo-3.1-32B-Instruct":"localhost",
    "olmo-3.1-instruct": "localhost",
    "olmo-3.1-thinking": "localhost",
    "SaulLM-141B-Instruct":"localhost",
    "LegalOne-8B":"localhost",
    "qwen3.5-plus-2026-02-15":  "alibaba",
    "qwen3.5-flash-2026-02-23": "alibaba",
    "qwen3.6-flash-2026-04-16": "alibaba",
    "qwen3.6-plus-2026-04-02":  "alibaba",
    "qwen3.6-max-preview":      "alibaba",
}


MODEL_PARAMS = {

    # ── Proprietary lawyers ──────────────────────────────────────────────
    "gpt-5.2-2025-12-11":       {},
    "gpt-5.5":                  {},
    "gpt-5.4-2026-03-05":       {},
    "claude-4.6-opus":          {},
    "claude-sonnet-4-6":        {},
    "gemini-3.1-pro":           {},
    "grok-4":                   {},
    "kimi-k2.5":                {},
    "glm-5":                    {},
    "gpt-5-mini-2025-08-07":    {},
    "gpt-5-2025-08-07":         {},
    "gemini-3.1-pro-preview":   {},
    "gemini-3.1-flash-lite-preview":   {},
    "kimi-k2.5":                {},
    "kimi-k2-thinking":         {},
    "kimi-k2-0905-preview":     {},
    "claude-opus-4-6-20260205": {},
    "claude-sonnet-4-6-20260217": {},

    # ── Open-source lawyers ──────────────────────────────────────────────
    "qwen-3.5-thinking-max":    {},
    "qwen-3.5":                 {},
    "deepseek-v3.2-thinking":   {},
    "deepseek-r1":              {},
    "deepseek-v4-pro":          {},
    "olmo-3.1-instruct":        {},
    "olmo-3.1-thinking":        {},
}


# ── Localhost (local deployment) control ──────────────────────────────────────
# USE_LOCALHOST: master switch — True when LOCALHOST_BASE_URL env var is set.
# LOCALHOST_MODELS: model names routed to a local server (e.g. vLLM, Ollama).
#   Only effective when USE_LOCALHOST is True.

def _use_localhost():
    """Dynamic check — .env may be loaded after this module is imported."""
    return bool(os.environ.get("LOCALHOST_BASE_URL"))

LOCALHOST_MODELS = [
    "Olmo-3-32B-Think",
    "Olmo-3.1-32B-Instruct",
    "SaulLM-141B-Instruct",
    "LegalOne-8B",
]

# Localhost concurrency control
LOCALHOST_CONCURRENT = True   # Set True to enable parallel dialogue runs
LOCALHOST_WORKERS   = 8      # Number of concurrent workers when enabled

# ── Fallback routing ────────────────────────────────────────────────────────
# When the primary provider fails, try the fallback provider with a
# possibly different model name.  Output files / logs still use the
# canonical model name (the dict key).

# ── Model name overrides ────────────────────────────────────────────────────
# When the canonical name (used in logs/files) differs from the API model name.

MODEL_API_NAME = {
    "deepseek-r1-250528": "deepseek-r1-0528",
}

# Keys managed internally — never treated as extra payload fields.
_STANDARD_KWARGS = frozenset({
    "temperature", "max_tokens", "top_p", "seed", "stop",
    "timeout", "retries",
})


# ── HTTP helper ──────────────────────────────────────────────────────────────

def http_post_json(url, headers, payload, timeout, retries=0):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    last_exc = None
    for attempt in range(0, max(0, int(retries)) + 1):
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", None) or getattr(resp, "code", None)
                stripped = body.strip()
                if not stripped:
                    raise ValueError(
                        f"Empty HTTP response body (status={status}) from {req.full_url}"
                    )
                try:
                    return json.loads(body)
                except json.JSONDecodeError as jexc:
                    preview = stripped[:800]
                    raise ValueError(
                        f"Non-JSON HTTP response (status={status}) from {req.full_url}: "
                        f"{jexc}; body_preview={preview!r}"
                    ) from jexc
        except error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            code = getattr(exc, 'code', 0) or 0
            # 429 Too Many Requests / 5xx → retryable
            if code == 429 or code >= 500:
                last_exc = exc
                if attempt >= int(retries):
                    msg = f"HTTP {code} {getattr(exc, 'reason', '')}".strip()
                    if body:
                        msg += f" | body={body[:2000]}"
                    raise error.HTTPError(
                        exc.url, exc.code, msg, getattr(exc, "headers", None), None
                    )
                # 429 uses longer backoff (base 5s) to respect rate limits
                wait = (5.0 * (2 ** attempt)) if code == 429 else (0.5 * (2 ** attempt))
                print(f"  [retry] HTTP {code}, attempt {attempt+1}/{int(retries)+1}, wait {wait:.1f}s")
                time.sleep(3)
                continue
            # Other HTTP errors (4xx) are not retryable
            msg = f"HTTP {code} {getattr(exc, 'reason', '')}".strip()
            if body:
                msg += f" | body={body[:2000]}"
            raise error.HTTPError(
                exc.url, exc.code, msg, getattr(exc, "headers", None), None
            )
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt >= int(retries):
                raise
            print(f"  [retry] {type(exc).__name__}, attempt {attempt+1}/{int(retries)+1}")
            time.sleep(3)
    raise last_exc


# ── Provider-specific call functions ─────────────────────────────────────────
# Each accepts an optional `extra_body` dict that is merged into the payload
# after the standard fields, so per-model params pass through transparently.

def call_chat_completion(base_url, api_key, params, messages, timeout,
                         retries=0, extra_body=None):
    url = base_url.rstrip("/") + "/chat/completions"
    model_name = (params.get("model") or "").strip()
    max_token_field = "max_completion_tokens" if _is_gpt5_family(model_name) else "max_tokens"
    payload = {
        "model": params["model"],
        "messages": messages,
        "temperature": params.get("temperature"),
        "top_p": params.get("top_p"),
        max_token_field: params.get("max_tokens"),
        "seed": params.get("seed"),
        "stop": params.get("stop"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if extra_body:
        payload.update(extra_body)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    return http_post_json(url, headers, payload, timeout, retries=retries)


def to_responses_input(messages):
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if not isinstance(role, str) or not role:
            continue
        text = str(content) if content is not None else ""
        out.append({"role": role, "content": [{"type": "input_text", "text": text}]})
    return out


def call_responses_api(base_url, api_key, params, messages, timeout,
                       retries=0, extra_body=None):
    url = base_url.rstrip("/") + "/responses"
    payload = {
        "model": params["model"],
        "input": to_responses_input(messages),
        "temperature": params.get("temperature"),
        "top_p": params.get("top_p"),
        "max_output_tokens": params.get("max_tokens"),
        "stop": params.get("stop"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if extra_body:
        body = dict(extra_body)
        # Translate Chat Completions param to Responses API format
        if "reasoning_effort" in body:
            effort = body.pop("reasoning_effort")
            body["reasoning"] = {"effort": effort}
        payload.update(body)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    return http_post_json(url, headers, payload, timeout, retries=retries)


def call_anthropic_api(base_url, api_key, params, messages, timeout,
                       retries=0, extra_body=None):
    """Call Anthropic Messages API. Converts OpenAI-format messages to Anthropic format."""
    url = base_url.rstrip("/") + "/messages"
    system_parts = []
    conversation = []
    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            system_parts.append(str(content))
        else:
            conversation.append({"role": role, "content": str(content)})
    payload = {
        "model": params["model"],
        "messages": conversation,
        "max_tokens": params.get("max_tokens") or 4096,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if params.get("temperature") is not None:
        payload["temperature"] = params["temperature"]
    if params.get("stop"):
        payload["stop_sequences"] = (
            params["stop"] if isinstance(params["stop"], list) else [params["stop"]]
        )
    if extra_body:
        # Filter out OpenAI-only params that Anthropic doesn't support
        anthropic_body = {k: v for k, v in extra_body.items()
                         if k not in ("reasoning_effort", "reasoning", "seed")}
        payload.update(anthropic_body)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    return http_post_json(url, headers, payload, timeout, retries=retries)


# ── Response extractors ──────────────────────────────────────────────────────

def extract_content(response):
    try:
        return response["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError):
        raise ValueError("Unexpected response format from LLM API")


def extract_content_from_responses(response):
    try:
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text") and part.get("text"):
                    return part["text"].strip()
        raise ValueError
    except (KeyError, IndexError, AttributeError, ValueError):
        raise ValueError("Unexpected response format from Responses API")


def extract_content_from_anthropic(response):
    """Extract text content from an Anthropic Messages API response."""
    try:
        for block in response.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                return block["text"].strip()
        raise ValueError
    except (KeyError, IndexError, AttributeError, ValueError):
        raise ValueError("Unexpected response format from Anthropic API")


# ── Model name utilities ─────────────────────────────────────────────────────

def _is_gpt5_family(model_name):
    """Check if the model belongs to the GPT-5 family (uses max_completion_tokens)."""
    if not model_name:
        return False
    name = model_name.strip().lower()
    return name.startswith("gpt-5") or name.startswith("gpt5")


def use_responses_api(model_name):
    """Check if the model should use the Responses API endpoint.
    gpt-5-mini uses Chat Completions API, not Responses API.
    """
    if not model_name:
        return False
    name = model_name.strip().lower()
    if "mini" in name:
        return False
    return name.startswith("gpt-5") or name.startswith("gpt5")


def normalize_model_name(name):
    if not name:
        return name
    s = name.strip()
    lower = s.lower()
    if lower.startswith("gpt5"):
        return "gpt-" + s[len("gpt"):]
    if lower.startswith("gpt4o"):
        return "gpt-" + s[len("gpt"):]
    return s


def use_anthropic_api(model_name: str) -> bool:
    if not model_name:
        return False
    return model_name.strip().lower().startswith("claude")


def use_gemini_api(model_name: str) -> bool:
    if not model_name:
        return False
    return model_name.strip().lower().startswith("gemini")


def is_mock_model(model_name: str) -> bool:
    """Return True for deterministic local smoke-test models."""
    return (model_name or "").strip().lower().startswith("mock")


def _mock_lawyer_response(messages):
    """Deterministic lawyer behavior for smoke tests without API keys."""
    client_answers = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            payload = json.loads(msg.get("content", ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("client_response"):
            client_answers.append(payload["client_response"])

    if len(client_answers) == 0:
        return {
            "tool": "talk_to_client",
            "message": "Please briefly describe what happened and what result you want."
        }
    if len(client_answers) == 1:
        return {
            "tool": "talk_to_client",
            "message": "What written agreement, invoice, or message record do you have?"
        }
    if len(client_answers) == 2:
        return {
            "tool": "talk_to_client",
            "message": "What evidence supports the delay or defects, and what loss did this cause?"
        }

    memo = [
        "# Legal Consultation Memo",
        "",
        "## Case Overview",
        "The client reports a service contract dispute involving late delivery and alleged defects.",
        "",
        "## Key Facts",
    ]
    for idx, answer in enumerate(client_answers, start=1):
        memo.append(f"{idx}. {answer}")
    memo.extend([
        "",
        "## Legal Issues",
        "The main issues are breach of contract, cure-period compliance, defect proof, and damages.",
        "",
        "## Risk Assessment",
        "The claim may be weakened if the contract allowed a cure period or if defect evidence is incomplete.",
        "",
        "## Recommended Next Steps",
        "Collect the contract, invoices, payment records, communications, delivery records, photos, complaints, and loss documentation.",
    ])
    return {"tool": "write_memo", "content": "\n".join(memo)}


def _mock_client_response(messages):
    """Deterministic client behavior for smoke tests without API keys."""
    system = messages[0].get("content", "") if messages else ""
    facts = []
    for line in system.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            facts.append(stripped[2:].strip())
    previous_answers = sum(1 for msg in messages if msg.get("role") == "assistant")
    if previous_answers < len(facts):
        return facts[previous_answers]
    return "That is all I remember right now. I can provide the contract, payment record, messages, and photos."


def _mock_judge_response(messages):
    """Deterministic judge behavior for smoke tests without API keys."""
    prompt = "\n".join(msg.get("content", "") for msg in messages)
    if "memo_expectation_scores" in prompt and "expected_action_scores" in prompt:
        return json.dumps({
            "memo_expectation_scores": [1, 1, 1],
            "expected_action_scores": [1, 1, 1],
            "overall_quality": "Pass",
            "analysis": "Deterministic smoke-test judge response."
        }, ensure_ascii=False)

    if '"discovery"' in prompt and '"grounding"' in prompt:
        return json.dumps({
            "discovery": {
                "covered": ["F1", "F2", "F3"],
                "missed": []
            },
            "accuracy": {
                "correct": ["F1", "F2", "F3"],
                "incorrect": []
            },
            "grounding": {
                "total_claims": 3,
                "grounded_claims": [
                    {"claim": "Late delivery", "basis": "F1"},
                    {"claim": "Deposit paid", "basis": "F2"},
                    {"claim": "Defect evidence exists", "basis": "F3"}
                ],
                "ungrounded_claims": []
            }
        }, ensure_ascii=False)

    return json.dumps({"analysis": "Deterministic smoke-test judge response."}, ensure_ascii=False)


def mock_call_model(model_name, messages):
    """Return a deterministic local response with the same call_model contract."""
    lower = (model_name or "").strip().lower()
    if "judge" in lower or "eval" in lower:
        content = _mock_judge_response(messages)
    elif "client" in lower:
        content = _mock_client_response(messages)
    else:
        content = json.dumps(_mock_lawyer_response(messages), ensure_ascii=False)
    return {"mock": True, "model": model_name, "content": content}, content


# ── Provider resolution ──────────────────────────────────────────────────────

def resolve_logical_provider(model_name: str) -> str:
    """Resolve the logical provider for a model.

    Used for recusal logic in Panel of Judges — two models from the same
    vendor should recuse.

    Returns:
        Provider name (e.g. 'openai', 'anthropic', 'gemini').
    """
    lower = (model_name or "").strip().lower()
    for prefix, provider in MODEL_ROUTING.items():
        if lower.startswith(prefix.lower()):
            return provider
    return "openai"


def resolve_provider(model_name: str) -> tuple:
    """Resolve provider name, API key, and base URL for a model.

    Checks LOCALHOST_MODELS first (when USE_LOCALHOST is True), then
    MODEL_ROUTING, then falls back to OpenAI.

    Returns:
        (provider_name, api_key, base_url)
    """
    lower = (model_name or "").strip().lower()

    if _use_localhost():
        for entry in LOCALHOST_MODELS:
            if lower.startswith(entry.lower()):
                cfg = PROVIDERS["localhost"]
                key = os.environ.get(cfg["api_key_env"]) or ""
                url = os.environ.get(cfg["base_url_env"]) or ""
                return "localhost", key, url

    for prefix, provider in MODEL_ROUTING.items():
        if lower.startswith(prefix.lower()):
            cfg = PROVIDERS[provider]
            key = os.environ.get(cfg["api_key_env"]) or ""
            url = os.environ.get(cfg["base_url_env"]) or ""
            return provider, key, url

    cfg = PROVIDERS["openai"]
    key = os.environ.get(cfg["api_key_env"]) or ""
    url = os.environ.get(cfg["base_url_env"]) or ""
    return "openai", key, url


# ── Main entry point ─────────────────────────────────────────────────────────

def call_model(model_name, messages, **kwargs):
    """Call an LLM model. Only model_name and messages are required.

    Per-model custom parameters (thinking mode, reasoning effort, …) are
    loaded automatically from MODEL_PARAMS.  Any **kwargs not in the standard
    set (temperature/max_tokens/top_p/seed/stop/timeout/retries) are treated
    as extra payload fields and override MODEL_PARAMS.

    Args:
        model_name: Model snapshot name (e.g. "gpt-5.2-2025-12-11", "claude-4.6-opus").
        messages: OpenAI-format message list [{"role": ..., "content": ...}, ...].
        **kwargs: Optional overrides —
            temperature (float):  Omitted if not specified (API default).
            max_tokens  (int):    Default DEFAULT_MAX_TOKENS.
            top_p       (float):  Default None (omitted from payload).
            seed        (int):    Default None.
            stop        (str|list): Default None.
            timeout     (int):    HTTP timeout in seconds.  Default DEFAULT_TIMEOUT.
            retries     (int):    Retry count on network errors. Default DEFAULT_RETRIES.
            <any other key>:      Merged into API payload (overrides MODEL_PARAMS).

    Returns:
        (response_dict, content_text) tuple.

    Examples:
        # Simplest — MODEL_PARAMS auto-applied:
        call_model("qwen-3.5-thinking-max", messages)

        # Override a model default:
        call_model("gpt-5.2-2025-12-11", messages, reasoning_effort="low")

        # One-off custom param not in MODEL_PARAMS:
        call_model("gpt-5-2025-08-07", messages, response_format={"type": "json_object"})
    """
    model_name = normalize_model_name(model_name) or ""
    if is_mock_model(model_name):
        return mock_call_model(model_name, messages)

    provider, api_key, base_url = resolve_provider(model_name)

    timeout = kwargs.get("timeout") if kwargs.get("timeout") is not None else DEFAULT_TIMEOUT
    retries = kwargs.get("retries") if kwargs.get("retries") is not None else DEFAULT_RETRIES

    # ── Standard params ──────────────────────────────────────────────────
    params = {"model": model_name}
    temp = kwargs.get("temperature")
    if temp is not None:
        params["temperature"] = temp
    mt = kwargs.get("max_tokens")
    params["max_tokens"] = mt if mt is not None else default_max_tokens_for_model(model_name)
    for k in ("top_p", "seed", "stop"):
        if kwargs.get(k) is not None:
            params[k] = kwargs[k]

    # ── Extra body: MODEL_PARAMS defaults + caller overrides ─────────────
    extra_body = dict(MODEL_PARAMS.get(model_name, {}))
    for k, v in kwargs.items():
        if k not in _STANDARD_KWARGS and v is not None:
            extra_body[k] = v
    # reasoning_effort is only valid for reasoning models (gpt-5, o1, o3)
    if "reasoning_effort" in extra_body and not use_responses_api(model_name):
        del extra_body["reasoning_effort"]

    # ── Dispatch to the correct provider call function ───────────────────
    # ── Apply API model name override (canonical name stays in logs/files)
    if model_name in MODEL_API_NAME:
        params = dict(params)
        params["model"] = MODEL_API_NAME[model_name]

    if provider == "anthropic":
        resp = call_anthropic_api(
            base_url, api_key, params, messages, timeout, retries,
            extra_body=extra_body,
        )
        return resp, extract_content_from_anthropic(resp)

    if use_responses_api(model_name):
        try:
            resp = call_responses_api(
                base_url, api_key, params, messages, timeout, retries,
                extra_body=extra_body,
            )
            content = extract_content_from_responses(resp)
            if not content or not content.strip():
                raise ValueError("Responses API returned empty content, falling back")
            return resp, content
        except Exception:
            resp = call_chat_completion(
                base_url, api_key, params, messages, timeout, retries,
                extra_body=extra_body,
            )
            return resp, extract_content(resp)

    resp = call_chat_completion(
        base_url, api_key, params, messages, timeout, retries,
        extra_body=extra_body,
    )
    return resp, extract_content(resp)


# ── Legacy compatibility ─────────────────────────────────────────────────────

def make_legacy_call_fn():
    """Create (call_fn, extract_fn) pair that routes through call_model.

    Used by POMDPEngine and StrategyClassifier which expect the old-style
    signature:  call_fn(base_url, api_key, params, messages, timeout, retries)

    The returned call_fn ignores the passed base_url/api_key and dispatches
    through call_model (→ resolve_provider → MODEL_PARAMS) so that all
    provider-specific logic is applied correctly.

    Returns:
        (call_fn, extract_fn) — pass both to the POMDP/strategy callers.
    """
    _CONTENT_KEY = "__routed_content__"

    def _call_fn(_base_url, _api_key, params, messages, timeout, retries=0):
        model = (params.get("model") or "").strip()
        kw = {k: v for k, v in params.items() if k != "model" and v is not None}
        kw["timeout"] = timeout
        kw["retries"] = retries
        resp, content = call_model(model, messages, **kw)
        resp[_CONTENT_KEY] = content
        return resp

    def _extract_fn(response):
        stored = response.pop(_CONTENT_KEY, None)
        if stored is not None:
            return stored
        return extract_content(response)

    return _call_fn, _extract_fn


def model_key_and_url(model_name, default_key, default_base_url):
    """Deprecated — use resolve_provider() instead."""
    _, key, url = resolve_provider(model_name)
    return key or default_key, url or default_base_url


# ── Model role configuration ─────────────────────────────────────────────────

_models_config_cache = None


def _default_params_dir():
    return str(CONFIG_DIR)


def load_models_config(params_dir: str = None) -> dict:
    """Load config/models.json — the single source of truth for model role assignments.

    Returns a dict with keys: client_model, lawyer_model, eval_model.
    Result is cached after first load.
    """
    global _models_config_cache
    if _models_config_cache is not None:
        return _models_config_cache

    path = os.path.join(params_dir or _default_params_dir(), "models.json")
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"models.json must be a JSON object: {path}")

    cfg = {}
    for key in ("client_model", "lawyer_model", "eval_model"):
        raw = (obj.get(key) or "").strip()
        cfg[key] = normalize_model_name(raw) if raw else ""

    _models_config_cache = cfg
    return cfg


def get_model(role: str, params_dir: str = None) -> str:
    """Get the configured model name for a role.

    Args:
        role: One of "client", "lawyer", "eval".

    Returns:
        Model snapshot name (e.g. "gpt-5-2025-08-07", "gpt-5.2-2025-12-11").
    """
    key = role if role.endswith("_model") else f"{role}_model"
    cfg = load_models_config(params_dir)
    model = cfg.get(key)
    if not model:
        raise ValueError(f"models.json is missing required key: {key}")
    return model
