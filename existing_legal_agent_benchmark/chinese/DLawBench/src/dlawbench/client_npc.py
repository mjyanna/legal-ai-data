"""Client NPC for the legal consultation benchmark.

The client is an LLM-based NPC whose behavior is driven by:
1. Case facts (client_belief only — NOT actual_situation)
2. Persona (who + voice from personas.json)
"""
import json

from .api_utils import call_model
from .paths import CONFIG_DIR


def resolve_lang(case):
    """Read case language. Defaults to 'zh' for backward compat with legacy cases."""
    lang = (case or {}).get("language") or "zh"
    return "en" if lang == "en" else "zh"


def _load_template(lang):
    fname = "client_npc_prompt_en.txt" if lang == "en" else "client_npc_prompt.txt"
    path = CONFIG_DIR / fname
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _resolve_localized(value, lang):
    """Pick the language-specific string from a {zh, en} dict, or pass through if str."""
    if isinstance(value, dict):
        return value.get(lang) or value.get("zh") or value.get("en") or ""
    return value or ""


def build_client_prompt(case, persona):
    """Build the client NPC system prompt from case data and persona.

    CRITICAL: actual_situation must NEVER appear in this prompt.
    Only client_belief is included.
    """
    lang = resolve_lang(case)
    template = _load_template(lang)

    facts_lines = []
    for fact in case.get("facts", []):
        facts_lines.append(f"- {fact['client_belief']}")
    facts_block = "\n".join(facts_lines)

    default_name = "Client" if lang == "en" else "客户"
    client_name = default_name
    bg = case.get("background", {})
    if isinstance(bg, dict):
        client_name = bg.get("client_name", default_name)

    return template.format(
        client_name=client_name,
        persona_who=_resolve_localized(persona.get("who"), lang),
        persona_voice=_resolve_localized(persona.get("voice"), lang),
        facts_block=facts_block,
    )


def load_persona(persona_key):
    """Load a persona from personas.json."""
    path = CONFIG_DIR / "personas.json"
    with open(path, "r", encoding="utf-8") as f:
        personas = json.load(f)
    if persona_key not in personas:
        raise ValueError(f"Unknown persona: {persona_key}. Available: {list(personas.keys())}")
    return personas[persona_key]


class ClientNPC:
    """LLM-based client NPC that responds to lawyer questions.

    Stateful: maintains full conversation history so the client remembers
    what was already discussed and does not repeat or contradict itself.
    """

    def __init__(self, case, persona, model, **model_kwargs):
        self.case = case
        self.persona = persona
        self.model = model
        self.model_kwargs = model_kwargs
        self.system_prompt = build_client_prompt(case, persona)
        self.history = []  # [(lawyer_msg, client_response), ...]

    def respond(self, lawyer_message):
        """Generate a response to the lawyer's message.

        Sends full conversation history so the client maintains state.
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        for prev_lawyer, prev_client in self.history:
            messages.append({"role": "user", "content": prev_lawyer})
            messages.append({"role": "assistant", "content": prev_client})
        messages.append({"role": "user", "content": lawyer_message})

        _, response = call_model(self.model, messages, **self.model_kwargs)
        self.history.append((lawyer_message, response))
        return response
