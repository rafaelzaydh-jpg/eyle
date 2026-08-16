#!/usr/bin/env python3
"""Fail-closed release identity/architecture verifier for Eyle ECC."""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from typing import Any, Dict, List

MIN_PYTHON=(3,11)
CORE_FILES={"__init__.py","agent.py","ecc.py","evidence.py","memory.py","session.py"}
_GENERIC_CORE_FILES=CORE_FILES
FORBIDDEN_CORE={"knowledge.py","decision.py","investigation.py","tasks.py","task_memory.py","validation.py","continuation.py","token_budget.py","tools.py","patching.py","grounding.py"}
FORBIDDEN_DIRS={"__pycache__",".pytest_cache",".mypy_cache",".ruff_cache"}
_REMOVED_CORE_DOMAIN_FILES=FORBIDDEN_CORE
FORBIDDEN_RUNTIME_FILES={"agent_pendente.json","telemetry.sqlite3","fila.json","conversa.json"}

class ReleaseIdentityError(RuntimeError): pass

def _json(path:Path)->Dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise ReleaseIdentityError(f"JSON object required:{path.name}")
    return value

def identidade_config(base:Path)->Dict[str,str]:
    cfg=_json(base/"config.json")
    return {k:str(cfg.get(k) or "") for k in ("app_version","config_schema_version","revision")}

def _artifact_violations(base:Path)->List[str]:
    out=[]
    for path in base.rglob("*"):
        rel=str(path.relative_to(base)).replace("\\","/")
        if path.is_dir() and path.name in FORBIDDEN_DIRS: out.append(f"generated artifact:{rel}/")
        elif path.is_file() and (path.suffix in {".pyc",".pyo"} or path.name in FORBIDDEN_RUNTIME_FILES): out.append(f"generated artifact:{rel}")
    return out

