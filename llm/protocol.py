"""Provider-neutral Eyle->Adapter request protocol.

Eyle always sends its tolerant cognition wire schema to the local Adapter. The
Adapter alone chooses upstream structured-output transport. Core never branches
on a provider/model name or upstream JSON mechanism.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CanonicalPrompt:
    """Stable prefix + dynamic suffix with deterministic serialization."""

    stable: dict[str, Any]
    dynamic: dict[str, Any]

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @property
    def stable_text(self) -> str:
        return self._encode(self.stable)

    @property
    def dynamic_text(self) -> str:
        return self._encode(self.dynamic)

    @property
    def wire_text(self) -> str:
        # Canonical flattened representation for accounting/debug/tests. Provider
        # transports still send stable and dynamic packets as distinct messages.
        return self._encode({**self.stable, **self.dynamic})

    @property
    def stable_hash(self) -> str:
        return hashlib.sha256(self.stable_text.encode("utf-8")).hexdigest()

    def messages(self, system_prompt: str) -> list[dict[str, str]]:
        # One canonical OpenAI-compatible shape is sent to the local Adapter.
        # Keeping the stable packet as a distinct earlier message gives adapters
        # a clean cache boundary without changing its semantic content.
        return [
            {"role": "system", "content": str(system_prompt or "")},
            {"role": "user", "content": self.stable_text},
            {"role": "user", "content": self.dynamic_text},
        ]

    def __str__(self) -> str:
        return self.wire_text


def prompt_messages(system_prompt: str, prompt: Any) -> list[dict[str, str]]:
    if isinstance(prompt, CanonicalPrompt):
        return prompt.messages(system_prompt)
    return [
        {"role": "system", "content": str(system_prompt or "")},
        {"role": "user", "content": str(prompt or "")},
    ]


def provider_policy(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return Eyle's fixed local-Adapter wire policy.

    Provider discovery, cache negotiation and structured-mode selection are not
    Eyle concerns. The bundled Adapter owns one explicitly configured provider
    profile; this function only exposes transport/reasoning metadata.
    """
    llm = (config or {}).get("llm") or {}
    return {
        "transport": "adapter_openai_chat",
        "structured_output": "adapter_wire_json_schema",
        "cache_mode": "provider_implicit",
        "cache_warmup": False,
        "reasoning_mode": str(llm.get("reasoning_mode") or "off"),
    }
