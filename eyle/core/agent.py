"""Single-session LLM-first programming agent.

There is one reasoning loop. The LLM decides whether to answer, plan, use a
tool, ask a blocking question or propose a patch. The runtime only validates
and executes concrete actions.
"""
from __future__ import annotations

import json
import os
import time
from json import JSONDecoder
from typing import Any, Dict, Iterable, List, Optional, Tuple

from llm.executar import ErroLLM, PROMPT_AGENTE, executar_agente as executar_agente_llm

from .session import AgentSession
from .security import _resolver_caminho_seguro
from .token_budget import available_user_prompt_tokens, estimate_tokens
from .text_hash import extrair_faixa, hash_faixa
from .tools import (
    executar_tool,
    gerar_catalogo_tools,
    reverter_patch_confirmado,
    reverter_patch_set_confirmado,
    validar_chamada_tool,
)
from .validation import validate_final

READ_TOOLS = {"list_tree", "search_code", "find_symbol", "read_range", "read_file"}
MEMORY_TOOLS = {"memory_search", "memory_store"}
PATCH_TOOLS = {"test_patch_dry_run", "test_patch_set_dry_run"}
TERMINAL_TOOL_ERRORS = {"UNSAFE_PATH", "PATH_OUTSIDE_PROJECT", "PERMISSION_DENIED", "WORKSPACE_NOT_AVAILABLE"}


def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _json_candidates(text: str) -> Iterable[Dict[str, Any]]:
    decoder = JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    if text.startswith("```") and "\n" in text:
        body = text.split("\n", 1)[1]
        if body.rstrip().endswith("```"):
            body = body.rsplit("```", 1)[0]
        try:
            value = json.loads(body.strip())
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    candidates = list(_json_candidates(text))
    if not candidates:
        raise ValueError("no JSON object found")
    protocol = {"tool", "tool_call", "tool_calls", "actions", "patches", "needs_user", "final"}
    return next((item for item in candidates if protocol.intersection(item)), candidates[0])


