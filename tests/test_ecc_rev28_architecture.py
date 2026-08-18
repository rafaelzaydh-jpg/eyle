from __future__ import annotations

import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from eyle.core.ecc import catalog
from eyle.core.memory import (
    apply_memory_sidecar, memory_activate_result, memory_continue_result,
    memory_overview_result, materialize_explicit_memory_view, sync_memory_lifecycle, memory_environment,
)
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION
from eyle.runtime.memory_graph import MEMORY_GRAPH_SCHEMA_VERSION, graph_counts
from llm.executar import PROMPT_ECC
from llm.protocol import CanonicalPrompt
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config, run_agent, standard_registry


def pc(root: Path, storage: Path | None = None, eyle_root: Path | None = None):
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(eyle_root or root)},
        "core_memory": {"storage_dir": str(storage or root.parent / (root.name + "_memory")), "world_scope_id": f"workspace:{root.resolve()}"},
    }


def explore(op, args=None, memory=None):
    return {"type":"explorar","operations":[{"operation":op,"arguments":dict(args or {})}],"memory_delta":list(memory or [])}


def explore_many(items, memory=None):
    return {"type":"explorar","operations":[{"operation":op,"arguments":dict(args or {})} for op,args in items],"memory_delta":list(memory or [])}


def build(op, args=None, memory=None):
    return {"type":"construir","operation":op,"arguments":dict(args or {}),"memory_delta":list(memory or [])}


def conclude(text, memory=None):
    return {"type":"concluir","response":text,"memory_delta":list(memory or [])}


def test_rev28_canonical_envelope_places_memory_beside_ecc():
    schema=schema_for_profile("ecc")
    assert set(schema["properties"])=={"decision","memory_delta"}
    assert schema["required"]==["decision","memory_delta"]
    assert "objective" not in json.dumps(schema).lower()
    raw={"decision":{"type":"concluir","response":"ok"},"memory_delta":[]}
    assert parse_profile_response(raw,"ecc")=={"type":"concluir","response":"ok","memory_delta":[]}
    # Rev2.8.6 wire is intentionally tolerant; flat decision + memory alias is
    # canonicalized inside Eyle before strict Runtime validation.
    assert parse_profile_response({"type":"concluir","response":"old","memory":[]},"ecc") == {
        "type":"concluir","response":"old","memory_delta":[]
    }


def test_rev283_explore_batch_has_no_semantic_operation_ceiling():
    raw={"decision":{"type":"explorar","operations":[
        {"operation":"git_status","arguments":{"source":"workspace"}},
        {"operation":"list_tree","arguments":{"source":"workspace"}},
    ]},"memory_delta":[]}
    parsed=parse_profile_response(raw,"ecc")
    assert len(parsed["operations"])==2
    many={"decision":{"type":"explorar","operations":[{"operation":"list_tree","arguments":{"source":"workspace"}} for _ in range(12)]},"memory_delta":[]}
    assert len(parse_profile_response(many,"ecc")["operations"])==12
    with pytest.raises(StructuredResponseError):
        parse_profile_response({"decision":{"type":"explorar","operations":[]},"memory_delta":[]},"ecc")


def test_rev283_build_must_return_to_main_after_real_observation():
    raw={"decision":{"type":"construir","operation":"transaction","arguments":{},"on_success":"Feito."},"memory_delta":[]}
    parsed=parse_profile_response(raw,"ecc")
    # Retired on_success is a harmless wire artifact and is deterministically
    # dropped; it never re-enters Runtime semantics.
    assert parsed["type"]=="construir" and "on_success" not in parsed


def test_persisted_identity_is_current_only():
    state=AgentSession("x").to_dict()
    assert state["session_schema_version"]==SESSION_SCHEMA_VERSION=="2.7.5-r3.7.2-ecc"
    assert MEMORY_GRAPH_SCHEMA_VERSION=="2.7.5-r3.7.1-memory-graph-v12"
    assert PENDING_SCHEMA_VERSION=="13-ecc"
    assert state["memory_view"]["node_ids"]==[]