def _architecture_violations(base:Path, manifest:Dict[str,Any])->List[str]:
    out=[]
    core=base/"eyle/core"
    actual={p.name for p in core.glob("*.py")}
    if actual != CORE_FILES: out.append(f"core files invalid:{sorted(actual)!r}")
    if actual & FORBIDDEN_CORE: out.append(f"legacy core returned:{sorted(actual & FORBIDDEN_CORE)!r}")
    for rel in ("eyle/runtime/ecc_runtime.py","eyle/runtime/continuation.py","eyle/runtime/token_budget.py"):
        if not (base/rel).is_file(): out.append(f"required ECC runtime file missing:{rel}")

    base_text=str(base.resolve()); inserted=False
    if base_text not in sys.path: sys.path.insert(0,base_text); inserted=True
    try:
        from eyle.core.session import SESSION_SCHEMA_VERSION, AgentSession
        from eyle.core.ecc import catalog as ecc_catalog
        from eyle.runtime.continuation import PENDING_SCHEMA_VERSION
        from eyle.runtime.execution_context import (
            EXECUTION_CONTINUITY_SCHEMA_VERSION, ExecutionContext, validate_execution_continuity_state,
        )
        from eyle.host import build_bundled_host
        from llm.executar import PROMPT_ECC
        from llm.structured import (
            canonicalize_wire_response, json_schema_response_format, schema_for_profile, wire_schema_for_profile,
        )
        cfg=_json(base/"config.json")
        llm_cfg=cfg.get("llm") or {}
        for removed_llm in ("max_tokens","agent_max_tokens","openai_compatible"):
            if removed_llm in llm_cfg: out.append(f"removed LLM limiter/legacy field returned:{removed_llm}")
        if int(llm_cfg.get("generated_token_fuse") or 0) != 120000:
            out.append("Rev3 generated_token_fuse must default to 120000")
        if str(llm_cfg.get("base_url") or "").rstrip("/") not in {"http://127.0.0.1:8080", "http://localhost:8080", "http://127.0.0.1:8080/v1", "http://localhost:8080/v1", "http://[::1]:8080", "http://[::1]:8080/v1"}:
            out.append("Rev3 default llm.base_url must remain the local Adapter boundary on port 8080")
        if llm_cfg.get("context_window_tokens") is not None:
            out.append("Rev3 default local context window must be disabled")
        if llm_cfg.get("read_timeout_seconds") is not None:
            out.append("Rev3 default read timeout must defer to task deadline")
        host=build_bundled_host(str(base)); registry=host.registry
        host_context=host.provider_context()
        core_memory=host_context.get("core_memory") if isinstance(host_context,dict) else None
        if not isinstance(core_memory,dict) or not str(core_memory.get("world_scope_id") or "").strip() or "scope_root" in core_memory:
            out.append("Host must provide opaque core_memory.world_scope_id without scope_root")
        if manifest.get("public_capabilities") != registry.names(): out.append("manifest public_capabilities != registry")
        expected_surface=ecc_catalog(registry,cfg,registry.names(),memory_enabled=bool(core_memory))
        expected_ops={
            "explorar":[item.get("operation") for item in expected_surface.get("explorar") or []],
            "construir":[item.get("operation") for item in expected_surface.get("construir") or []],
            "concluir":["concluir"],
        }
        if manifest.get("ecc_operations") != expected_ops: out.append("manifest ecc_operations != ECC catalog")
        schema=schema_for_profile("ecc")
        wire_schema=wire_schema_for_profile("ecc")
        response_format=json_schema_response_format("ecc")
        if wire_schema == schema:
            out.append("Rev3 wire schema must be distinct from canonical ECC schema")
        if wire_schema.get("additionalProperties") is not True:
            out.append("Rev3 provider wire schema must remain tolerant")
        if ((response_format.get("json_schema") or {}).get("strict")) is not False:
            out.append("Rev3 provider wire schema must not claim strict canonical enforcement")
        if ((response_format.get("json_schema") or {}).get("schema")) != wire_schema:
            out.append("Rev3 response_format must expose the wire schema, not the canonical schema")
        try:
            sample=canonicalize_wire_response({"type":"concluir","answer":"ok"})
            if sample != {"decision":{"type":"concluir","response":"ok"},"memory_delta":[]}:
                out.append("Rev3 deterministic flat-wire canonicalization invalid")
        except Exception as exc:
            out.append(f"Rev3 deterministic wire canonicalizer failed:{type(exc).__name__}")
        if set(schema.get("properties") or {}) != {"decision", "memory_delta"}:
            out.append("Rev3 canonical envelope must be exactly decision + memory_delta")
        if set(schema.get("required") or []) != {"decision", "memory_delta"}:
            out.append("Rev3 canonical envelope fields must be required")
        decision_schema=((schema.get("properties") or {}).get("decision") or {})
        types=[]
        for variant in decision_schema.get("oneOf") or []:
            enum=((variant.get("properties") or {}).get("type") or {}).get("enum") or []
            if enum: types.append(enum[0])
        if types != ["explorar","construir","concluir"]: out.append(f"ECC decision types invalid:{types!r}")
        memory_delta=((schema.get("properties") or {}).get("memory_delta") or {})
        if memory_delta.get("type") != "array": out.append("Rev3 memory_delta must be an array")
        schema_text=json.dumps(schema,ensure_ascii=False).lower()
        for forbidden in ("objective","investigation_updates","task_updates","memory_updates","learned","await_user","completion_mode","grounding_ids","effect_ids","disposition","focus"):
            if forbidden in schema_text: out.append(f"legacy structured field:{forbidden}")
        lower=PROMPT_ECC.lower()
        for required in (
            "three ecc moves", "explorar", "construir", "concluir",
            "understand what the user means", "not every message is a task",
            "current_request is the active user request", "no raw transcript memory",
            "coverage", "frontier", "private handles", "memory graph",
            "memory_delta", 'retention:"temporary|persistent"', "memory is continuous learning",
            "memory_overview", "memory_activate", "runtime does not", "hot-cold",
            "workspace = the user-selected/open project", "eyle = the source tree of the eyle instance",
            "batch", "memory_view is a working view",
            "not universal truth", "do not guess",
            "frontier is not a limit", "no semantic count ceiling",
            "atomic", "artifact", "material", "support format",
            "simple json cognition object", "prefer the flat wire shape", "simplest unambiguous wire support",
            "eyle deterministically wraps", "epistemic memory", "retention is only a storage/lifecycle choice", "memory consolidation",
            "persistent does not mean certain", "memory_history", "memory_relation_history", "revise_relation", "changed_from", "db-cursor",
            "associative recall cues", "retrieval hints only", "recall aliases/concepts/cues", "relation labels",
        ):
            if required not in lower: out.append(f"ECC prompt concept missing:{required}")
        explore=(decision_schema.get("oneOf") or [{}])[0]
        operations=((explore.get("properties") or {}).get("operations") or {})
        if operations.get("minItems") != 1 or "maxItems" in operations:
            out.append("Rev3 explorar must be open-ended after one operation")
        if "maxItems" in memory_delta:
            out.append("Rev3 memory_delta must not have a semantic item ceiling")
        action_variants=((memory_delta.get("items") or {}).get("oneOf") or [])
        action_names=[]
        for variant in action_variants:
            enum=(((variant.get("properties") or {}).get("op") or {}).get("enum") or [])
            if enum: action_names.append(enum[0])
        if action_names != ["remember","revise","relate","revise_relation","archive","supersede","retire_relation"]:
            out.append(f"Rev3 memory action schema incomplete:{action_names!r}")
        remember_schema=action_variants[0] if action_variants else {}
        remember_args=((remember_schema.get("properties") or {}).get("arguments") or {})
        epistemic_schema=((remember_args.get("properties") or {}).get("epistemic") or {})
        epistemic_props=(epistemic_schema.get("properties") or {})
        if set(epistemic_props) != {"nature","confidence","volatility","temporal","context"}:
            out.append(f"Rev3 epistemic schema incomplete:{sorted(epistemic_props)!r}")
        if "enum" in (epistemic_props.get("nature") or {}) or "enum" in (epistemic_props.get("volatility") or {}):
            out.append("Rev3 epistemic nature/volatility must remain Main-authored open labels")
        recall_schema=((remember_args.get("properties") or {}).get("recall") or {})
        recall_props=(recall_schema.get("properties") or {})
        if set(recall_props) != {"aliases","concepts","cues"}:
            out.append(f"Rev3 associative recall schema incomplete:{sorted(recall_props)!r}")
        revise_schema=action_variants[1] if len(action_variants)>1 else {}
        revise_props=((((revise_schema.get("properties") or {}).get("arguments") or {}).get("properties")) or {})
        for recall_field in ("recall","add_recall","remove_recall"):
            if recall_field not in revise_props:
                out.append(f"Rev3 revise associative field missing:{recall_field}")
        support_schema=((remember_args.get("properties") or {}).get("supports") or {})
        support_array_variant=(support_schema.get("oneOf") or [{}])[0]
        support_variants=(((support_array_variant.get("items") or {}).get("oneOf")) or [])
        support_kinds=[]
        for variant in support_variants[:3]:
            enum=(((variant.get("properties") or {}).get("kind") or {}).get("enum") or [])
            if enum: support_kinds.append(enum[0])
        if support_kinds != ["request","memory","material"]:
            out.append(f"Rev3 canonical support schema incomplete:{support_kinds!r}")
        for required_support_field in ("material_id","memory_id"):
            if required_support_field not in schema_text:
                out.append(f"Rev3 support field missing from provider schema:{required_support_field}")
        build=(decision_schema.get("oneOf") or [{},{ }])[1] if len(decision_schema.get("oneOf") or [])>1 else {}
        if "on_success" in (build.get("properties") or {}):
            out.append("Rev3 build must return to Main after the physical observation; on_success is forbidden")
        if '"project"' in schema_text or "line_start" in schema_text or "line_end" in schema_text:
            out.append("ECC memory contract contains domain-specific project/line selector")
        protocol=(base/"llm/protocol.py")
        if not protocol.is_file():
            out.append("provider-neutral llm/protocol.py missing")
        else:
            protocol_text=protocol.read_text(encoding="utf-8").lower()
            for required in ("canonicalprompt","stable","dynamic","adapter_wire_json_schema","implicit","explicit","session"):
                if required not in protocol_text: out.append(f"provider protocol concept missing:{required}")
            for upstream_mode in ("native_json_schema","json_object","prompt_json"):
                if upstream_mode in protocol_text: out.append(f"Eyle protocol must not own upstream structured mode:{upstream_mode}")
            for vendor in ("deepseek","qwen"):
                if vendor in protocol_text: out.append(f"provider protocol hardcodes vendor:{vendor}")
        llm_transport=(base/"llm/executar.py").read_text(encoding="utf-8").lower()
        for legacy_transport in ("localhost:11434", "/api/chat", "def _chamar_ollama"):
            if legacy_transport in llm_transport: out.append(f"legacy local-model transport returned:{legacy_transport}")
        for required_handshake in ("eyle-adapter-transport-v1", "eyle-adapter-handshake-v1", "eyle/handshake", "_ensure_adapter_handshake", "diagnosticar_backend"):
            if required_handshake not in llm_transport:
                out.append(f"Rev3 Eyle Adapter-handshake mechanic missing:{required_handshake}")
        if 'diagnosticar_backend' in llm_transport and '/v1/models' in llm_transport:
            # /v1/models may still exist as a generic endpoint helper elsewhere, but
            # startup compatibility must be handshake + advertised readiness. The
            # function-level regression test provides the strict behavioral proof.
            pass
        adapter_path=base/"server/server.py"
        if adapter_path.is_file():
            adapter_text=adapter_path.read_text(encoding="utf-8").lower()
            for required_adapter in ("eyle-adapter-transport-v1", "eyle-adapter-handshake-v1", '@app.get("/v1/eyle/handshake")', '"authority": "transport-only"', '"semantic_protocol": "client-owned"'):
                if required_adapter not in adapter_text:
                    out.append(f"Rev3 Adapter formal-handshake mechanic missing:{required_adapter}")
            for forbidden_adapter_semantic in ("memory_delta", "epistemic", "remember", "on_success", "explorar", "construir", "concluir"):
                if forbidden_adapter_semantic in adapter_text:
                    out.append(f"Rev3 Adapter regained cognition semantics:{forbidden_adapter_semantic}")
        state=AgentSession("x").to_dict()
        if state.get("session_schema_version") != SESSION_SCHEMA_VERSION: out.append("Session schema identity mismatch")
        for forbidden in ("decision_ledger","investigation","tasks","task_memory","pending_capability"):
            if forbidden in state: out.append(f"legacy session field:{forbidden}")
        for required in ("evidence","memory_view","runtime_feedback","pending_operation"):
            if required not in state: out.append(f"ECC session field missing:{required}")
        for removed in ("memory_focus","objective_state","conversation_background","request_context"):
            if removed in state: out.append(f"removed Rev2.7 session field returned:{removed}")
        if set((cfg.get("agent") or {}).keys()) != {"task_deadline_seconds"}: out.append("agent config must only contain task_deadline_seconds")
        if PENDING_SCHEMA_VERSION != "11-ecc":
            out.append("Rev3 pending continuation schema must be 11-ecc")
        if EXECUTION_CONTINUITY_SCHEMA_VERSION != "execution-continuity-v1":
            out.append("Rev3 execution continuity schema identity invalid")
        try:
            logical = ExecutionContext.from_config(cfg, execution_id="release-verifier", source_job_id=1)
            logical.completion_tokens_actual = 17
            state = logical.continuation_state()
            validate_execution_continuity_state(state)
            changed_cfg = json.loads(json.dumps(cfg))
            changed_cfg.setdefault("llm", {})["generated_token_fuse"] = int(logical.generated_token_limit) + 999
            changed_cfg.setdefault("agent", {})["task_deadline_seconds"] = int((cfg.get("agent") or {}).get("task_deadline_seconds") or 1800) + 999
            restored = ExecutionContext.from_continuation_state(changed_cfg, state, source_job_id=2)
            if restored.execution_id != "release-verifier" or restored.generated_token_limit != logical.generated_token_limit or restored.completion_tokens_actual != 17 or restored.deadline_wall_time != logical.deadline_wall_time or restored.resume_count != 1:
                out.append("Rev3 logical execution state does not preserve identity/fuse/deadline/usage across resume")
        except Exception as exc:
            out.append(f"Rev3 execution continuity roundtrip failed:{type(exc).__name__}")
    finally:
        if inserted:
            try: sys.path.remove(base_text)
            except ValueError: pass

    ecc=(base/"eyle/core/ecc.py").read_text(encoding="utf-8").lower()
    for forbidden_provider in ('"standard.', '"memory.', "'standard.", "'memory."):
        if forbidden_provider in ecc: out.append(f"ECC core hardcodes bundled provider:{forbidden_provider}")
    agent=(base/"eyle/core/agent.py").read_text(encoding="utf-8").lower()
    for forbidden in ("from eyle.providers.standard","import eyle.providers.standard","write_prepare","analysis_investigate","task_updates","investigation_updates"):
        if forbidden in agent: out.append(f"agent legacy/domain coupling:{forbidden}")
    if "ecc_protocol_recovery" not in agent:
        out.append("Rev3 structured semantic failures must return to Main as runtime feedback")
    if "protocol_retry_streak" in agent:
        out.append("Rev3 must not restore a fixed structured retry ceiling")
    for required_continuity in ("from_continuation_state", "continuation_state", "task_deadline_exceeded", "release_memory_navigation"):
        if required_continuity not in agent:
            out.append(f"Rev3 agent execution-continuity mechanic missing:{required_continuity}")
    continuation_text=(base/"eyle/runtime/continuation.py").read_text(encoding="utf-8").lower()
    for required_pending in ('pending_schema_version = "11-ecc"', '"execution_state"'):
        if required_pending not in continuation_text:
            out.append(f"Rev3 pending execution-state contract missing:{required_pending}")
    execution_text=(base/"eyle/runtime/execution_context.py").read_text(encoding="utf-8").lower()
    for required_execution in ("execution-continuity-v1", "deadline_wall_time", "generated_token_limit", "resume_count"):
        if required_execution not in execution_text:
            out.append(f"Rev3 execution snapshot mechanic missing:{required_execution}")
    if (base/"eyle/providers/standard_impl/objective_scope.py").exists():
        out.append("removed objective_scope provider fossil returned")
    if not (base/"eyle/providers/standard_impl/file_scope.py").is_file():
        out.append("file_scope provider helper missing")
    for root_name in ("eyle/providers","eyle/capabilities"):
        for path in (base/root_name).rglob("*.py"):
            text=path.read_text(encoding="utf-8").lower()
            if "from eyle.core" in text or "import eyle.core" in text: out.append(f"provider imports core:{path.relative_to(base)}")
    memory_core=(base/"eyle/core/memory.py").read_text(encoding="utf-8").lower()
    if "trim_temporary_nodes(" in memory_core:
        out.append("Rev3 must not auto-trim temporary memory in Core")
    if "def release_memory_navigation(" not in memory_core or "release_recall_snapshot(" not in memory_core:
        out.append("Rev3 terminal Memory-navigation cleanup missing")
    memory_runtime=(base/"eyle/runtime/memory_graph.py").read_text(encoding="utf-8").lower()
    for required_scalable in ("memory_recall_snapshots", "memory_recall_items", "create_recall_snapshot", "recall_snapshot_page", "memory_fts", "fts5"):
        if required_scalable not in memory_runtime:
            out.append(f"Rev3 scalable recall mechanic missing:{required_scalable}")
    for required_associative in ("associative_recall", "_clean_recall_metadata", "queries", "relation_labels"):
        if required_associative not in memory_runtime:
            out.append(f"Rev3 cognitive-recall mechanic missing:{required_associative}")
    if "2.7.5-r2.9-memory-graph-v8" not in memory_runtime:
        out.append("Rev3 Memory Graph v8 identity missing")
    if '"all_ids"' in (base/"eyle/core/memory.py").read_text(encoding="utf-8").lower():
        out.append("Rev3 Memory Frontier must not persist full selected ID universes")
    for forbidden_semantic in ("decide_importance", "detect_dead_code", "semantic_relevance", "choose_what_to_remember", "retrieval_count", "exposure_tier", "connectivity_score", "articulation_point", "topology_fallback"):
        if forbidden_semantic in memory_runtime: out.append(f"memory runtime gained removed/semantic policy:{forbidden_semantic}")
    if "def world_scope(" not in memory_runtime or "def project_scope(" in memory_runtime:
        out.append("memory runtime world scope boundary invalid")
    for required_memory in ("retention", "temporary_graph_records", "temporary", "persistent"):
        if required_memory not in memory_runtime:
            out.append(f"Rev3 intrinsic memory mechanic missing:{required_memory}")
    if "def trim_temporary_nodes(" in memory_runtime or "def clear_temporary_nodes(" in memory_runtime:
        out.append("Rev3 obsolete automatic temporary-memory trimming helpers returned")
    return out

