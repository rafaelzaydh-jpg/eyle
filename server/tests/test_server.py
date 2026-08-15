from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx

import server


FIXTURE = Path(__file__).parent / "fixtures" / "eyle_rev252_ecc_schema.json"


def ecc_schema():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def payload(schema=None):
    schema = schema or ecc_schema()
    return {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "Analise."}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "ecc", "schema": schema}},
        "stream": False,
    }


def completion(content, prompt=100, completion=10, hit=64, miss=36):
    return {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
        },
    }


def test_fixture_is_full_rev252_objective_and_memory_contract():
    schema = ecc_schema()
    memory = schema["oneOf"][0]["properties"]["memory"]
    operations = memory["properties"]["operations"]["items"]["oneOf"]
    names = [item["properties"]["op"]["enum"][0] for item in operations]
    assert names == ["remember", "revise", "relate", "archive", "supersede", "retire_relation"]
    remember = operations[0]
    assert set(remember["required"]) == {"op", "scope", "kind", "content"}
    assert "supports" in remember["properties"]
    objective = schema["oneOf"][0]["properties"]["objective"]
    assert objective["properties"]["disposition"]["enum"] == ["unchanged", "updated", "cleared"]
    state_variants = objective["properties"]["state"]["oneOf"]
    state = next(item for item in state_variants if item.get("type") == "object")
    assert set(state["required"]) == {"summary", "status", "children", "constraints"}


def test_deepseek_never_receives_openai_json_schema_and_gets_full_memory_grammar(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_api_key="x"))
    body, _, schema = server.prepare_upstream(payload())
    assert schema is not None
    assert body["response_format"] == {"type": "json_object"}
    assert body["response_format"].get("json_schema") is None
    instruction = body["messages"][0]["content"]
    assert "JSON" in instruction
    assert '"type":"explorar"' in instruction
    assert "explorar.search" not in instruction
    assert '"objective":{"disposition":"unchanged","state":null}' in instruction
    assert '"memory":{"focus":[],"disposition":"unchanged","operations":[]}' in instruction
    assert "MUST include objective" in instruction
    assert "objective.state={" in instruction
    assert "objective.child={" in instruction
    assert "MUST include memory" in instruction
    assert "NOT a fourth action" in instruction
    assert "remember={" in instruction
    assert "scope:world|user" in instruction
    assert "revise={" in instruction
    assert "relate={" in instruction
    assert "retire_relation={" in instruction
    assert "support.material={" in instruction
    assert "support.request={" in instruction
    assert "support.memory={" in instruction
    assert "@alias" in instruction
    # Compact enough to cache cheaply, but complete enough to actually author Memory operations.
    assert len(instruction) < 4300


def test_local_validator_enforces_full_canonical_memory_schema_with_specific_errors():
    valid = completion('{"type":"explorar","operation":"search","arguments":{},"objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"unchanged","operations":[]}}')
    _, errors = server.validate_structured_response(valid, ecc_schema())
    assert errors == []

    invalid_memory = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"updated",'
        '"operations":[{"op":"remember","content":"Agent Core"}]}}'
    )
    _, errors = server.validate_structured_response(invalid_memory, ecc_schema())
    joined = "\n".join(errors)
    assert "$.memory.operations[0].scope" in joined
    assert "$.memory.operations[0].kind" in joined
    assert "not valid under any" not in joined

    inconsistent = completion('{"type":"explorar","operation":"search","arguments":{},"objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"updated","operations":[]}}')
    _, errors = server.validate_structured_response(inconsistent, ecc_schema())
    assert any("requires at least one operation" in item for item in errors)


def test_validator_discriminates_support_kind_instead_of_oneof_umbrella():
    invalid = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"updated","operations":['
        '{"op":"remember","scope":"world","kind":"architecture","content":"x","supports":['
        '{"kind":"material"}]}'
        ']}}'
    )
    _, errors = server.validate_structured_response(invalid, ecc_schema())
    joined = "\n".join(errors)
    assert "material_id" in joined
    assert "not valid under any" not in joined


def test_cache_usage_is_not_double_counted_or_larger_than_prompt():
    acc = server.UsageAccumulator()
    data = completion('{}', prompt=100, completion=3, hit=80, miss=20)
    data["usage"]["prompt_tokens_details"] = {"cached_tokens": 80}
    acc.add(data)
    assert acc.prompt_tokens == 100
    assert acc.cached_prompt_tokens == 80
    assert acc.cache_miss_tokens == 20
    data2 = completion('{}', prompt=50, completion=3, hit=70, miss=0)
    acc.add(data2)
    assert acc.cached_prompt_tokens == 130
    assert acc.cached_prompt_tokens <= acc.prompt_tokens


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self.content = json.dumps(data).encode()

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, headers=None, json=None):
        self.calls.append((url, headers, json))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def test_structured_repair_is_bounded_and_usage_is_aggregated(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_api_key="x", structured_repair_attempts=1))
    invalid = completion('{"type":"explorar","operation":"","arguments":{},"objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"unchanged","operations":[]}}', prompt=100, completion=8, hit=64, miss=36)
    valid = completion('{"type":"explorar","operation":"search","arguments":{},"objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"unchanged","operations":[]}}', prompt=150, completion=9, hit=100, miss=50)
    client = FakeClient([FakeResponse(invalid), FakeResponse(valid)])
    response = asyncio.run(server.execute_structured(client, payload(), "req-1"))
    assert response.status_code == 200
    assert len(client.calls) == 2
    body = json.loads(response.body)
    assert body["usage"]["prompt_tokens"] == 250
    assert body["usage"]["completion_tokens"] == 17
    assert body["usage"]["prompt_cache_hit_tokens"] == 164
    assert response.headers["x-eyle-upstream-attempts"] == "2"
    assert response.headers["x-eyle-structured-repairs"] == "1"


