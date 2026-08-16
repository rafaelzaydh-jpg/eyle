from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

# Make the Adapter suite runnable both inside Eyle/server/ and from a standalone
# Adapter archive, independent of pytest rootdir/import-mode decisions.
_THIS_DIR = Path(__file__).resolve().parents[1]
_EMBEDDED_ROOT = _THIS_DIR.parent
if str(_EMBEDDED_ROOT) not in sys.path:
    sys.path.insert(0, str(_EMBEDDED_ROOT))
try:
    from server import server as server  # embedded as Eyle/server/server.py
except (ImportError, ModuleNotFoundError):
    spec = importlib.util.spec_from_file_location("eyle_adapter_server", _THIS_DIR / "server.py")
    if spec is None or spec.loader is None:
        raise ImportError("unable to load Adapter server.py")
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)


FIXTURE = Path(__file__).parent / "fixtures" / "eyle_rev281_ecc_schema.json"


def arbitrary_client_schema():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def payload(schema=None, *, model="provider-model-1"):
    schema = schema or arbitrary_client_schema()
    return {
        "model": model,
        "messages": [{"role": "system", "content": "caller semantics"}, {"role": "user", "content": "Do the task."}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "client_wire", "strict": False, "schema": schema}},
        "stream": False,
    }


def completion(content, prompt=100, completion_tokens=10, cached=64):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "provider-model-1",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self.content = json.dumps(data).encode()

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://upstream.invalid")
            response = httpx.Response(self.status_code, request=request, content=self.content)
            raise httpx.HTTPStatusError("upstream error", request=request, response=response)


