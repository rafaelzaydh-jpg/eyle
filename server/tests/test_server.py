import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.requests import Request

from server import server


def payload():
    return {
        "model": "auto",
        "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "answer"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "wire",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {"type": {"type": "string"}, "answer": {"type": "string"}},
                    "required": ["type", "answer"],
                    "additionalProperties": True,
                },
            },
        },
        "max_completion_tokens": 1000,
        "provider_token_budget_remaining": 150000,
        "reasoning_mode": "off",
        "stream": False,
    }


def completion(content, *, total=10, prompt=6, completion_tokens=4):
    return {
        "id": "r1",
        "model": "deepseek-v4-flash",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens, "total_tokens": total},
    }


def request_from(host="127.0.0.1", headers=None):
    raw_headers = [(str(k).lower().encode(), str(v).encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw_headers, "client": (host, 1234), "server": ("127.0.0.1", 8080), "scheme": "http"})


def test_fixed_model_never_uses_incoming_model(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, model="deepseek-v4-flash"))
    assert server.model_for({"model": "auto"}) == "deepseek-v4-flash"
    assert server.model_for({"model": "totally-different"}) == "deepseek-v4-flash"


def test_structured_translation_is_one_fixed_json_object_path(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, model="deepseek-v4-flash"))
    body, headers, schema = server.prepare_upstream(payload())
    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 1000
    assert "max_completion_tokens" not in body
    assert "provider_token_budget_remaining" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert any("JSON" in str(message.get("content")) for message in body["messages"])
    assert schema["required"] == ["type", "answer"]
    assert headers["Content-Type"] == "application/json"


def test_provider_default_does_not_invent_reasoning_knobs():
    p = payload()
    p["reasoning_mode"] = "provider_default"
    body, _, _ = server.prepare_upstream(p)
    assert "thinking" not in body


def test_streaming_forces_provider_usage_chunk():
    p = {"messages": [{"role": "user", "content": "hi"}], "stream": True, "reasoning_mode": "off"}
    body, _, _ = server.prepare_upstream(p)
    assert body["stream_options"]["include_usage"] is True


def test_usage_preserves_provider_total_tokens_even_when_sum_differs():
    usage = server.UsageAccumulator()
    usage.add({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99}})
    assert usage.total_tokens == 99
    assert usage.provider_total_authoritative is True
    assert usage.as_dict()["total_tokens"] == 99


def test_generic_json_recovery_does_not_semantically_alias_fields():
    schema = {"type": "object", "properties": {"type": {"const": "expected"}}, "required": ["type"]}
    value, errors, steps = server.normalize_structured(completion("```json\n{'type':'wrong'}\n```"), schema)
    assert value == {"type": "wrong"}
    assert steps == ["fence_removed"]
    assert errors and "expected" in errors[0]


def test_structured_uses_at_most_one_repair_and_aggregates_usage(monkeypatch):
    calls = []

    async def fake_call_once(client, incoming, request_id, attempt_no, **kwargs):
        calls.append((attempt_no, kwargs))
        if attempt_no == 1:
            data = completion('{"type":"x"}', total=11, prompt=7, completion_tokens=4)
        else:
            data = completion('{"type":"x","answer":"ok"}', total=13, prompt=8, completion_tokens=5)
        return server.AttemptResult(data, 200, "application/json", b"")

    monkeypatch.setattr(server, "call_once", fake_call_once)
    response = asyncio.run(server.execute_structured(object(), payload(), "req"))
    body = json.loads(response.body)
    assert len(calls) == 2
    assert calls[1][1]["repair_instruction"]
    assert body["usage"]["total_tokens"] == 24
    assert response.headers["x-eyle-upstream-attempts"] == "2"
    assert response.headers["x-eyle-structured-repairs"] == "1"


def test_missing_usage_stops_repair_instead_of_guessing_zero(monkeypatch):
    calls = []

    async def fake_call_once(client, incoming, request_id, attempt_no, **kwargs):
        calls.append(attempt_no)
        data = completion('{"type":"x"}')
        data.pop("usage")
        return server.AttemptResult(data, 200, "application/json", b"")

    monkeypatch.setattr(server, "call_once", fake_call_once)
    response = asyncio.run(server.execute_structured(object(), payload(), "req"))
    assert calls == [1]
    assert response.headers["x-eyle-upstream-usage-unknown"] == "1"
    assert response.headers["x-eyle-retry-cost-risk"] == "1"


def test_handshake_is_static_and_declares_no_discovery():
    response = asyncio.run(server.handshake(request_from(headers={"X-Eyle-Transport-Protocol": server.ADAPTER_TRANSPORT_PROTOCOL})))
    body = json.loads(response.body)
    assert body["provider"]["discovery"] is False
    assert body["provider"]["runtime_probing"] is False
    assert body["provider"]["model_policy"] == "fixed_configured"
    assert body["capabilities"]["structured_mode"] == "json_object"
    assert body["limits"]["max_upstream_attempts_per_logical_call"] == 2


def test_remote_proxy_auth_is_not_bypassed(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, proxy_key="abc", proxy_allow_loopback_no_auth=True))
    server.client_auth(request_from("127.0.0.1"))
    with pytest.raises(server.HTTPException):
        server.client_auth(request_from("10.0.0.20"))
    server.client_auth(request_from("10.0.0.20", {"Authorization": "Bearer abc"}))


def test_source_has_no_runtime_provider_negotiation():
    source = Path(server.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "def discover_models", "def resolve_model", "native_json_schema", "prompt_json",
        "model_discovery_cache", "upstream_structured_mode", "upstream_cache_mode",
        "upstream_extra_body_json", "reasoning_control_unconfigured",
    ):
        assert forbidden not in source
    assert server.MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL == 2