def _normalize_action(action: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(action, dict):
        return None
    if isinstance(action.get("function"), dict):
        function = action["function"]
        action = {"tool": function.get("name"), "arguments": function.get("arguments") or {}}
    tool = action.get("tool") or action.get("name")
    arguments = action.get("arguments") or action.get("args") or action.get("input") or {}
    if not tool:
        return None
    return {"tool": str(tool), "arguments": arguments if isinstance(arguments, dict) else {}}


def _parse_decision(raw: Any) -> Dict[str, Any]:
    value = _json_object(raw)
    if isinstance(value.get("decision"), dict):
        value = value["decision"]
    plan = value.get("plan")
    if isinstance(plan, list):
        plan = [str(item).strip() for item in plan if str(item).strip()]
    else:
        plan = None
    if isinstance(value.get("tool_call"), dict):
        action = _normalize_action(value["tool_call"])
        if action:
            action["plan"] = plan
            return action
    calls = value.get("tool_calls") or value.get("actions")
    if isinstance(calls, list):
        normalized = [item for item in (_normalize_action(call) for call in calls) if item]
        return {"tool_calls": normalized, "plan": plan}
    if isinstance(value.get("patches"), list):
        return {
            "tool": "test_patch_set_dry_run",
            "arguments": {"patches": value["patches"]},
            "plan": plan,
        }
    if "tool" in value or "name" in value:
        action = _normalize_action(value)
        if action:
            action["plan"] = plan
            return action
    if "needs_user" in value:
        return {"needs_user": str(value.get("needs_user") or "").strip(), "plan": plan}
    if "final" in value:
        return {"final": value["final"], "plan": plan}
    raise ValueError("unsupported decision object")


def _trim_history(context: Any, token_budget: int, chars_per_token: int) -> Dict[str, Any]:
    messages = list((context or {}).get("recent_messages") or []) if isinstance(context, dict) else []
    kept: List[Dict[str, Any]] = []
    used = 0
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        cost = estimate_tokens(item, chars_per_token)
        if used + cost > max(0, token_budget):
            continue
        kept.append(item)
        used += cost
    kept.reverse()
    return {"recent_messages": kept, "omitted_messages": max(0, len(messages) - len(kept))}


def _project_descriptor(project: Dict[str, Any]) -> Dict[str, Any]:
    root = (project or {}).get("caminho_origem")
    return {
        "available": bool(root and os.path.isdir(root)),
        "name": os.path.basename(os.path.realpath(root)) if root else None,
    }


def _tests_enabled(config: Dict[str, Any]) -> bool:
    return bool((((config or {}).get("codar") or {}).get("testes") or {}).get("ativado", False))


def _allowed_tools(config: Dict[str, Any], project: Dict[str, Any]) -> set[str]:
    root = (project or {}).get("caminho_origem")
    if not root or not os.path.isdir(root):
        return set()
    names = set(READ_TOOLS) | set(MEMORY_TOOLS)
    if bool(((config or {}).get("codar") or {}).get("ativado", True)):
        names |= PATCH_TOOLS
    if _tests_enabled(config):
        names.add("run_tests")
    return names


def _tool_catalog(config: Dict[str, Any], project: Dict[str, Any]) -> Tuple[set[str], List[Dict[str, Any]]]:
    allowed = _allowed_tools(config, project)
    catalog = gerar_catalogo_tools(
        config=config, allowed_names=allowed, compact=True, minimal=True,
    ) if allowed else []
    for item in catalog:
        if item.get("name") == "test_patch_set_dry_run":
            item["patch_contract"] = {
                "replace_existing": {"operation": "replace", "path": "app.py", "content": "complete new file"},
                "create": {"operation": "create", "path": "routes.py", "content": "complete file"},
                "delete": {"operation": "delete", "path": "old.py"},
                "range_update": {"operation": "update", "path": "app.py", "line_start": 1, "line_end": 3, "new_code": "replacement"},
            }
            item["note"] = "Hashes are filled only from fresh evidence; read every existing file first."
    return allowed, catalog


def _compact_non_read_result(tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    detail = result.get("detail")
    if isinstance(detail, dict):
        detail = {
            key: value for key, value in detail.items()
            if key not in {"rollback_snapshot", "prepared_patches", "applied_patches", "stdout", "stderr"}
        }
    elif isinstance(detail, str):
        detail = detail[:4000]
    return {
        "tool": tool,
        "status": result.get("status"),
        "ok": result.get("ok"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "error_code": result.get("error_code"),
        "detail": detail,
    }


def _register_evidence(session: AgentSession, tool: str, detail: Any) -> List[str]:
    if tool == "search_code" and isinstance(detail, dict):
        candidates = [item for item in detail.get("resultados") or [] if isinstance(item, dict)]
    elif tool in {"read_file", "read_range", "find_symbol"} and isinstance(detail, dict):
        candidates = [detail]
    else:
        candidates = []
    ids: List[str] = []
    for item in candidates:
        if not item.get("arquivo") or not item.get("file_hash"):
            continue
        existing = next((
            evidence_id for evidence_id, evidence in session.evidence.items()
            if evidence.get("arquivo") == item.get("arquivo")
            and evidence.get("linha_inicio") == item.get("linha_inicio")
            and evidence.get("linha_fim") == item.get("linha_fim")
            and evidence.get("file_hash") == item.get("file_hash")
            and evidence.get("content_hash") == item.get("content_hash")
        ), None)
        evidence_id = existing or f"ev-{len(session.evidence) + 1:04d}"
        clone = dict(item)
        clone["id"] = evidence_id
        session.evidence[evidence_id] = clone
        ids.append(evidence_id)
    return ids


def _model_tool_result(session: AgentSession, tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    evidence_ids = _register_evidence(session, tool, result.get("detail")) if result.get("ok") else []
    if tool in READ_TOOLS and isinstance(result.get("detail"), dict):
        detail = result.get("detail")
        if tool == "search_code":
            copied = dict(detail)
            copied_results = []
            for item, evidence_id in zip(copied.get("resultados") or [], evidence_ids):
                clone = dict(item)
                clone["evidence_id"] = evidence_id
                copied_results.append(clone)
            copied["resultados"] = copied_results
            detail = copied
        elif evidence_ids:
            detail = dict(detail)
            detail["evidence_id"] = evidence_ids[0]
        return {
            "tool": tool,
            "status": result.get("status"),
            "ok": result.get("ok"),
            "error_code": result.get("error_code"),
            "detail": detail,
            "evidence_ids": evidence_ids,
        }
    compact = _compact_non_read_result(tool, result)
    if evidence_ids:
        compact["evidence_ids"] = evidence_ids
    return compact


def _crop_payload(payload: Dict[str, Any], budget: int, chars_per_token: int) -> Dict[str, Any]:
    """Fit a prompt by dropping old chat and cropping only the latest raw source."""
    while estimate_tokens(payload, chars_per_token) > budget:
        history = (payload.get("recent_context") or {}).get("recent_messages") or []
        if history:
            payload["recent_context"]["recent_messages"] = history[1:]
            payload["recent_context"]["omitted_messages"] = int(payload["recent_context"].get("omitted_messages", 0)) + 1
            continue
        results = payload.get("latest_tool_results") or []
        cropped = False
        for result in results:
            detail = result.get("detail") if isinstance(result, dict) else None
            if not isinstance(detail, dict):
                continue
            for key in ("conteudo", "trecho_numerado", "conteudo_raw"):
                value = detail.get(key)
                if isinstance(value, str) and len(value) > 1000:
                    detail[key] = value[: max(1000, len(value) // 2)]
                    detail["context_truncated"] = True
                    cropped = True
                    break
            if cropped:
                break
            nested = detail.get("resultados")
            if isinstance(nested, list) and len(nested) > 1:
                detail["resultados"] = nested[: max(1, len(nested) // 2)]
                detail["context_truncated"] = True
                cropped = True
                break
        if cropped:
            continue
        if len(payload.get("evidence_index") or []) > 8:
            payload["evidence_index"] = payload["evidence_index"][-8:]
            continue
        break
    return payload


def _agent_config(config: Dict[str, Any], session: AgentSession) -> Dict[str, Any]:
    clone = dict(config)
    llm = dict(config.get("llm") or {})
    latest_has_source = any(
        isinstance(item, dict)
        and item.get("tool") in READ_TOOLS
        and isinstance(item.get("detail"), dict)
        for item in session.latest_tool_results
    )
    decision_limit = int(llm.get("agent_decision_max_tokens", 1400) or 1400)
    patch_limit = int(llm.get("agent_patch_max_tokens", 4200) or 4200)
    if latest_has_source:
        source_chars = 0
        for item in session.latest_tool_results:
            detail = item.get("detail") if isinstance(item, dict) else None
            if not isinstance(detail, dict):
                continue
            source_chars += sum(
                len(value) for key, value in detail.items()
                if key in {"conteudo", "trecho_numerado", "conteudo_raw"} and isinstance(value, str)
            )
        chars_per_token = max(1, int((config.get("context_engine") or {}).get("chars_per_token_fallback", 3) or 3))
        adaptive = max(1800, (source_chars // chars_per_token) + 1000)
        llm["agent_max_tokens"] = min(patch_limit, adaptive)
    else:
        llm["agent_max_tokens"] = decision_limit
    clone["llm"] = llm
    return clone


def _compile_prompt(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    conversation_context: Any,
    feedback: str,
) -> Tuple[str, set[str]]:
    call_config = config
    context_cfg = call_config.get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    history_budget = int((call_config.get("agent") or {}).get("chat_history_token_budget", 1200) or 1200)
    history = (
        _trim_history(conversation_context, history_budget, chars_per_token)
        if session.turn <= 1 else {"recent_messages": [], "omitted_messages": 0}
    )
    runtime = call_config.get("_runtime_agent_budget")
    if isinstance(runtime, dict):
        runtime["history_messages_omitted"] = history.get("omitted_messages", 0)
    allowed, tools = _tool_catalog(call_config, project)
    payload = {
        "request": session.request,
        "turn": session.turn + 1,
        "plan": session.plan,
        "project": _project_descriptor(project),
        "recent_context": history,
        "latest_tool_results": session.latest_tool_results,
        "evidence_index": session.evidence_index(),
        "available_tools": tools,
        "runtime_feedback": feedback or None,
    }
    output_tokens = int((call_config.get("llm") or {}).get("agent_max_tokens", 1400) or 1400)
    prompt_budget = available_user_prompt_tokens(call_config, PROMPT_AGENTE, output_tokens=output_tokens)
    payload = _crop_payload(payload, prompt_budget, chars_per_token)
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    session.record_prompt(
        mode="agent", characters=len(prompt),
        estimated_tokens=estimate_tokens(prompt, chars_per_token), tool_count=len(tools),
    )
    return prompt, allowed


def _call_agent(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    conversation_context: Any,
    feedback: str = "",
) -> Tuple[Dict[str, Any], set[str]]:
    call_config = _agent_config(config, session)
    prompt, allowed = _compile_prompt(session, call_config, project, conversation_context, feedback)
    raw = executar_agente_llm(prompt, call_config)
    return _parse_decision(raw), allowed


def _details(
    session: AgentSession,
    status: str,
    config: Dict[str, Any],
    limitations: Optional[List[str]] = None,
    failure_code: Optional[str] = None,
) -> Dict[str, Any]:
    runtime = config.get("_runtime_agent_budget") or {}
    usage_keys = (
        "llm_calls", "llm_requests", "prompt_tokens_actual", "prompt_tokens_effective",
        "completion_tokens_actual", "generated_tokens", "reasoning_tokens_actual",
        "total_tokens_effective", "history_messages_omitted",
    )
    return {
        "status": status,
        "plan": session.plan,
        "turns": session.turn,
        "tool_calls": session.tool_calls,
        "tools_used": [item.get("tool") for item in session.tool_history],
        "evidence": session.evidence_index(),
        "limitations": list(limitations or []),
        "failure_code": failure_code,
        "llm_usage": {key: runtime.get(key, 0) for key in usage_keys},
        "llm_responses": list(runtime.get("llm_responses") or []),
        "parse_failures": session.parse_failures,
        "prompt_snapshots": session.prompt_snapshots,
    }


def _find_patch_evidence(session: AgentSession, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for item in reversed(list(session.evidence.values())):
        if item.get("arquivo") != args.get("caminho_relativo"):
            continue
        if item.get("file_hash") != args.get("file_hash_esperado"):
            continue
        if (
            item.get("linha_inicio") == args.get("linha_inicio")
            and item.get("linha_fim") == args.get("linha_fim")
            and item.get("content_hash") == args.get("range_hash_esperado")
        ):
            return item
        content = item.get("conteudo")
        whole_file = (
            isinstance(content, str)
            and int(item.get("linha_inicio") or 0) == 1
            and int(item.get("linha_fim") or 0) == int(item.get("total_linhas_arquivo") or -1)
        )
        if not whole_file:
            continue
        start = int(args.get("linha_inicio") or 0)
        end = int(args.get("linha_fim") or 0)
        derived_content = extrair_faixa(content, start, end)
        if derived_content is None or hash_faixa(content, start, end) != args.get("range_hash_esperado"):
            continue
        derived = dict(item)
        derived.update({
            "linha_inicio": start, "linha_fim": end,
            "conteudo": derived_content, "content_hash": args.get("range_hash_esperado"),
        })
        return derived
    return None


def _pending_single_patch(session: AgentSession, args: Dict[str, Any], evidence: Dict[str, Any]):
    original = str(evidence.get("conteudo") or "")
    if original.endswith("\r\n"):
        original = original[:-2]
    elif original.endswith(("\n", "\r")):
        original = original[:-1]
    apply_args = {
        "caminho_relativo": args["caminho_relativo"],
        "linha_inicio": args["linha_inicio"],
        "linha_fim": args["linha_fim"],
        "codigo_original_esperado": original,
        "codigo_novo": args.get("codigo_novo", ""),
        "file_hash_esperado": args["file_hash_esperado"],
        "range_hash_esperado": args["range_hash_esperado"],
    }
    text = (
        f"Proposta pronta para confirmação: {apply_args['caminho_relativo']}:"
        f"{apply_args['linha_inicio']}-{apply_args['linha_fim']}. Dry-run aprovado. "
        "A aplicação exige confirmação do usuário."
    )
    pending = {
        "continuation_kind": "write_confirmation",
        "pergunta_ao_usuario": text,
        "estado": session.to_dict(),
        "tool_pendente": {"tool": "apply_patch", "arguments": apply_args},
    }
    return text, pending


def _sanitize_prepared_patches(prepared: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for patch in prepared:
        operation = patch.get("operation")
        item = {"operation": operation, "path": patch.get("path")}
        if operation in {"create", "replace"}:
            item["content"] = patch.get("result_content", patch.get("content", ""))
            if operation == "replace":
                item["file_hash_expected"] = patch.get("file_hash_expected")
        elif operation == "delete":
            item["file_hash_expected"] = patch.get("file_hash_expected")
        else:
            item.update({
                "line_start": patch.get("line_start"),
                "line_end": patch.get("line_end"),
                "new_code": patch.get("new_code", ""),
                "file_hash_expected": patch.get("file_hash_expected"),
                "range_hash_expected": patch.get("range_hash_expected"),
            })
        result.append(item)
    return result


def _pending_patch_set(session: AgentSession, detail: Dict[str, Any]):
    patches = _sanitize_prepared_patches(detail.get("prepared_patches") or [])
    files = [str(patch.get("path") or "") for patch in patches]
    text = (
        f"Proposta transacional pronta para confirmação: {len(patches)} arquivo(s): "
        f"{', '.join(files)}. Dry-run aprovado para o conjunto completo. "
        "A aplicação exige confirmação do usuário."
    )
    pending = {
        "continuation_kind": "write_confirmation",
        "pergunta_ao_usuario": text,
        "estado": session.to_dict(),
        "tool_pendente": {"tool": "apply_patch_set", "arguments": {"patches": patches}},
    }
    return text, pending


def _run_tests_after_write(config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    if not _tests_enabled(config):
        return {
            "status": "skipped", "ok": True, "executed": False,
            "error_code": "TESTS_DISABLED", "detail": "Execução de testes desativada.",
        }
    return executar_tool("run_tests", {}, context)


def _resume_single(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    context = {"config": config, "projeto": project, "evidence": session.evidence}
    args, error = validar_chamada_tool("apply_patch", (pending.get("tool_pendente") or {}).get("arguments") or {})
    if error:
        text = "A proposta confirmada ficou inválida."
        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_RESPONSE_INVALID"), full)
    applied = executar_tool("apply_patch", args, context)
    if not applied.get("ok"):
        text = f"A alteração confirmada não foi aplicada: {applied.get('error_code') or 'PATCH_FAILED'}."
        return _return("failed", text, None, _details(session, "failed", config, failure_code=applied.get("error_code") or "PATCH_FAILED"), full)
    snapshot = (applied.get("detail") or {}).get("rollback_snapshot") if isinstance(applied.get("detail"), dict) else None
    tests = _run_tests_after_write(config, context)
    if tests.get("executed") and tests.get("ok") is not True:
        rollback = reverter_patch_confirmado(snapshot, context) if snapshot else {"ok": False}
        text = "Os testes falharam após a escrita. " + ("O arquivo foi restaurado automaticamente." if rollback.get("ok") else "O rollback não pôde ser confirmado.")
        return _return("failed", text, None, _details(session, "failed", config, failure_code="TESTS_FAILED_ROLLED_BACK"), full)
    end = (applied.get("detail") or {}).get("linha_fim_final") if isinstance(applied.get("detail"), dict) else None
    if not isinstance(end, int):
        end = max(args["linha_inicio"], args["linha_inicio"] + max(0, len(str(args.get("codigo_novo") or "").splitlines()) - 1))
    reread = executar_tool("read_range", {
        "caminho_relativo": args["caminho_relativo"],
        "linha_inicio": args["linha_inicio"],
        "linha_fim": max(args["linha_inicio"], end),
    }, context)
    if not reread.get("ok"):
        rollback = reverter_patch_confirmado(snapshot, context) if snapshot else {"ok": False}
        if rollback.get("ok"):
            text = "A releitura obrigatória falhou após a escrita. O arquivo foi restaurado automaticamente."
            failure_code = "POST_WRITE_READ_FAILED_ROLLED_BACK"
        else:
            text = "A releitura obrigatória falhou após a escrita e o rollback não pôde ser confirmado."
            failure_code = "POST_WRITE_READ_FAILED"
        return _return("failed", text, None, _details(session, "failed", config, failure_code=failure_code), full)
    verification = "testes executados com sucesso" if tests.get("executed") else "testes não executados; verificação por dry-run e releitura"
    text = (
        f"Alteração aplicada em {args['caminho_relativo']}.\n\nVerificação:\n"
        f"- dry-run aprovado;\n- patch aplicado;\n- {verification};\n- arquivo relido após a alteração."
    )
    limitations = [] if tests.get("executed") else [str(tests.get("detail") or verification)]
    return _return("success", text, None, _details(session, "success", config, limitations=limitations), full)


def _resume_set(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    context = {"config": config, "projeto": project, "evidence": session.evidence}
    args, error = validar_chamada_tool("apply_patch_set", (pending.get("tool_pendente") or {}).get("arguments") or {})
    if error:
        text = "A transação confirmada ficou inválida."
        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_RESPONSE_INVALID"), full)
    applied = executar_tool("apply_patch_set", args, context)
    if not applied.get("ok"):
        text = f"A transação não foi aplicada: {applied.get('error_code') or 'PATCH_TRANSACTION_FAILED'}."
        return _return("failed", text, None, _details(session, "failed", config, failure_code=applied.get("error_code") or "PATCH_TRANSACTION_FAILED"), full)
    applied_patches = (applied.get("detail") or {}).get("applied_patches") or []
    tests = _run_tests_after_write(config, context)
    if tests.get("executed") and tests.get("ok") is not True:
        rollback = reverter_patch_set_confirmado(applied_patches, context)
        text = "Os testes falharam após a transação. " + ("Todos os arquivos foram restaurados." if rollback.get("ok") else "O rollback completo não pôde ser confirmado.")
        return _return("failed", text, None, _details(session, "failed", config, failure_code="TESTS_FAILED_ROLLED_BACK"), full)
    root = project.get("caminho_origem")
    failures = []
    for patch in applied_patches:
        path = patch.get("path")
        if patch.get("operation") == "delete":
            absolute = _resolver_caminho_seguro(root, path)
            if absolute is None or os.path.exists(absolute):
                failures.append(path)
            continue
        reread = executar_tool("read_file", {"caminho_relativo": path}, context)
        if not reread.get("ok"):
            failures.append(path)
    if failures:
        rollback = reverter_patch_set_confirmado(applied_patches, context)
        if rollback.get("ok"):
            text = "A verificação final falhou em " + ", ".join(failures) + ". Todos os arquivos foram restaurados."
            failure_code = "POST_WRITE_READ_FAILED_ROLLED_BACK"
        else:
            text = "A verificação final falhou em " + ", ".join(failures) + "; o rollback completo não pôde ser confirmado."
            failure_code = "POST_WRITE_READ_FAILED"
        return _return("failed", text, None, _details(session, "failed", config, failure_code=failure_code), full)
    files = [str(item.get("path") or "") for item in applied_patches]
    verification = "testes executados com sucesso" if tests.get("executed") else "testes não executados; verificação por dry-run e releitura"
    text = (
        f"Transação aplicada em {len(files)} arquivo(s): {', '.join(files)}.\n\nVerificação:\n"
        f"- dry-run conjunto aprovado;\n- alterações aplicadas como transação;\n"
        f"- {verification};\n- arquivos alterados relidos e exclusões confirmadas."
    )
    limitations = [] if tests.get("executed") else [str(tests.get("detail") or verification)]
    return _return("success", text, None, _details(session, "success", config, limitations=limitations), full)


def _resume(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    tool = (pending.get("tool_pendente") or {}).get("tool")
    if tool == "apply_patch_set":
        return _resume_set(session, pending, config, project, full)
    return _resume_single(session, pending, config, project, full)



def _freshest_evidence_for_path(session: AgentSession, path: str) -> Optional[Dict[str, Any]]:
    normalized = str(path or "").replace("\\", "/")
    for item in reversed(list(session.evidence.values())):
        if str(item.get("arquivo") or "").replace("\\", "/") == normalized:
            return item
    return None



def _enrich_single_patch(session: AgentSession, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    patch = dict(arguments or {})
    aliases = {
        "path": "caminho_relativo", "file": "caminho_relativo",
        "line_start": "linha_inicio", "line_end": "linha_fim",
        "new_code": "codigo_novo", "content": "codigo_novo",
        "file_hash_expected": "file_hash_esperado",
        "range_hash_expected": "range_hash_esperado",
    }
    for source, target in aliases.items():
        if target not in patch and source in patch:
            patch[target] = patch[source]
        if source != target:
            patch.pop(source, None)
    path = patch.get("caminho_relativo")
    try:
        start, end = int(patch.get("linha_inicio")), int(patch.get("linha_fim"))
    except (TypeError, ValueError):
        return arguments, "range patch needs linha_inicio and linha_fim"
    if not isinstance(path, str) or not path.strip():
        return arguments, "range patch needs caminho_relativo"
    evidence = _freshest_evidence_for_path(session, path)
    if not evidence or not evidence.get("file_hash"):
        return arguments, f"read the file before updating: {path}"
    patch.setdefault("file_hash_esperado", evidence["file_hash"])
    if not patch.get("range_hash_esperado"):
        if int(evidence.get("linha_inicio") or 0) == start and int(evidence.get("linha_fim") or 0) == end:
            patch["range_hash_esperado"] = evidence.get("content_hash")
        else:
            content = evidence.get("conteudo")
            whole_file = (
                isinstance(content, str)
                and int(evidence.get("linha_inicio") or 0) == 1
                and int(evidence.get("linha_fim") or 0) == int(evidence.get("total_linhas_arquivo") or -1)
            )
            if whole_file:
                patch["range_hash_esperado"] = hash_faixa(content, start, end)
        if not patch.get("range_hash_esperado"):
            return arguments, f"read the exact range before updating {path}:{start}-{end}"
    return patch, None

def _enrich_patch_set(session: AgentSession, project: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    raw_patches = arguments.get("patches")
    if not isinstance(raw_patches, list) or not raw_patches:
        return arguments, "patches must be a non-empty list"
    root = project.get("caminho_origem")
    enriched: List[Dict[str, Any]] = []
    for raw in raw_patches:
        if not isinstance(raw, dict):
            return arguments, "each patch must be an object"
        patch = dict(raw)
        path = patch.get("path") or patch.get("caminho_relativo") or patch.get("file")
        if not isinstance(path, str) or not path.strip():
            return arguments, "each patch needs path"
        path = path.strip().replace("\\", "/")
        patch["path"] = path
        absolute = _resolver_caminho_seguro(root, path) if root else None
        if absolute is None:
            return arguments, f"unsafe patch path: {path}"
        exists = os.path.isfile(absolute)
        operation = str(patch.get("operation") or patch.get("operacao") or "").strip().lower()
        has_range = any(key in patch for key in ("line_start", "linha_inicio", "line_end", "linha_fim"))
        has_content = any(key in patch for key in ("content", "conteudo", "new_code", "codigo_novo"))
        if not operation:
            operation = "replace" if exists and has_content and not has_range else "create" if not exists and has_content else "update"
        aliases = {"add": "create", "remove": "delete"}
        operation = aliases.get(operation, operation)
        if operation in {"write", "overwrite"}:
            operation = "replace"
        if operation == "modify":
            operation = "replace" if has_content and not has_range else "update"
        if operation == "update" and has_content and not has_range:
            operation = "replace"
        if operation in {"replace", "create", "update"} and not has_content:
            return arguments, f"{operation} needs an explicit content/new_code field: {path}"
        patch["operation"] = operation
        evidence = _freshest_evidence_for_path(session, path)
        if operation in {"replace", "delete"}:
            if not exists:
                return arguments, f"{operation} requires an existing file: {path}"
            if not evidence or not evidence.get("file_hash"):
                return arguments, f"read the existing file before {operation}: {path}"
            if operation == "replace":
                whole_file = (
                    int(evidence.get("linha_inicio") or 0) == 1
                    and int(evidence.get("linha_fim") or 0) == int(evidence.get("total_linhas_arquivo") or -1)
                )
                if not whole_file:
                    return arguments, f"replace requires a fresh whole-file read: {path}"
            patch.setdefault("file_hash_expected", evidence["file_hash"])
        elif operation == "update":
            start = patch.get("line_start", patch.get("linha_inicio"))
            end = patch.get("line_end", patch.get("linha_fim"))
            try:
                start, end = int(start), int(end)
            except (TypeError, ValueError):
                return arguments, f"range update needs line_start and line_end: {path}"
            patch["line_start"], patch["line_end"] = start, end
            if not evidence or not evidence.get("file_hash"):
                return arguments, f"read the file before updating: {path}"
            patch.setdefault("file_hash_expected", evidence["file_hash"])
            if not patch.get("range_hash_expected"):
                if int(evidence.get("linha_inicio") or 0) == start and int(evidence.get("linha_fim") or 0) == end:
                    patch["range_hash_expected"] = evidence.get("content_hash")
                else:
                    content = evidence.get("conteudo")
                    ev_start = int(evidence.get("linha_inicio") or 0)
                    ev_end = int(evidence.get("linha_fim") or 0)
                    if isinstance(content, str) and ev_start == 1 and ev_end == int(evidence.get("total_linhas_arquivo") or -1):
                        patch["range_hash_expected"] = hash_faixa(content, start, end)
                if not patch.get("range_hash_expected"):
                    return arguments, f"read the exact range before updating {path}:{start}-{end}"
        elif operation == "create":
            if exists:
                return arguments, f"create cannot overwrite an existing file: {path}; use replace"
        enriched.append(patch)
    return {"patches": enriched}, None


def _preserve_source_for_retry(previous: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    needs_source = any(
        (item.get("tool") in PATCH_TOOLS and item.get("ok") is False)
        or item.get("error_code") == "IDENTICAL_READ_BLOCKED"
        for item in current if isinstance(item, dict)
    )
    if not needs_source:
        return current
    sources = [
        item for item in previous
        if isinstance(item, dict) and item.get("tool") in READ_TOOLS and item.get("ok") is True
    ][-2:]
    return sources + current

def _action_signature(tool: str, arguments: Dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _deadline_exceeded(config: Dict[str, Any]) -> bool:
    deadline = (config.get("_runtime_agent_budget") or {}).get("deadline_monotonic")
    return deadline is not None and time.monotonic() >= float(deadline)


def _run(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    full: bool,
    conversation_context: Any = None,
) -> tuple:
    cfg = config.get("agent") or {}
    max_turns = max(1, int(cfg.get("max_llm_turns", 8) or 8))
    max_tool_calls = max(1, int(cfg.get("max_tool_calls", 16) or 16))
    max_identical = max(1, int(cfg.get("max_identical_tool_repeats", 2) or 2))
    parse_retries = max(0, int(cfg.get("protocol_parse_retries", 1) or 1))
    final_retries = max(0, int(cfg.get("final_validation_retries", 1) or 1))
    max_patch_failures = max(1, int(cfg.get("max_patch_dry_run_failures", 2) or 2))
    feedback = ""
    final_failures = 0

    while session.turn < max_turns:
        if _deadline_exceeded(config):
            text = "A tarefa excedeu o prazo de execução."
            return _return("failed", text, None, _details(session, "failed", config, failure_code="TASK_DEADLINE_EXCEEDED"), full)
        session.turn += 1
        try:
            decision, allowed = _call_agent(session, config, project, conversation_context, feedback)
            session.parse_failures = 0
        except ErroLLM as error:
            text = f"A chamada LLM falhou: {error.error_code or 'LLM_FAILED'}."
            return _return("failed", text, None, _details(session, "failed", config, limitations=[str(error)], failure_code=error.error_code or "LLM_FAILED"), full)
        except Exception as error:
            session.parse_failures += 1
            if session.parse_failures <= parse_retries:
                feedback = f"PROTOCOL_ERROR: {error}. Return exactly one valid JSON decision."
                continue
            text = "A LLM não produziu uma decisão estruturada válida."
            return _return("failed", text, None, _details(session, "failed", config, limitations=[str(error)], failure_code="AGENT_JSON_INVALID"), full)

        if isinstance(decision.get("plan"), list):
            session.plan = decision["plan"][:20]

        if decision.get("needs_user"):
            text = str(decision["needs_user"])
            pending = {
                "continuation_kind": "user_input",
                "pergunta_ao_usuario": text,
                "estado": session.to_dict(),
                "tool_pendente": {"tool": "__user_response__", "arguments": {}},
            }
            return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

        if "final" in decision:
            ok, reason, answer, limitations = validate_final(decision["final"], session.evidence)
            if ok:
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)
            final_failures += 1
            if final_failures <= final_retries:
                feedback = f"FINAL_VALIDATION_ERROR: {reason}. Return a corrected final answer."
                continue
            text = f"A conclusão final ficou inválida: {reason}."
            return _return("failed", text, None, _details(session, "failed", config, failure_code=reason), full)

        calls = decision.get("tool_calls") if isinstance(decision.get("tool_calls"), list) else [decision]
        calls = [call for call in calls if isinstance(call, dict) and call.get("tool")]
        if not calls:
            feedback = "Choose one available tool, ask a blocking question, or return final."
            continue

        next_results: List[Dict[str, Any]] = []
        for call in calls[:4]:
            if session.tool_calls >= max_tool_calls:
                text = "A tarefa atingiu o limite de ferramentas antes de concluir."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="MAX_TOOL_CALLS_EXCEEDED"), full)
            tool = str(call.get("tool") or "")
            arguments = call.get("arguments") or {}
            if tool == "test_patch_dry_run":
                arguments, patch_error = _enrich_single_patch(session, arguments)
                if patch_error:
                    session.patch_failures += 1
                    next_results.append({
                        "tool": tool, "status": "failed", "ok": False,
                        "error_code": "PATCH_SCHEMA_INVALID", "detail": patch_error,
                    })
                    if session.patch_failures >= max_patch_failures:
                        text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {patch_error}."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_SCHEMA_INVALID"), full)
                    continue
            if tool == "test_patch_set_dry_run":
                arguments, patch_error = _enrich_patch_set(session, project, arguments)
                if patch_error:
                    session.patch_failures += 1
                    next_results.append({
                        "tool": tool, "status": "failed", "ok": False,
                        "error_code": "PATCH_SCHEMA_INVALID", "detail": patch_error,
                    })
                    if session.patch_failures >= max_patch_failures:
                        text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {patch_error}."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_SCHEMA_INVALID"), full)
                    continue
            if tool not in allowed:
                next_results.append({
                    "tool": tool, "status": "failed", "ok": False,
                    "error_code": "TOOL_NOT_AVAILABLE",
                    "detail": "A ferramenta não está disponível neste workspace/configuração.",
                })
                continue
            normalized, error = validar_chamada_tool(tool, arguments)
            if error:
                next_results.append(_compact_non_read_result(tool, error))
                if tool in PATCH_TOOLS:
                    session.patch_failures += 1
                    if session.patch_failures >= max_patch_failures:
                        text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {error.get('detail')}."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code=error.get("error_code") or "PATCH_SCHEMA_INVALID"), full)
                continue
            signature = _action_signature(tool, normalized)
            if signature == session.last_tool_signature:
                session.consecutive_identical_calls += 1
            else:
                session.last_tool_signature = signature
                session.consecutive_identical_calls = 1
            if session.consecutive_identical_calls > max_identical:
                text = "A LLM repetiu exatamente a mesma ferramenta várias vezes sem mudar a ação."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="IDENTICAL_TOOL_LOOP"), full)
            if tool in READ_TOOLS and session.consecutive_identical_calls > 1:
                next_results.append({
                    "tool": tool, "status": "skipped", "ok": False,
                    "executed": False, "changed": False,
                    "error_code": "IDENTICAL_READ_BLOCKED",
                    "detail": "A mesma leitura fresca já está disponível. Use o resultado atual, conclua ou escolha outra faixa/arquivo.",
                })
                continue

            context = {"config": config, "projeto": project, "evidence": session.evidence}
            result = executar_tool(tool, normalized, context)
            session.tool_calls += 1
            session.tool_history.append({
                "tool": tool,
                "status": result.get("status"),
                "error_code": result.get("error_code"),
            })
            model_result = _model_tool_result(session, tool, result)
            next_results.append(model_result)
            if tool in PATCH_TOOLS and result.get("ok") is not True:
                session.patch_failures += 1
                if session.patch_failures >= max_patch_failures:
                    detail = result.get("detail")
                    text = f"O dry-run da escrita falhou {session.patch_failures} vez(es): {result.get('error_code') or 'DRY_RUN_FAILED'} — {detail}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=result.get("error_code") or "DRY_RUN_FAILED"), full)

            if tool == "test_patch_dry_run" and result.get("ok"):
                evidence = _find_patch_evidence(session, normalized)
                if evidence is None:
                    next_results.append({"tool": tool, "status": "failed", "error_code": "PATCH_REQUIRES_FRESH_EXACT_READ"})
                    continue
                text, pending = _pending_single_patch(session, normalized, evidence)
                return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

            if tool == "test_patch_set_dry_run" and result.get("ok"):
                detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
                text, pending = _pending_patch_set(session, detail)
                return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

            if not result.get("ok") and result.get("error_code") in TERMINAL_TOOL_ERRORS:
                text = f"A ferramenta encontrou um erro terminal: {result.get('error_code')}."
                return _return("failed", text, None, _details(session, "failed", config, failure_code=result.get("error_code")), full)

        session.latest_tool_results = _preserve_source_for_retry(session.latest_tool_results, next_results)
        feedback = ""

    text = "A tarefa atingiu o limite de turnos do agente antes de concluir."
    return _return("failed", text, None, _details(session, "failed", config, failure_code="MAX_LLM_TURNS_EXCEEDED"), full)


def executar_agente(
    objetivo: str,
    config: Dict[str, Any],
    projeto: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None,
    retornar_detalhes: bool = False,
    task_id: Optional[str] = None,
    conversation_context: Any = None,
    resposta_usuario: Optional[str] = None,
):
    """Run or resume the single AgentSession."""
    full = bool(retornar_detalhes)
    project = projeto or {}
    if retomar:
        session = AgentSession.from_dict(retomar.get("estado") or {})
        pending_tool = (retomar.get("tool_pendente") or {}).get("tool")
        if pending_tool == "__user_response__":
            session.latest_tool_results = [{
                "tool": "user_response", "status": "success", "ok": True,
                "detail": str(resposta_usuario or ""),
            }]
            return _run(session, config, project, full, conversation_context=None)
        return _resume(session, retomar, config, project, full)
    session = AgentSession(str(objetivo or ""), task_id=task_id)
    return _run(session, config, project, full, conversation_context=conversation_context)
