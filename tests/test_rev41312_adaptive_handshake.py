import json
import urllib.error
from pathlib import Path

import llm.executar as llm_mod
from llm import capabilities
from llm.structured import (
    StructuredResponseError, json_schema_response_format,
    mandatory_top_level_keys, parse_profile_response, retry_instruction,
)


class _FakeResponse:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._bytes


def _config():
    return {
        "llm": {
            "base_url": "http://localhost:8080",
            "model": "model-a",
            "openai_compatible": True,
            "temperature": 0.2,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 120,
            "retry_max_attempts": 1,
            "agent_retry_max_attempts": 1,
            "max_concurrent_requests": 1,
        },
        "_runtime_agent_budget": {
            "max_llm_calls": 12,
            "max_completion_tokens": 9000,
            "max_total_tokens": 105000,
            "max_prompt_tokens": 96000,
            "llm_calls": 0,
            "generated_tokens": 0,
        },
    }


def _agent_json(answer="ok"):
    return json.dumps({
        "action": "final", "tool_calls": None, "patches": None,
        "needs_user": None, "final": {"answer": answer, "evidence_ids": [], "limitations": []},
        "workspace_scope": {"mode": "none", "reason": "transport fixture is workspace-independent"},
        "investigation": [],
    })


def _payload(req):
    return json.loads(req.data.decode("utf-8")) if req.data else {}


def _is_schema_probe(payload):
    fmt = payload.get("response_format") or {}
    js = fmt.get("json_schema") or {}
    return fmt.get("type") == "json_schema" and js.get("name") == "eyle_capability_probe"