def test_prompt_defines_intrinsic_memory_and_provider_neutral_call_optimizations():
    low=PROMPT_ECC.lower()
    for phrase in (
        "memory and ecc are distinct", "memory_delta", 'retention?:"temporary|persistent"',
        "memory is continuous learning", "batch", "coverage", "frontier",
        "frontier is not a limit", "no semantic count ceiling", "atomic", "artifact", "material",
    ):
        assert phrase in low
    assert "on_success" not in low
    assert "hot/cold" in low and "transcript memory" in low


def test_catalog_memory_navigation_uses_temporary_persistent_filter():
    reg=standard_registry(); surface=catalog(reg,base_config(),reg.names(),memory_enabled=True)
    activate=next(x for x in surface["explorar"] if x["operation"]=="memory_activate")
    assert "temporary" in activate["inputs"]["retention"]


def test_temporary_can_promote_same_node_to_persistent(tmp_path):
    reg=standard_registry(); context=pc(tmp_path); seed=AgentSession("seed")
    made=apply_memory_sidecar(seed,[{"op":"remember","key":"clue","scope":"world","retention":"temporary","kind":"clue","content":"Weak anomaly","supports":[{"kind":"request"}]}],registry=reg,provider_context=context)
    mem_id=made["aliases"]["clue"]
    promoted=apply_memory_sidecar(seed,[{"op":"revise","id":mem_id,"expected_revision":1,"retention":"persistent","content":"Confirmed recurring anomaly"}],registry=reg,provider_context=context)
    assert promoted["ok"] and promoted["affected"][0]["id"]==mem_id
    counts=graph_counts(context["core_memory"]["storage_dir"])
    assert counts["temporary_nodes"]==0 and counts["persistent_nodes"]==1


def test_persistent_recall_remains_explicit_with_coverage_frontier(tmp_path):
    reg=standard_registry(); context=pc(tmp_path); seed=AgentSession("seed")
    ops=[{"op":"remember","scope":"user","retention":"persistent","kind":"preference","content":f"cat preference {i}","tags":["cats"],"supports":[{"kind":"request"}]} for i in range(5)]
    apply_memory_sidecar(seed,ops,registry=reg,provider_context=context)
    session=AgentSession("cats")
    overview=memory_overview_result(session,arguments={"scope":"all"},provider_context=context)
    assert overview["ok"] and "cat preference" not in json.dumps(overview)
    first=memory_activate_result(session,arguments={"tags":["cats"],"retention":"persistent","limit":2},registry=reg,config=base_config(),provider_context=context)
    assert first["ok"] and session.memory_view["frontiers"]
    second=memory_continue_result(session,frontier_id=session.memory_view["frontiers"][0],registry=reg,config=base_config(),provider_context=context)
    assert second["ok"] and "handle:" not in json.dumps(second)


def test_compile_prompt_has_stable_prefix_before_dynamic_state(monkeypatch,tmp_path):
    seen=[]
    def fake(prompt,cfg):
        assert isinstance(prompt,CanonicalPrompt)
        seen.append(prompt)
        assert list(prompt.stable)==["ecc_operations","runtime_environment"]
        assert list(prompt.dynamic)[0]=="current_request"
        assert list(prompt.dynamic)[-1]=="runtime_feedback"
        return conclude("ok")
    monkeypatch.setattr(agent,"executar_ecc_llm",fake)
    status,_,_,_=run_agent(agent,"Oi",base_config(),provider_context=pc(tmp_path),retornar_detalhes=True)
    assert status=="completed" and seen[0].stable_hash


def test_batch_exploration_executes_two_independent_operations_in_one_llm_turn(monkeypatch,tmp_path):
    (tmp_path/"a.py").write_text("x=1\n")
    calls=[]
    def fake(prompt,cfg):
        calls.append(prompt)
        if len(calls)==1:
            return explore_many([("list_tree",{"source":"workspace"}),("git_status",{"source":"workspace"})])
        assert len(prompt.dynamic["latest_observations"])==2
        return conclude("done")
    monkeypatch.setattr(agent,"executar_ecc_llm",fake)
    status,text,_,details=run_agent(agent,"inspect both",base_config(),provider_context=pc(tmp_path),retornar_detalhes=True)
    assert (status,text)==("completed","done")
    assert len(calls)==2
    assert details["physical_capability_calls"]==2


