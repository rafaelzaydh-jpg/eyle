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
        from eyle.host import build_bundled_host
        from llm.executar import PROMPT_ECC
        from llm.structured import schema_for_profile
        cfg=_json(base/"config.json")
        host=build_bundled_host(str(base)); registry=host.registry
        host_context=host.provider_context()
        core_memory=host_context.get("core_memory") if isinstance(host_context,dict) else None
        if not isinstance(core_memory,dict) or not str(core_memory.get("world_scope_id") or "").strip() or "scope_root" in core_memory:
            out.append("Host must provide opaque core_memory.world_scope_id without scope_root")
        if manifest.get("public_capabilities") != registry.names(): out.append("manifest public_capabilities != registry")
        expected_surface=ecc_catalog(registry,cfg,registry.names())
        expected_ops={
            "explorar":[item.get("operation") for item in expected_surface.get("explorar") or []],
            "construir":[item.get("operation") for item in expected_surface.get("construir") or []],
            "concluir":["concluir"],
        }
        if manifest.get("ecc_operations") != expected_ops: out.append("manifest ecc_operations != ECC catalog")
        schema=schema_for_profile("ecc")
        types=[]
        for variant in schema.get("oneOf") or []:
            enum=((variant.get("properties") or {}).get("type") or {}).get("enum") or []
            if enum: types.append(enum[0])
        if types != ["explorar","construir","concluir"]: out.append(f"ECC decision types invalid:{types!r}")
        schema_text=json.dumps(schema,ensure_ascii=False).lower()
        for forbidden in ("investigation_updates","task_updates","memory_updates","learned","await_user","completion_mode","grounding_ids","effect_ids"):
            if forbidden in schema_text: out.append(f"legacy structured field:{forbidden}")
        lower=PROMPT_ECC.lower()
        for required in (
            "three moves", "explorar", "construir", "concluir",
            "understand what the user means", "not every message is a task",
            "runtime does not understand meaning", "what am i still trying to achieve",
            "evidence means something was really observed", "fresh does not mean",
            "memory is what is worth knowing again later", "useful again in the future",
            "do not need to wait for the user", "memory can also be wrong",
            "not a fourth move", "operation=recall", "objective says what",
        ):
            if required not in lower: out.append(f"ECC prompt concept missing:{required}")
        memory_props=[]
        for variant in schema.get("oneOf") or []:
            memory=((variant.get("properties") or {}).get("memory") or {})
            props=memory.get("properties") or {}
            memory_props.append(set(props))
        if any(props != {"focus","disposition","operations"} for props in memory_props):
            out.append("ECC memory sidecar contract invalid")
        objective_props=[]
        for variant in schema.get("oneOf") or []:
            objective=((variant.get("properties") or {}).get("objective") or {})
            objective_props.append(set((objective.get("properties") or {}).keys()))
            if "objective" not in (variant.get("required") or []): out.append("ECC objective sidecar must be required")
        if any(props != {"disposition","state"} for props in objective_props):
            out.append("ECC objective sidecar contract invalid")
        if '"project"' in schema_text or "line_start" in schema_text or "line_end" in schema_text:
            out.append("ECC memory contract contains domain-specific project/line selector")
        state=AgentSession("x").to_dict()
        if state.get("session_schema_version") != SESSION_SCHEMA_VERSION: out.append("Session schema identity mismatch")
        for forbidden in ("decision_ledger","investigation","tasks","task_memory","pending_capability"):
            if forbidden in state: out.append(f"legacy session field:{forbidden}")
        for required in ("evidence","memory_focus","objective_state","runtime_feedback","pending_operation","conversation_background"):
            if required not in state: out.append(f"ECC session field missing:{required}")
        if set((cfg.get("agent") or {}).keys()) != {"task_deadline_seconds"}: out.append("agent config must only contain task_deadline_seconds")
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
    for root_name in ("eyle/providers","eyle/capabilities"):
        for path in (base/root_name).rglob("*.py"):
            text=path.read_text(encoding="utf-8").lower()
            if "from eyle.core" in text or "import eyle.core" in text: out.append(f"provider imports core:{path.relative_to(base)}")
    memory_runtime=(base/"eyle/runtime/memory_graph.py").read_text(encoding="utf-8").lower()
    for forbidden_semantic in ("decide_importance", "detect_dead_code", "semantic_relevance", "choose_what_to_remember"):
        if forbidden_semantic in memory_runtime: out.append(f"memory runtime gained semantic policy:{forbidden_semantic}")
    if "def world_scope(" not in memory_runtime or "def project_scope(" in memory_runtime:
        out.append("memory runtime world scope boundary invalid")
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
    if not readme.startswith("# Eyle") or "Explorar" not in readme or "Construir" not in readme or "Concluir" not in readme:
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