def _schema_probe_response(payload, *, enforced=True):
    schema = ((payload.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
    nonce = (((schema.get("properties") or {}).get("schema_probe") or {}).get("enum") or [""])[0]
    if enforced:
        content = json.dumps({"schema_probe": nonce, "items": [], "optional": None})
    else:
        content = json.dumps({"prompt_probe": nonce})
    return _FakeResponse({"choices": [{"message": {"content": content}}]})


def test_contract_schema_and_local_parser_share_one_profile_definition():
    fmt = json_schema_response_format("claim_verifier")
    schema = fmt["json_schema"]["schema"]
    assert tuple(schema["required"]) == mandatory_top_level_keys("claim_verifier")
    parsed = parse_profile_response(
        {"claims": [], "findings": [], "semantic_gaps": []}, "claim_verifier"
    )
    assert set(parsed) == set(schema["required"])


def test_json_schema_is_selected_only_when_behaviorally_enforced(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        payload = _payload(req); calls.append(payload)
        if _is_schema_probe(payload):
            return _schema_probe_response(payload, enforced=True)
        return _FakeResponse({"choices": [{"message": {"content": _agent_json()}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    config = _config()
    result = llm_mod._chamar_llm("system", "user", config, perfil="agent")
    assert result["final"]["answer"] == "ok"
    assert config["_runtime_agent_budget"]["structured_capability"]["mode"] == "json_schema"
    assert config["_runtime_agent_budget"]["administrative_llm_calls"] == 1
    assert config["_runtime_agent_budget"]["llm_calls"] == 1
    actual = calls[-1]
    assert actual["response_format"]["type"] == "json_schema"
    assert actual["response_format"]["json_schema"]["name"] == "eyle_agent_decision"


def test_schema_accepted_but_ignored_falls_to_json_object(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        payload = _payload(req); calls.append(payload)
        if _is_schema_probe(payload):
            return _schema_probe_response(payload, enforced=False)
        fmt = payload.get("response_format") or {}
        messages = payload.get("messages") or []
        user = str((messages[-1] if messages else {}).get("content") or "")
        if fmt.get("type") == "json_object" and "PROBE-" in user:
            return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})
        return _FakeResponse({"choices": [{"message": {"content": _agent_json()}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    config = _config()
    result = llm_mod._chamar_llm("system", "user", config, perfil="agent")
    assert result["final"]["answer"] == "ok"
    assert config["_runtime_agent_budget"]["structured_capability"]["mode"] == "json_object"
    assert config["_runtime_agent_budget"]["administrative_llm_calls"] == 2
    assert calls[-1]["response_format"] == {"type": "json_object"}


def test_prompt_mode_is_official_when_native_structured_modes_are_unavailable(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        payload = _payload(req); calls.append(payload)
        fmt = payload.get("response_format")
        if isinstance(fmt, dict):
            raise urllib.error.HTTPError(req.full_url, 400, "unsupported", {}, None)
        messages = payload.get("messages") or []
        user = str((messages[-1] if messages else {}).get("content") or "")
        if "prompt_probe" in user and "Return exactly one JSON object" in user:
            nonce = user.split('{"prompt_probe":"', 1)[-1].split('"}', 1)[0]
            return _FakeResponse({"choices": [{"message": {"content": json.dumps({"prompt_probe": nonce})}}]})
        return _FakeResponse({"choices": [{"message": {"content": _agent_json()}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    config = _config()
    result = llm_mod._chamar_llm("system", "user", config, perfil="agent")
    assert result["final"]["answer"] == "ok"
    assert config["_runtime_agent_budget"]["structured_capability"]["mode"] == "prompt"
    assert "response_format" not in calls[-1]


def test_persisted_mode_is_tested_first_after_process_restart(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        payload = _payload(req); calls.append(payload)
        if _is_schema_probe(payload):
            return _schema_probe_response(payload, enforced=True)
        return _FakeResponse({"choices": [{"message": {"content": _agent_json()}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    llm_mod._chamar_llm("system", "user", _config(), perfil="agent")
    first_count = len(calls)
    assert Path(capabilities.cache_path()).exists()

    capabilities.reset_process_cache()
    llm_mod._chamar_llm("system", "user", _config(), perfil="agent")
    restart_calls = calls[first_count:]
    assert len(restart_calls) == 2  # one cached-mode probe + one real request
    assert _is_schema_probe(restart_calls[0])
    assert restart_calls[1]["response_format"]["type"] == "json_schema"


def test_structural_failure_revalidates_and_renegotiates_changed_connection(monkeypatch):
    calls = []
    schema_probe_count = 0
    actual_count = 0

    def fake(req, timeout=None):
        nonlocal schema_probe_count, actual_count
        payload = _payload(req); calls.append(payload)
        if _is_schema_probe(payload):
            schema_probe_count += 1
            # Initial handshake succeeds. Revalidation later proves schema no longer enforced.
            return _schema_probe_response(payload, enforced=(schema_probe_count == 1))
        fmt = payload.get("response_format") or {}
        messages = payload.get("messages") or []
        user = str((messages[-1] if messages else {}).get("content") or "")
        if fmt.get("type") == "json_object" and "PROBE-" in user:
            return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})
        actual_count += 1
        if actual_count == 1:
            return _FakeResponse({"choices": [{"message": {"content": '{"action":"final"}'}}]})
        return _FakeResponse({"choices": [{"message": {"content": _agent_json("recovered")}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    config = _config()
    result = llm_mod._chamar_llm("system", "user", config, perfil="agent")
    assert result["final"]["answer"] == "recovered"
    assert config["_runtime_agent_budget"]["structured_capability"]["mode"] == "json_object"
    assert config["_runtime_agent_budget"]["structured_capability"]["source"] == "renegotiated"
    assert config["_runtime_agent_budget"]["llm_calls"] == 2
    assert config["_runtime_agent_budget"]["administrative_llm_calls"] >= 4


def test_retry_instruction_names_observed_and_missing_keys():
    error = StructuredResponseError(
        "CLAIM_REVIEW_MISSING_KEYS",
        "missing top-level field(s): findings, semantic_gaps",
    )
    text = retry_instruction("claim_verifier", error, {"claims": []})
    assert "Observed top-level keys: claims" in text
    assert "Missing mandatory top-level keys: findings, semantic_gaps" in text
    assert "Use [] when an array has no items" in text