class FakeClient:
    def __init__(self, *, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    async def post(self, url, headers=None, json=None):
        self.post_calls.append((url, headers, json))
        if not self.posts:
            raise AssertionError("unexpected upstream POST")
        item = self.posts.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get(self, url, headers=None):
        self.get_calls.append((url, headers))
        if not self.gets:
            raise AssertionError("unexpected upstream GET")
        item = self.gets.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def parsed_response(response):
    body = json.loads(response.body)
    return body, json.loads(body["choices"][0]["message"]["content"])


def request_from(host: str, headers=None):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/v1/models", "headers": raw_headers, "client": (host, 12345), "server": ("127.0.0.1", 8080), "scheme": "http", "query_string": b""})


def reset_transport_cache():
    server._STRUCTURED_UNSUPPORTED_CACHE.clear()


def test_env_is_loaded_from_adapter_directory():
    assert server.ENV_FILE == Path(server.__file__).resolve().parent / ".env"


def test_adapter_source_has_no_eyle_semantic_grammar():
    source = Path(server.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("on_success", "memory_delta", "epistemic", "remember", "explorar", "construir", "concluir"):
        assert forbidden not in source


def test_model_selection_never_forwards_auto(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, model_override=None, default_model="auto"))
    assert server.model_for({"model": "auto"}) == "auto"
    assert server.model_for({"model": "real-model"}) == "real-model"
    monkeypatch.setattr(server, "S", replace(server.S, model_override=None, default_model="configured-model"))
    assert server.model_for({"model": "auto"}) == "configured-model"
    monkeypatch.setattr(server, "S", replace(server.S, model_override="forced-model", default_model="auto"))
    assert server.model_for({"model": "anything"}) == "forced-model"


def test_auto_starts_with_native_json_schema_without_semantic_injection(monkeypatch):
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto"))
    incoming = payload()
    body, _, schema = server._prepare_upstream(incoming, resolved_model="provider-model-1")
    assert schema == arbitrary_client_schema()
    assert body["response_format"] == incoming["response_format"]
    assert body["messages"] == incoming["messages"]
    assert server._first_structured_mode("provider-model-1") == "native_json_schema"


def test_json_object_and_prompt_json_add_only_generic_json_instruction(monkeypatch):
    for mode in ("json_object", "prompt_json"):
        monkeypatch.setattr(server, "S", replace(server.S, structured_mode=mode))
        body, _, _ = server._prepare_upstream(payload(), resolved_model="provider-model-1")
        joined = "\n".join(str(m.get("content") or "") for m in body["messages"])
        assert "one json object" in joined.lower()
        for forbidden in ("memory_delta", "explorar", "construir", "concluir", "on_success"):
            assert forbidden not in joined.lower()
        if mode == "json_object":
            assert body["response_format"] == {"type": "json_object"}
        else:
            assert "response_format" not in body


def test_json_recovery_is_syntactic_only_and_preserves_semantics():
    raw = {"type": "finish", "answer": "ok", "whatever": {"x": 1}}
    value, errors, steps = server.normalize_structured(completion(json.dumps(raw)), arbitrary_client_schema())
    assert errors == []
    assert steps == []
    assert value == raw


def test_local_normalizer_recovers_fence_prose_and_python_literal_without_llm():
    samples = [
        '```json\n{"x":1}\n```',
        'Aqui está: {"x":1} fim',
        "{'x': 1, 'ok': True}",
    ]
    for text in samples:
        value, errors, steps = server.normalize_structured(completion(text), arbitrary_client_schema())
        assert errors == []
        assert value["x"] == 1
        assert steps or text.startswith("{'x'")


def test_parseable_but_client_schema_invalid_is_returned_without_repair(monkeypatch):
    """Adapter does not own client semantic/schema validity anymore."""
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto", model_override="provider-model-1"))
    semantically_invalid = {"decision": {"type": "whatever"}, "unexpected": 7}
    client = FakeClient(posts=[FakeResponse(completion(json.dumps(semantically_invalid)))])
    response = asyncio.run(server.execute_structured(client, payload(), "req-semantic"))
    assert response.status_code == 200
    assert len(client.post_calls) == 1
    _, value = parsed_response(response)
    assert value == semantically_invalid
    assert response.headers["x-eyle-structured-repairs"] == "0"
    assert response.headers["x-eyle-schema-enforcement"] == "adapter_json_valid"
    assert server._first_structured_mode("provider-model-1") == "native_json_schema"


def test_auto_degrades_only_after_technical_provider_rejection_and_caches_rejection(monkeypatch):
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto", model_override="provider-model-1"))
    rejected = FakeResponse({"error": {"message": "unsupported response_format"}}, status=400)
    client = FakeClient(posts=[rejected, FakeResponse(completion('{"x":1}'))])
    response = asyncio.run(server.execute_structured(client, payload(), "req-fallback"))
    assert response.status_code == 200
    assert len(client.post_calls) == 2
    assert client.post_calls[0][2]["response_format"]["type"] == "json_schema"
    assert client.post_calls[1][2]["response_format"] == {"type": "json_object"}
    assert response.headers["x-eyle-structured-upstream-mode"] == "json_object"
    assert server._first_structured_mode("provider-model-1") == "json_object"


def test_auto_can_degrade_json_object_to_prompt_json_only_on_technical_rejection(monkeypatch):
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto", model_override="provider-model-1"))
    # First logical request proves native unsupported but json_object works.
    client1 = FakeClient(posts=[
        FakeResponse({"error": {"message": "no json_schema"}}, status=400),
        FakeResponse(completion('{"x":1}')),
    ])
    asyncio.run(server.execute_structured(client1, payload(), "req-a"))
    assert server._first_structured_mode("provider-model-1") == "json_object"
    # Second request: provider now technically rejects json_object -> prompt_json.
    client2 = FakeClient(posts=[
        FakeResponse({"error": {"message": "no json_object"}}, status=415),
        FakeResponse(completion('{"x":2}')),
    ])
    response = asyncio.run(server.execute_structured(client2, payload(), "req-b"))
    assert response.status_code == 200
    assert "response_format" not in client2.post_calls[1][2]
    assert server._first_structured_mode("provider-model-1") == "prompt_json"


def test_syntax_repair_stays_on_same_strong_mode(monkeypatch):
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto", model_override="provider-model-1"))
    first = completion("not json at all", prompt=80, completion_tokens=5)
    second = completion('{"x":1}', prompt=90, completion_tokens=7)
    client = FakeClient(posts=[FakeResponse(first), FakeResponse(second)])
    response = asyncio.run(server.execute_structured(client, payload(), "req-repair"))
    assert response.status_code == 200
    assert len(client.post_calls) == 2
    assert client.post_calls[0][2]["response_format"]["type"] == "json_schema"
    assert client.post_calls[1][2]["response_format"]["type"] == "json_schema"
    assert client.post_calls[1][2]["temperature"] == 0
    assert response.headers["x-eyle-structured-upstream-mode"] == "native_json_schema"
    assert response.headers["x-eyle-structured-repairs"] == "1"
    assert response.headers["x-eyle-schema-enforcement"] == "adapter_format_repaired"
    body, value = parsed_response(response)
    assert value == {"x": 1}
    assert body["usage"]["prompt_tokens"] == 170
    assert server._first_structured_mode("provider-model-1") == "native_json_schema"


def test_failed_syntax_repair_returns_candidate_200_not_fatal_502(monkeypatch):
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto", model_override="provider-model-1"))
    client = FakeClient(posts=[
        FakeResponse(completion("plain unstructured answer", prompt=80, completion_tokens=5)),
        FakeResponse(completion("still not json", prompt=90, completion_tokens=7)),
    ])
    response = asyncio.run(server.execute_structured(client, payload(), "req-candidate"))
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["choices"][0]["message"]["content"] == "plain unstructured answer"
    assert body["usage"]["completion_tokens"] == 12
    assert response.headers["x-eyle-schema-enforcement"] == "adapter_candidate_unparsed"
    assert response.headers["x-eyle-structured-repairs"] == "1"


def test_model_discovery_reads_upstream_and_caches(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_base_url="http://upstream.local/v1", upstream_api_key="secret", model_discovery_ttl=300))
    server._MODEL_DISCOVERY_CACHE.clear()
    client = FakeClient(gets=[FakeResponse({"object": "list", "data": [{"id": "model-a"}, {"id": "model-b"}]})])
    first = asyncio.run(server.discover_models(client))
    second = asyncio.run(server.discover_models(client))
    assert first == ["model-a", "model-b"]
    assert second == first
    assert len(client.get_calls) == 1
    assert client.get_calls[0][0] == "http://upstream.local/v1/models"
    assert client.get_calls[0][1]["Authorization"] == "Bearer secret"


