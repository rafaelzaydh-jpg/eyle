#!/usr/bin/env python3
"""Canonical response decoder for the Eyle Adapter boundary.

Eyle accepts one transport envelope: OpenAI-compatible Chat Completions from
the local Adapter. Provider/model-specific translation belongs behind the
Adapter, never in Core.
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
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    response_id: str | None = None

    def usable_text(self) -> str:
        return self.content if self.content.strip() else ""


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _openai_usage(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None, None, None
    prompt = _int_or_none(usage.get("prompt_tokens"))
    completion = _int_or_none(usage.get("completion_tokens"))
    total = _int_or_none(usage.get("total_tokens"))
    details = usage.get("prompt_tokens_details")
    cached_candidates = [
        _int_or_none(details.get("cached_tokens")) if isinstance(details, dict) else None,
        _int_or_none(usage.get("prompt_cache_hit_tokens")),
        _int_or_none(usage.get("cached_prompt_tokens")),
        _int_or_none(usage.get("cached_tokens")),
    ]
    cached_values = [value for value in cached_candidates if value is not None and value >= 0]
    cached = min(prompt, max(cached_values)) if cached_values and prompt is not None else (max(cached_values) if cached_values else None)
    completion_details = usage.get("completion_tokens_details")
    reasoning = _int_or_none(completion_details.get("reasoning_tokens")) if isinstance(completion_details, dict) else None
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, cached, completion, total, reasoning


def normalize_openai_chat_response(payload: Any, *, streaming: bool = False) -> NormalizedModelResponse:
    if not isinstance(payload, dict):
        raise ResponseEnvelopeError("OpenAI-compatible response must be a JSON object")
    choices = payload.get("choices")
    prompt, cached, completion, total_tokens, reasoning_tokens = _openai_usage(payload)
    # DeepSeek/OpenAI-compatible streaming may emit a final usage-only chunk
    # (choices=[] when stream_options.include_usage=true). It is physical
    # accounting, not malformed model output.
    if streaming and isinstance(choices, list) and not choices and isinstance(payload.get("usage"), dict):
        return NormalizedModelResponse(
            content="", streaming=True, finish_reason=None,
            prompt_tokens=prompt, cached_prompt_tokens=cached,
            completion_tokens=completion, total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            model=str(payload.get("model")) if payload.get("model") is not None else None,
            response_id=str(payload.get("id")) if payload.get("id") is not None else None,
        )
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
    return NormalizedModelResponse(
        content=content,
        streaming=streaming,
        finish_reason=str(choice.get("finish_reason")) if choice.get("finish_reason") is not None else None,
        prompt_tokens=prompt,
        cached_prompt_tokens=cached,
        completion_tokens=completion,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
        response_id=str(payload.get("id")) if payload.get("id") is not None else None,
    )