def test_memory_contract_repair_uses_specific_diagnostics_and_preserves_update(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_api_key="x", structured_repair_attempts=1))
    invalid = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"updated",'
        '"operations":[{"op":"remember","content":"AgentSession is state."}]}}',
        prompt=100, completion=80, hit=64, miss=36,
    )
    valid = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"unchanged","state":null},"memory":{"focus":["@session"],"disposition":"updated",'
        '"operations":[{"op":"remember","key":"session","scope":"world","kind":"architecture_component",'
        '"content":"AgentSession is state.","supports":[{"kind":"material","material_id":"mat-1"}]}]}}',
        prompt=160, completion=90, hit=128, miss=32,
    )
    client = FakeClient([FakeResponse(invalid), FakeResponse(valid)])
    response = asyncio.run(server.execute_structured(client, payload(), "req-memory"))
    assert response.status_code == 200
    assert len(client.calls) == 2
    repair_body = client.calls[1][2]
    repair_text = "\n".join(str(m.get("content") or "") for m in repair_body["messages"])
    assert "$.memory.operations[0].scope" in repair_text
    assert "$.memory.operations[0].kind" in repair_text
    assert "do not erase a genuine objective or memory update" in repair_text
    body = json.loads(response.body)
    parsed = json.loads(body["choices"][0]["message"]["content"])
    assert parsed["memory"]["disposition"] == "updated"
    assert parsed["memory"]["operations"][0]["op"] == "remember"
    assert response.headers["x-eyle-upstream-attempts"] == "2"
    assert response.headers["x-eyle-structured-repairs"] == "1"


def test_objective_validator_avoids_oneof_umbrella_and_reports_missing_fields():
    invalid = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"updated","state":'
        '{"summary":"Compound","children":[],"constraints":[]}},'
        '"memory":{"focus":[],"disposition":"unchanged","operations":[]}}'
    )
    _, errors = server.validate_structured_response(invalid, ecc_schema())
    joined = "\n".join(errors)
    assert "$.objective.state" in joined
    assert "status" in joined
    assert "not valid under any" not in joined


def test_objective_contract_repair_preserves_compound_state(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_api_key="x", structured_repair_attempts=1))
    invalid = completion(
        '{"type":"concluir","response":"340","objective":{"disposition":"updated","state":'
        '{"summary":"Compound request","children":[{"key":"calc","description":"Calculate","status":"resolved","outcome":"340"}],"constraints":[]}},'
        '"memory":{"focus":[],"disposition":"unchanged","operations":[]}}',
        prompt=100, completion=70, hit=64, miss=36,
    )
    valid = completion(
        '{"type":"concluir","response":"340","objective":{"disposition":"updated","state":'
        '{"summary":"Compound request","status":"concluded","children":[{"key":"calc","description":"Calculate","status":"resolved","outcome":"340"}],"constraints":[]}},'
        '"memory":{"focus":[],"disposition":"unchanged","operations":[]}}',
        prompt=140, completion=80, hit=96, miss=44,
    )
    client = FakeClient([FakeResponse(invalid), FakeResponse(valid)])
    response = asyncio.run(server.execute_structured(client, payload(), "req-objective"))
    assert response.status_code == 200
    assert len(client.calls) == 2
    repair_text = "\n".join(str(m.get("content") or "") for m in client.calls[1][2]["messages"])
    assert "$.objective.state" in repair_text and "status" in repair_text
    body = json.loads(response.body)
    parsed = json.loads(body["choices"][0]["message"]["content"])
    assert parsed["objective"]["disposition"] == "updated"
    assert parsed["objective"]["state"]["children"][0]["outcome"] == "340"


def test_timeout_is_fail_closed_and_marks_billing_unknown(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_api_key="x", structured_repair_attempts=1))
    client = FakeClient([httpx.ReadTimeout("late")])
    response = asyncio.run(server.execute_structured(client, payload(), "req-2"))
    assert response.status_code == 504
    assert len(client.calls) == 1
    body = json.loads(response.body)
    assert body["error"]["provider_usage_unknown"] is True
    assert body["error"]["billing_may_have_occurred"] is True
    assert response.headers["x-eyle-upstream-usage-unknown"] == "1"


def test_exhausted_contract_returns_usage_and_actionable_errors(monkeypatch):
    monkeypatch.setattr(server, "S", replace(server.S, upstream_api_key="x", structured_repair_attempts=1))
    invalid1 = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"updated",'
        '"operations":[{"op":"remember","content":"x"}]}}', prompt=100, completion=80, hit=60, miss=40,
    )
    invalid2 = completion(
        '{"type":"concluir","response":"ok","objective":{"disposition":"unchanged","state":null},"memory":{"focus":[],"disposition":"updated",'
        '"operations":[{"op":"remember","scope":"world","content":"x"}]}}', prompt=120, completion=70, hit=70, miss=50,
    )
    client = FakeClient([FakeResponse(invalid1), FakeResponse(invalid2)])
    response = asyncio.run(server.execute_structured(client, payload(), "req-3"))
    assert response.status_code == 502
    assert len(client.calls) == 2
    body = json.loads(response.body)
    assert body["error"]["type"] == "structured_contract_unsatisfied"
    assert body["error"]["repairs"] == 1
    assert body["error"]["upstream_attempts"] == 2
    assert any("$.memory.operations[0].kind" in item for item in body["error"]["validation_errors"])
    assert body["usage"]["prompt_tokens"] == 220
    assert body["usage"]["completion_tokens"] == 150
    assert response.headers["x-eyle-usage-prompt-tokens"] == "220"