def test_resolve_model_replaces_auto_with_real_upstream_id(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_base_url="http://upstream.local/v1", model_override=None, default_model="auto"))
    server._MODEL_DISCOVERY_CACHE.clear()
    client = FakeClient(gets=[FakeResponse({"data": [{"id": "loaded-model"}]})])
    result = asyncio.run(server.resolve_model(client, {"model": "auto"}))
    assert result == "loaded-model"


def test_proxy_key_does_not_break_direct_local_eyle(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, proxy_key="abc", proxy_allow_loopback_no_auth=True))
    server.client_auth(request_from("127.0.0.1"))
    with pytest.raises(Exception) as exc:
        server.client_auth(request_from("10.0.0.20"))
    assert getattr(exc.value, "status_code", None) == 401
    server.client_auth(request_from("10.0.0.20", {"Authorization": "Bearer abc"}))


def test_usage_accumulator_never_counts_cached_above_prompt():
    acc = server.UsageAccumulator()
    acc.add(completion("{}", prompt=100, completion_tokens=3, cached=999))
    assert acc.prompt_tokens == 100
    assert acc.cached_prompt_tokens == 100
    assert acc.as_dict()["prompt_cache_miss_tokens"] == 0


def test_timeout_reports_billing_risk(monkeypatch):
    reset_transport_cache()
    monkeypatch.setattr(server, "S", replace(server.S, structured_mode="auto", model_override="provider-model-1"))
    client = FakeClient(posts=[httpx.ReadTimeout("late")])
    response = asyncio.run(server.execute_structured(client, payload(), "req-timeout"))
    assert response.status_code == 504
    body = json.loads(response.body)
    assert body["error"]["type"] == "upstream_timeout"
    assert body["error"]["billing_may_have_occurred"] is True
    assert response.headers["x-eyle-retry-cost-risk"] == "1"


def test_health_declares_transport_only_contract(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S))
    health = asyncio.run(server.health())
    assert health["adapter_profile"] == "eyle-provider-transport-v3"
    assert health["semantic_protocol"] == "client-owned"
    assert "technical provider rejection" in health["structured_auto_policy"]
    assert health["openai_base_url"].endswith("/v1")
    assert server.MAX_UPSTREAM_ATTEMPTS_PER_LOGICAL_CALL == 4


def test_no_local_llm_fallback_is_hardcoded():
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "127.0.0.1:8000" not in source
    assert "127.0.0.1:11434" not in source


def test_models_uses_configured_model_without_upstream_discovery(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server, "S", replace(server.S, upstream_base_url="https://provider.example/v1", default_model="provider-model-1", model_override=None))

    async def forbidden(*args, **kwargs):
        raise AssertionError("discover_models must not be called for explicit DEFAULT_MODEL")

    monkeypatch.setattr(server, "discover_models", forbidden)
    with TestClient(server.app) as client:
        response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "provider-model-1"
    assert response.headers["X-Eyle-Model-Discovery"] == "configured"


def test_rev288_formal_handshake_advertises_transport_only_contract(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server, "S", replace(server.S, upstream_base_url="http://upstream.local/v1", default_model="provider-model-1", model_override=None))
    with TestClient(server.app) as client:
        response = client.get(
            "/v1/eyle/handshake",
            headers={"X-Eyle-Transport-Protocol": server.ADAPTER_TRANSPORT_PROTOCOL},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["handshake_schema"] == "eyle-adapter-handshake-v1"
    assert body["adapter_protocol"] == server.ADAPTER_TRANSPORT_PROTOCOL
    assert body["authority"] == "transport-only"
    assert body["semantic_protocol"] == "client-owned"
    assert body["capabilities"]["json_candidate_passthrough"] is True
    assert body["capabilities"]["syntactic_json_recovery"] is True
    assert response.headers["X-Eyle-Adapter-Protocol"] == server.ADAPTER_TRANSPORT_PROTOCOL


def test_rev288_handshake_rejects_incompatible_declared_protocol(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server, "S", replace(server.S, upstream_base_url="http://upstream.local/v1", default_model="provider-model-1", model_override=None))
    with TestClient(server.app) as client:
        response = client.get(
            "/v1/eyle/handshake",
            headers={"X-Eyle-Transport-Protocol": "wrong-protocol"},
        )
    assert response.status_code == 426
    assert response.json()["error_code"] == "ADAPTER_PROTOCOL_INCOMPATIBLE"
