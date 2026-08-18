"""Provider-neutral Eyle->Adapter request protocol.

Eyle sends its current cognition JSON Schema to the local Adapter. The Adapter
owns provider connection and mechanical JSON conformance; Eyle Core owns ECC,
Memory, Task and tool semantics. Core never branches on provider/model names.
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
        """Serialize physical context in causal order and keep the active request last.

        Stable runtime/catalog state remains an early cacheable user message. The
        recent conversation is transported using its native user/assistant roles,
        while non-conversational dynamic state stays in one compact Runtime packet.
        The current request is emitted exactly once as the final user message.
        """
        dynamic = dict(self.dynamic)
        current_request = str(dynamic.pop("current_request", "") or "")
        conversation = dynamic.pop("conversation", None)
        conversation_messages = []
        conversation_state = {}
        if isinstance(conversation, dict):
            conversation_state = {k: v for k, v in conversation.items() if k != "messages"}
            for item in conversation.get("messages") or []:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                content = str(item.get("content") or "")
                if isinstance(item.get("execution_failure"), dict) and item["execution_failure"]:
                    content += "\n[execution_failure=" + self._encode(item["execution_failure"]) + "]"
                conversation_messages.append({"role": role, "content": content})
        if conversation_state:
            dynamic["conversation_state"] = conversation_state

        messages = [
            {"role": "system", "content": str(system_prompt or "")},
            {"role": "user", "content": self.stable_text},
        ]
        if dynamic:
            messages.append({"role": "user", "content": self._encode(dynamic)})
        messages.extend(conversation_messages)
        # The active request is the causal/semantic frontier. Even an empty request
        # is explicit rather than allowing the prior assistant turn to become last.
        messages.append({"role": "user", "content": current_request})
        return messages

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