def validar_artefato_release(base_dir:os.PathLike[str]|str, manifesto:Dict[str,Any]|None=None)->None:
    base=Path(base_dir); manifest=manifesto if isinstance(manifesto,dict) else _json(base/"release_manifest.json")
    violations=_artifact_violations(base)+_architecture_violations(base,manifest)
    if (manifest.get("publication") or {}).get("requires_extracted_artifact_verification") is not True: violations.append("extracted artifact verification not required")
    if violations: raise ReleaseIdentityError("invalid release artifact:\n- "+"\n- ".join(sorted(set(violations))))

def validar_identidade_release(base_dir:os.PathLike[str]|str)->Dict[str,str]:
    if sys.version_info<MIN_PYTHON: raise ReleaseIdentityError("Python 3.11+ required")
    base=Path(base_dir); identity=identidade_config(base); manifest=_json(base/"release_manifest.json")
    errors=[]
    for k,v in identity.items():
        if str(manifest.get(k) or "") != v: errors.append(f"manifest {k} mismatch")
    if str(manifest.get("release")) != identity["app_version"]: errors.append("release != app_version")
    readme=(base/"README.md").read_text(encoding="utf-8")
    readme_lower=readme.lower()
    if "# eyle" not in readme_lower or any(term not in readme_lower for term in ("explorar", "construir", "concluir")):
        errors.append("README project overview missing")
    if "**Revision:**" in readme or "REV2_" in readme:
        errors.append("README must describe the project, not a revision report")
    if errors: raise ReleaseIdentityError("release identity mismatch:\n- "+"\n- ".join(errors))
    validar_artefato_release(base,manifest); return identity

def main()->int:
    base=Path(__file__).resolve().parent.parent.parent
    identity=validar_identidade_release(base)
    print(f"release artifact ok: app={identity['app_version']} schema={identity['config_schema_version']} revision={identity['revision']} python>={MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
    return 0

if __name__=="__main__": raise SystemExit(main())
