"""Select OpenAI vs Anthropic for vision + translation. Set LLM_PROVIDER=openai|anthropic;
when unset, Anthropic is used if ANTHROPIC_API_KEY is set, else OpenAI if OPENAI_API_KEY is set."""
from __future__ import annotations

import json
import os
from typing import Any, Literal

Provider = Literal["openai", "anthropic"]


def resolve_llm_provider() -> Provider | None:
    pref = os.environ.get("LLM_PROVIDER", "").strip().lower()
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if pref == "openai":
        return "openai" if has_openai else None
    if pref == "anthropic":
        return "anthropic" if has_anthropic else None
    if has_anthropic:
        return "anthropic"
    if has_openai:
        return "openai"
    return None


def llm_configured() -> bool:
    return resolve_llm_provider() is not None


def openai_model(*, dedicated_env: str, shared_env: str, default: str) -> str:
    """Model name when provider is OpenAI: dedicated env, else shared env if not a Claude id."""
    if os.environ.get(dedicated_env, "").strip():
        return os.environ[dedicated_env].strip()
    shared = os.environ.get(shared_env, "").strip()
    if shared and not shared.startswith("claude"):
        return shared
    return default


def openai_tool_call(
    *,
    model: str,
    system: str,
    user_content: str | list[dict[str, Any]],
    tool_name: str,
    tool_description: str,
    parameters: dict,
    max_tokens: int,
    client=None,
) -> tuple[dict | None, str | None]:
    """One OpenAI chat completion with a forced function/tool call → parsed JSON args."""
    from openai import OpenAI

    client = client or OpenAI()
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_description,
                    "parameters": parameters,
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": tool_name}},
    )
    choice = resp.choices[0]
    finish = choice.finish_reason
    if choice.message.tool_calls:
        raw = choice.message.tool_calls[0].function.arguments
        parsed = json.loads(raw)
        return (parsed if isinstance(parsed, dict) else None), finish
    return None, finish