def test_rev283_successful_build_returns_real_observation_to_main_before_conclusion(monkeypatch,tmp_path):
    from eyle.runtime.ecc_runtime import DispatchOutcome
    calls=[]
    def fake(prompt,cfg):
        calls.append(prompt)
        if len(calls)==1:
            return build("transaction",{"patches":[]})
        observed=prompt.dynamic["latest_observations"]
        assert len(observed)==1 and observed[0]["ok"] is True and observed[0]["changed"] is True
        return conclude("Corrigido.", memory=[{
            "op":"remember","scope":"world","retention":"temporary","kind":"result",
            "content":"The requested workspace change succeeded.","supports":[{"kind":"request"}],
        }])
    monkeypatch.setattr(agent,"executar_ecc_llm",fake)
    monkeypatch.setattr(agent,"dispatch",lambda *a,**k: DispatchOutcome({"operation":"transaction","status":"success","ok":True,"executed":True,"changed":True},physical_progress=True))
    status,text,_,_=run_agent(agent,"mude",base_config(),provider_context=pc(tmp_path),retornar_detalhes=True)
    assert status=="completed" and text=="Corrigido." and len(calls)==2


def test_rev371_temporary_memory_is_reachable_but_not_auto_projected(monkeypatch,tmp_path):
    context=pc(tmp_path)
    monkeypatch.setattr(agent,"executar_ecc_llm",lambda prompt,cfg: conclude("Plano criado.",memory=[{
        "op":"remember","scope":"world","retention":"temporary","kind":"referent","content":"'o plano' means the current Eyle token-economy plan","supports":[{"kind":"request"}],
    }]))
    status,_,_,_=run_agent(agent,"faça um plano",base_config(),provider_context=context,retornar_detalhes=True,conversation_context={"recent_messages":[]})
    assert status=="completed"

    # Rev3.7.1 deliberately removed universal Temporary Memory projection.
    # The graph remains reachable; only explicit activation may materialize it.
    captured = {}
    def follow(prompt,cfg):
        captured["nodes"] = list(prompt.dynamic["memory_view"]["nodes"])
        return conclude("Vou detalhar o plano.")
    monkeypatch.setattr(agent,"executar_ecc_llm",follow)
    status,text,_,_=run_agent(agent,"Detalhe o plano",base_config(),provider_context=context,retornar_detalhes=True,conversation_context={"recent_messages":[]})
    assert (status,text)==("completed","Vou detalhar o plano.")
    assert captured["nodes"] == []


def test_rev28_cache_warmup_prompt_uses_same_stable_prefix(tmp_path):
    reg=standard_registry(); context=pc(tmp_path); cfg=base_config()
    warm=agent.compile_cache_warmup_prompt(cfg,context,reg)
    assert isinstance(warm,CanonicalPrompt)
    assert list(warm.stable)==["ecc_operations","runtime_environment"]
    assert warm.dynamic["current_request"].startswith("Provider cache warmup")


def test_rev371_cache_warmup_remains_adapter_owned_and_disabled_locally(monkeypatch,tmp_path):
    import llm.executar as llm_exec
    reg=standard_registry(); context=pc(tmp_path); cfg=base_config()
    prompt=agent.compile_cache_warmup_prompt(cfg,context,reg)
    assert llm_exec.warmup_provider_cache(prompt,cfg)["status"]=="disabled"
    # Rev3.6.1+ fixed the bundled Adapter policy: local flags cannot negotiate
    # provider cache semantics or secretly enable a second transport behavior.
    cfg["llm"]["cache_warmup"]=True
    monkeypatch.setattr(llm_exec,"executar_ecc",lambda prompt,cfg:{"type":"concluir","response":"ok","memory_delta":[]})
    result=llm_exec.warmup_provider_cache(prompt,cfg)
    assert result["status"]=="disabled"


def test_rev281_memory_view_omits_repeated_explanatory_envelope(tmp_path):
    reg=standard_registry()
    context=pc(tmp_path)
    view=materialize_explicit_memory_view(AgentSession("hello"),registry=reg,config=base_config(),provider_context=context)
    assert view["available"] is True
    assert "trust_note" not in view
    assert "temporary" not in view  # empty optional region is omitted
    env = memory_environment(context)
    assert set(env) == {"available", "world_scope"}
    assert "nodes" not in env and "edges" not in env
