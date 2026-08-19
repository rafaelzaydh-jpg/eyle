from __future__ import annotations

import json

from eyle.core.memory import (
    apply_memory_sidecar,
    materialize_explicit_memory_view,
    memory_activate_result,
)
from eyle.core.session import AgentSession
from eyle.runtime.context_materializer import materialize_runtime_feedback
from eyle.runtime.history import build_prompt_cost_accounting
from eyle.runtime.memory_graph import ingest_chat_message, world_scope
from llm.executar import PROMPT_ECC
from llm.protocol import CanonicalPrompt
from tests.canonical import base_config, standard_registry


def _context(tmp_path):
    return {
        "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory"),
            "world_scope_id": f"workspace:{tmp_path.resolve()}",
        },
    }


def test_rev373_current_request_is_final_provider_message_after_native_conversation():
    prompt = CanonicalPrompt(
        stable={"ecc_operations": {"explorar": []}},
        dynamic={
            "current_request": "oi",
            "conversation": {
                "conversation_id": "conv-1",
                "messages": [
                    {"role": "user", "content": "Você ainda consegue acessar o começo?"},
                    {"role": "assistant", "content": "Sim, consigo."},
                ],
                "history_messages_materialized": 2,
                "history_messages_omitted": 0,
            },
            "runtime_feedback": [],
        },
    )
    messages = prompt.messages("system")
    assert [m["role"] for m in messages[-3:]] == ["user", "assistant", "user"]
    assert messages[-1] == {"role": "user", "content": "oi"}
    assert all("current_request" not in m["content"] for m in messages[:-1])
    # Conversation metadata remains physically available without re-embedding bodies.
    assert any("history_messages_omitted" in m["content"] for m in messages[:-3])


def test_rev373_self_identity_and_non_helpdesk_contract_are_explicit():
    low = PROMPT_ECC.lower()
    assert "you are eyle, the running agent" in low
    assert 'source=eyle' in low
    assert "workspace is the user's selected project" in low
    assert "help-desk dispatcher" in low
    assert "final user message is always current_request" in low
    assert "history_messages_omitted=0" in low and "do not substitute an unrelated fact" in low


def test_rev373_memory_projection_preserves_domain_and_context_key(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    storage = context["core_memory"]["storage_dir"]
    scope = world_scope(context["core_memory"]["world_scope_id"])
    ingest_chat_message(
        storage,
        world_scope_value=scope,
        conversation_id="conv-73",
        message_id=1,
        role="user",
        content="quero falar de dinheiro",
    )
    session = AgentSession("recall chat")
    result = memory_activate_result(
        session,
        arguments={"domain": "chat", "context_key": "conv-73", "limit": 10},
        registry=registry,
        config=base_config(),
        provider_context=context,
    )
    assert result["ok"] is True
    assert "memory_view" not in result["detail"]
    view = materialize_explicit_memory_view(
        session, registry=registry, config=base_config(), provider_context=context,
    )
    assert len(view["nodes"]) == 1
    node = view["nodes"][0]
    assert node["domain"] == "chat"
    assert node["context_key"] == "conv-73"
    assert node["content"] == "quero falar de dinheiro"


def test_rev373_memory_activate_domain_filter_does_not_cross_conversations(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    storage = context["core_memory"]["storage_dir"]
    scope = world_scope(context["core_memory"]["world_scope_id"])
    ingest_chat_message(storage, world_scope_value=scope, conversation_id="conv-a", message_id=1, role="user", content="empresa Alpha")
    ingest_chat_message(storage, world_scope_value=scope, conversation_id="conv-b", message_id=2, role="user", content="empresa Beta")

    session = AgentSession("which company")
    result = memory_activate_result(
        session,
        arguments={"domain": "chat", "context_key": "conv-a", "query": "empresa", "limit": 10},
        registry=registry,
        config=base_config(),
        provider_context=context,
    )
    assert result["ok"] is True
    view = materialize_explicit_memory_view(session, registry=registry, config=base_config(), provider_context=context)
    assert [n["content"] for n in view["nodes"]] == ["empresa Alpha"]


def test_rev373_recall_result_is_compact_and_body_has_single_prompt_authority(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    seed = AgentSession("seed")
    learned = apply_memory_sidecar(
        seed,
        [{"op": "remember", "key": "long", "scope": "user", "retention": "persistent", "kind": "note", "content": "X" * 5000}],
        registry=registry,
        provider_context=context,
    )
    assert learned["ok"] is True
    session = AgentSession("recall")
    result = memory_activate_result(
        session,
        arguments={"ids": [learned["aliases"]["long"]], "limit": 10},
        registry=registry,
        config=base_config(),
        provider_context=context,
    )
    assert result["ok"] is True
    encoded = json.dumps(result["detail"], ensure_ascii=False)
    assert "X" * 100 not in encoded
    assert result["detail"]["activation"] == "materialized_in_memory_view"
    view = materialize_explicit_memory_view(session, registry=registry, config=base_config(), provider_context=context)
    assert any(len(n["content"]) >= 5000 for n in view["nodes"])


def test_rev373_runtime_feedback_is_physically_budgeted_not_count_ceiling():
    cfg = base_config()
    cfg.setdefault("context_engine", {})["runtime_feedback_materialization_tokens"] = 40
    feedback = [
        {"code": f"C{i}", "detail": "z" * 80}
        for i in range(20)
    ]
    materialized = materialize_runtime_feedback(feedback, cfg)
    assert materialized
    assert materialized[-1]["code"] == "C19"
    assert len(materialized) < len(feedback)


def test_rev373_history_accounting_surfaces_conversation_materialization():
    details = {
        "llm_usage": {
            "prompt_tokens_actual": 100,
            "conversation_messages_materialized": 8,
            "conversation_messages_omitted": 3,
        },
        "llm_calls": [],
    }
    accounting = build_prompt_cost_accounting(details)
    assert accounting["diagnostics"]["conversation_messages_materialized"] == 8
    assert accounting["diagnostics"]["conversation_messages_omitted"] == 3
    assert accounting["diagnostics"]["older_history_available"] is True
