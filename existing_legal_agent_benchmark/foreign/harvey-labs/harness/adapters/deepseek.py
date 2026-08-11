"""DeepSeek adapter — OpenAI-compatible Chat Completions API.

Reads ``DEEPSEEK_API_KEY`` and (optional) ``DEEPSEEK_BASE_URL`` from the
environment. ``DEEPSEEK_BASE_URL`` defaults to the official DeepSeek API;
set it to a proxy gateway (e.g. Paratera) when needed.

``DEEPSEEK_MAX_TOKENS`` (optional) caps per-turn output; defaults to 65536
(Paratera gateway limit).
"""

import os

import openai

from harness.adapters.baseten import BasetenAdapter

_DEFAULT_MAX_TOKENS = 65536


class DeepSeekAdapter(BasetenAdapter):
    """Adapter for DeepSeek models over the OpenAI-compatible chat API."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not resolved_key:
            raise ValueError("DeepSeek adapter requires DEEPSEEK_API_KEY")
        resolved_base = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
        if max_tokens is None:
            env_cap = os.environ.get("DEEPSEEK_MAX_TOKENS")
            max_tokens = int(env_cap) if env_cap else _DEFAULT_MAX_TOKENS
        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            base_url=resolved_base,
            api_key=resolved_key,
        )
        # Proxy gateways can be slow on long tool-heavy prompts; use a generous timeout.
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url.rstrip("/"),
            timeout=600.0,
            max_retries=0,
        )
