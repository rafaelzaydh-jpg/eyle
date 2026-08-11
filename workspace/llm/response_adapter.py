#!/usr/bin/env python3
"""Canonical response decoders for the two supported Eyle LLM transports.

The runtime supports exactly two backend envelopes: OpenAI-compatible Chat
Completions and Ollama ``/api/chat``.  The decoders do not guess alternate
provider shapes, recover partial JSON, or promote reasoning into executable
content.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ResponseEnvelopeError(ValueError):
    """The backend response does not match the configured transport."""


@dataclass(frozen=True)
class NormalizedModelResponse:
    content: str = ""
    streaming: bool = False
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    response_id: str | None = None

    def usable_text(self) -> str:
        return self.content if self.content.strip() else ""


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _openai_usage(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None, None
    prompt = _int_or_none(usage.get("prompt_tokens"))
    completion = _int_or_none(usage.get("completion_tokens"))
    details = usage.get("prompt_tokens_details")
    cached = _int_or_none(details.get("cached_tokens")) if isinstance(details, dict) else None
    completion_details = usage.get("completion_tokens_details")
    reasoning = _int_or_none(completion_details.get("reasoning_tokens")) if isinstance(completion_details, dict) else None
    return prompt, cached, completion, reasoning


def normalize_openai_chat_response(payload: Any, *, streaming: bool = False) -> NormalizedModelResponse:
    if not isinstance(payload, dict):
        raise ResponseEnvelopeError("OpenAI-compatible response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ResponseEnvelopeError("OpenAI-compatible response requires choices[0]")
    choice = choices[0]
    key = "delta" if streaming else "message"
    block = choice.get(key)
    if not isinstance(block, dict):
        raise ResponseEnvelopeError(f"OpenAI-compatible response requires choices[0].{key}")
    content = block.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ResponseEnvelopeError("OpenAI-compatible message content must be a string")
    prompt, cached, completion, reasoning_tokens = _openai_usage(payload)
    return NormalizedModelResponse(
        content=content,
        streaming=streaming,
        finish_reason=str(choice.get("finish_reason")) if choice.get("finish_reason") is not None else None,
        prompt_tokens=prompt,
        cached_prompt_tokens=cached,
        completion_tokens=completion,
        reasoning_tokens=reasoning_tokens,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
        response_id=str(payload.get("id")) if payload.get("id") is not None else None,
    )


def normalize_ollama_chat_response(payload: Any, *, streaming: bool = False) -> NormalizedModelResponse:
    if not isinstance(payload, dict):
        raise ResponseEnvelopeError("Ollama response must be a JSON object")
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ResponseEnvelopeError("Ollama response requires message")
    content = message.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ResponseEnvelopeError("Ollama message content must be a string")
    return NormalizedModelResponse(
        content=content,
        streaming=streaming,
        finish_reason=str(payload.get("done_reason")) if payload.get("done_reason") is not None else None,
        prompt_tokens=_int_or_none(payload.get("prompt_eval_count")),
        completion_tokens=_int_or_none(payload.get("eval_count")),
        model=str(payload.get("model")) if payload.get("model") is not None else None,
    )
