#!/usr/bin/env python3
"""Compilador de contexto do agente unico da Eyle 2.7.4."""
import hashlib
import json
import os

from engine.context_engine import calcular_orcamento_evidencias, selecionar_evidencias

_MODEL_STATE_TEXT = {
    "resposta_direta": "direct_response",
    "sem_ferramentas_de_projeto": "no_project_tools",
    "Responder ao usuario": "Reply to the user",
    "codigo_fresco_lido": "fresh_code_read",
    "sugestoes_grounded": "grounded_suggestions",
    "sem_escrita": "no_write",
    "somente_leitura": "read_only",
    "uma_acao_por_decisao": "one_action_per_decision",
    "Ler o codigo fresco do alvo": "Read fresh code for the target",
    "Propor melhorias com evidencias": "Propose improvements grounded in evidence",
    "Localizar o componente relevante": "Locate the relevant component",
    "Ler o codigo fresco necessario": "Read the required fresh code",
    "codigo_fresco_relevante": "fresh_relevant_code",
    "confirmacao_explicita": "explicit_confirmation",
    "mudanca_verificada": "verified_change",
    "escrita_confirmada": "confirmed_write",
    "Localizar e ler o alvo": "Locate and read the target",
    "Preparar a mudanca": "Prepare the change",
    "Confirmar e aplicar": "Confirm and apply",
    "Testar e reler o resultado": "Test and re-read the result",
    "evidencia_pos_escrita": "post_write_evidence",
    "resposta_grounded": "grounded_answer",
    "Responder com evidencias": "Answer with evidence",
    "Mapear o projeto com list_tree": "Map the project with list_tree",
}

def _compactar_catalogo_tools(catalogo_tools):
    """Projecao pequena do schema real, sem perder nome/tipo/limite/saida."""
    compacto = []
    for tool in catalogo_tools or []:
        schema = tool.get("input_schema") or {}
        obrigatorios = set(schema.get("required") or [])
        argumentos = {}
        for nome, contrato in (schema.get("properties") or {}).items():
            tipo = contrato.get("type", "any")
            if "minimum" in contrato:
                tipo += f">={contrato['minimum']}"
            if nome in obrigatorios:
                tipo += "!"
            argumentos[nome] = tipo
        saida = str(tool.get("output_schema") or "")
        for prefixo in ("Standard envelope; ", "Envelope padrao; "):
            if saida.startswith(prefixo):
                saida = saida[len(prefixo):]
                break
        compacto.append({
            "name": tool.get("name"),
            "description": tool.get("description"),
            "permission": tool.get("permission"),
            "arguments": argumentos,
            "limits": tool.get("limits") or {},
            "returns": saida,
        })
    return compacto

def _valor_para_modelo(valor):
    """Translate only deterministic state-machine text shown to the model.

    The original user objective and arbitrary user-provided blocker text remain
    untouched, so no translation layer can distort the request.
    """
    if isinstance(valor, dict):
        return {chave: _valor_para_modelo(item) for chave, item in valor.items()}
    if isinstance(valor, list):
        return [_valor_para_modelo(item) for item in valor]
    if isinstance(valor, str):
        return _MODEL_STATE_TEXT.get(valor, valor)
    return valor

def _hash_curto_arquivo(caminho):
    h = hashlib.sha256()
    try:
        with open(caminho, "rb") as handle:
            for bloco in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(bloco)
    except OSError:
        return None
    return h.hexdigest()[:16]

def _hash_entendimento_confere(caminho_relativo, item, projeto):
    raiz = (projeto or {}).get("caminho_origem") if isinstance(projeto, dict) else None
    esperado = (item or {}).get("hash") if isinstance(item, dict) else None
    if not raiz or not esperado:
        return False
    raiz_real = os.path.realpath(os.path.abspath(str(raiz)))
    alvo = os.path.realpath(os.path.abspath(os.path.join(raiz_real, str(caminho_relativo))))
    try:
        if os.path.commonpath((raiz_real, alvo)) != raiz_real:
            return False
    except ValueError:
        return False
    atual = _hash_curto_arquivo(alvo)
    return bool(atual and atual == str(esperado))

def bloco_entendimento(entendimento, projeto=None):
    """Renderiza memoria indexada somente como pista de navegacao.

    A revisao 55.19 impede que ``entendimento.json`` apareca antes das
    evidencias como se fosse estado atual observado. Entradas por arquivo so
    recebem o selo ``HASH_MATCHES_DISK`` quando o hash persistido corresponde
    ao arquivo real; qualquer outra entrada permanece explicitamente
    ``UNTRUSTED_NAVIGATION_HINT`` e nao pode sustentar claims finais.
    """
    partes = [
        "UNTRUSTED NAVIGATION HINT (memory/entendimento.json):",
        "Unverified entries are clues only. HASH_VERIFIED_NAVIGATION_FACT means the indexed file hash still matches disk; project-audit final claims still require fresh Evidence Registry IDs.",
    ]
    adicionou = False

    arquivos = (entendimento or {}).get("arquivos", {})
    if isinstance(arquivos, dict):
        for caminho, item in sorted(arquivos.items()):
            if not isinstance(item, dict):
                continue
            descricao = item.get("responsabilidade") or item.get("funcao")
            if not descricao:
                continue
            confiavel = _hash_entendimento_confere(caminho, item, projeto)
            selo = "HASH_VERIFIED_NAVIGATION_FACT" if confiavel else "UNTRUSTED_NAVIGATION_HINT"
            partes.append(f"- [{selo}] {caminho}: {descricao}")
            adicionou = True

    componentes = (entendimento or {}).get("componentes", {})
    if isinstance(componentes, dict):
        for nome, item in componentes.items():
            if not isinstance(item, dict) or not item.get("funcao"):
                continue
            partes.append(f"- [UNTRUSTED_NAVIGATION_HINT] {nome}: {item['funcao']}")
            adicionou = True

    return partes if adicionou else []

def _proxima_acao_edicao(goal_state, evidencias, actions, edit_state):
    """Resume o proximo gate deterministico do ciclo de edicao."""
    if (goal_state or {}).get("mode") != "edit":
        return None
    frescas = [item for item in (evidencias or []) if item.get("estado") == "fresh"]
    if not frescas:
        return "READ_REQUIRED: use read_range/read_file to obtain fresh code before proposing any patch."
    dry_run_ok = any(
        item.get("tool") == "test_patch_dry_run"
        and item.get("ok") is True
        and item.get("executed") is True
        for item in (actions or [])
    )
    estado_edicao = edit_state or {}
    status = estado_edicao.get("status")
    if not dry_run_ok and not status:
        return "The next action must be test_patch_dry_run for the exact range to change."
    if dry_run_ok and not status:
        return "WRITE_PENDING: the next action must be apply_patch with the same dry-run proposal; the system will request confirmation."
    if status == "applied_pending_tests":
        return "RUN_TESTS_REQUIRED: the next action must be run_tests; do not finalize before verification."
    if status == "tests_passed" and not estado_edicao.get("post_write_evidence_id"):
        return "POST_WRITE_READ_REQUIRED: the next action must be read_range on the final changed range."
    if status == "reverted":
        return "The change was reverted; explain the failure without claiming success."
    return None

def _renderizar_inventario_projeto(inventario):
    """Renderiza todas as entradas retornadas por ``list_tree`` sem corte.

    O bloco faz parte do prompt fixo e entra no calculo do orcamento antes das
    evidencias de codigo. Assim a arvore nao compete silenciosamente com o
    limite historico de 500 caracteres das observacoes recentes.
    """
    if not isinstance(inventario, dict):
        return []
    entradas = [
        item for item in (inventario.get("entradas") or [])
        if isinstance(item, dict) and item.get("caminho")
    ]
    if not entradas:
        return []

    completa = bool(
        inventario.get("varredura_completa") and not inventario.get("truncado")
    )
    metadados = {
        "schema_version": inventario.get("schema_version", 1),
        "inventory_hash": inventario.get("inventory_hash"),
        "total_entries": inventario.get("total_retornado", len(entradas)),
        "files": inventario.get("total_arquivos"),
        "directories": inventario.get("total_diretorios"),
        "complete": completa,
        "limit": inventario.get("limite"),
        "max_depth": inventario.get("profundidade_maxima"),
        "filter": inventario.get("filtro"),
        "ignored_by_reason": inventario.get("ignorados_por_motivo") or {},
        "root_directories": inventario.get("diretorios_raiz") or [],
        "root_files": inventario.get("arquivos_raiz") or [],
        "extensions": inventario.get("extensoes") or {},
    }
    linhas = [
        "PROJECT INVENTORY (complete structured list returned by list_tree; "+
        "never reconstructed from RECENT OBSERVATIONS):",
        json.dumps(metadados, ensure_ascii=False, separators=(",", ":")),
        "ENTRIES (D=directory, F=file; all returned entries follow):",
    ]
    for item in entradas:
        marcador = "D" if item.get("tipo") == "diretorio" else "F"
        caminho = str(item.get("caminho") or "").replace("\\", "/")
        linhas.append(f"{marcador} {caminho}")
    if completa:
        linhas.append(
            "COVERAGE: complete for the configured depth/filter and ignore rules. "
            "Use this full list to choose which source files and tests must be inspected."
        )
    else:
        linhas.append(
            "COVERAGE: PARTIAL. The tool hit a limit or incomplete traversal; do not claim "
            "that files/directories absent from this list do not exist."
        )
    return linhas

def _renderizar_cobertura_auditoria(cobertura):
    if not isinstance(cobertura, dict) or cobertura.get("task_type") != "project_audit":
        return []
    resumo = {
        "criteria": cobertura.get("criteria") or {},
        "missing": cobertura.get("missing") or [],
        "failure_code": cobertura.get("failure_code"),
        "inventory": cobertura.get("inventory") or {},
        "reads": cobertura.get("reads") or {},
        "coverage": cobertura.get("coverage") or {},
        "critical_components": cobertura.get("critical_components") or {},
        "test_execution": cobertura.get("test_execution") or {},
        "minimum_code_files_required": cobertura.get("minimum_code_files_required"),
        "next_read_candidates": cobertura.get("next_read_candidates") or [],
    }
    return [
        "PROJECT AUDIT COVERAGE (system-calculated; the model cannot override it):",
        json.dumps(resumo, ensure_ascii=False, separators=(",", ":")),
        "AUDIT RULES: README, CHANGELOG and docs/** are context only and NEVER satisfy source-code reading. "
        "Do not return final while any structural criterion is missing. Use the next candidate or another READ tool. "
        "The system will publish measured coverage separately; do not invent coverage percentages or claim universal bug absence.",
    ]

def montar_prompt_agente(pergunta, observacoes=None, entendimento=None, max_entradas=4,
                         fatos_importantes=None, catalogo_tools=None,
                         goal_state=None, evidencias=None, actions=None,
                         edit_state=None, project_inventory=None, analysis_coverage=None,
                         config=None, system_prompt="", projeto=None):
    """Monta um passo do Agente com contexto virtual orcado (Atualizacao 42).

    Metadados, acoes e observacoes continuam compactos. Codigo real fica em
    evidencias estruturadas persistentes e entra por relevancia ate o saldo
    dinamico da janela ``llm.context_window_tokens``. O retrieval antigo
    conserva seu proprio ``context.token_budget`` e nao participa desta conta.
    """
    cfg_contexto = (config or {}).get("context_engine", {})
    max_entradas = cfg_contexto.get("max_recent_observations", max_entradas)
    partes = []
    if entendimento:
        bloco = bloco_entendimento(entendimento, projeto=projeto)
        if bloco:
            partes.extend(bloco)
            partes.append("")

    goal_state = goal_state or {
        "objective": pergunta,
        "task_type": "chat",
        "status": "in_progress",
    }
    partes.append("ORIGINAL USER REQUEST (preserve exactly; answer in the same language):")
    partes.append(pergunta)
    partes.append("")
    partes.append("GOAL STATE (internal protocol in English):")
    partes.append(json.dumps(_valor_para_modelo(goal_state), ensure_ascii=False, separators=(",", ":")))
    proxima_acao = _proxima_acao_edicao(goal_state, evidencias, actions, edit_state)
    if proxima_acao:
        partes.append("MANDATORY NEXT EDIT ACTION:")
        partes.append(proxima_acao)
    partes.append("")

    bloco_inventario = _renderizar_inventario_projeto(project_inventory)
    if bloco_inventario:
        partes.extend(bloco_inventario)
        partes.append("")

    bloco_cobertura = _renderizar_cobertura_auditoria(analysis_coverage)
    if bloco_cobertura:
        partes.extend(bloco_cobertura)
        partes.append("")

    catalogo_tools = catalogo_tools or []
    partes.append(
        "TOOL CATALOG (generated from the executable registry; "
        "use only tool names and arguments declared here):"
    )
    partes.append(json.dumps(
        _compactar_catalogo_tools(catalogo_tools),
        ensure_ascii=False,
        separators=(",", ":"),
    ))
    partes.append("")

    fatos_importantes = fatos_importantes or []
    if fatos_importantes:
        partes.append(
            "IMPORTANT FACTS (auxiliary model memory; never a substitute for tool evidence):"
        )
        for fato in fatos_importantes:
            partes.append(f"- {fato}")
        partes.append("")

    actions = actions or []
    if actions:
        recentes_acoes = actions[-max_entradas:]
        partes.append(
            f"ACTIONS (last {len(recentes_acoes)} of {len(actions)}; long results are not duplicated):"
        )
        for acao in recentes_acoes:
            partes.append(json.dumps(acao, ensure_ascii=False, separators=(",", ":")))
        partes.append("")

    observacoes = observacoes or []
    if observacoes:
        recentes = observacoes[-max_entradas:]
        omitidas = len(observacoes) - len(recentes)
        partes.append(
            f"RECENT OBSERVATIONS (last {len(recentes)} of {len(observacoes)}; "
            "full code lives in EVIDENCE, not in these summaries):"
        )
        if omitidas > 0:
            partes.append(
                f"(+ {omitidas} older observation(s) omitted; associated evidence remains preserved)"
            )
        for i, obs in enumerate(recentes, start=1):
            partes.append(f"\n{i}. tool: {obs.get('tool')}")
            partes.append(f"   summary: {obs.get('resumo')}")
    else:
        partes.append("RECENT OBSERVATIONS: (no action has been executed in this task yet)")

    failed_actions = [
        item for item in actions
        if isinstance(item, dict) and item.get("ok") is False
    ]
    if failed_actions:
        last_failure = failed_actions[-1]
        partes.extend([
            "",
            "TOOL FAILURE REPAIR (system-owned):",
            "- Read the exact error_code and error_detail below before choosing the next action.",
            "- Never repeat the same invalid tool call unchanged.",
            "- If retryable=true, correct arguments or refresh evidence and retry once.",
            "- If terminal=true, do not retry; return a clear failure with the observed detail.",
            json.dumps(last_failure, ensure_ascii=False, separators=(",", ":")),
        ])

    evidencias = evidencias or []
    stale = [item for item in evidencias if item.get("estado") == "stale"]
    if stale:
        partes.append("")
        partes.append(
            "STALE EVIDENCE (do not use in the final answer; re-read the file): "
            + ", ".join(
                f"{item.get('id')}={item.get('arquivo')}:"
                f"{item.get('linha_inicio')}-{item.get('linha_fim')}"
                for item in stale
            )
        )

    cfg_llm = (config or {}).get("llm", {})
    partes.append("")
    partes.append(
        "CONTEXT BUDGET: window={} tokens; reserved response={}; "
        "safety margin={}.".format(
            cfg_llm.get("context_window_tokens", 2048),
            cfg_llm.get("max_tokens", 0) or 0,
            cfg_contexto.get("safety_margin_tokens", 256),
        )
    )
    partes.append(
        "SELECTED FRESH EVIDENCE (full content remains in state by evidence_id):"
    )
    prompt_sem_evidencias = "\n".join(partes)
    orcamento = calcular_orcamento_evidencias(
        config or {}, system_prompt, prompt_sem_evidencias,
    )
    selecao = selecionar_evidencias(
        evidencias,
        pergunta,
        orcamento["evidence_budget_tokens"],
        orcamento["chars_per_token"],
    )
    if selecao["selecionadas"]:
        for item in selecao["selecionadas"]:
            partes.append("")
            partes.append(item["bloco"])
    else:
        partes.append("(no fresh evidence is available or fits in this step)")

    return "\n".join(partes)

def montar_prompt_scout_auditoria(
    pergunta,
    candidate_catalog,
    *,
    phase="initial",
    analysis_coverage=None,
    evidencias=None,
    pipeline_state=None,
    config=None,
    system_prompt="",
):
    """Prompt de planejamento; no gap review inclui codigo real selecionado."""
    catalog = candidate_catalog or {}
    projection = {
        "schema_version": catalog.get("schema_version"),
        "inventory_hash": catalog.get("inventory_hash"),
        "inventory_complete": catalog.get("inventory_complete"),
        "counts": catalog.get("counts") or {},
        "required_slots": catalog.get("required_slots") or [],
        "candidates": catalog.get("candidates") or [],
    }
    fresh = [
        {
            "id": item.get("id"),
            "path": item.get("arquivo"),
            "lines": [item.get("linha_inicio"), item.get("linha_fim")],
            "complete": item.get("leitura_completa"),
        }
        for item in (evidencias or [])
        if isinstance(item, dict) and item.get("estado") == "fresh"
    ]
    partes = [
        "ORIGINAL USER REQUEST:",
        str(pergunta or ""),
        "",
        f"SCOUT PHASE: {phase}",
        "CANDIDATE CATALOG (system-generated; select paths only from this list):",
        json.dumps(projection, ensure_ascii=False, separators=(",", ":")),
        "",
        "CURRENT SYSTEM COVERAGE:",
        json.dumps(analysis_coverage or {}, ensure_ascii=False, separators=(",", ":")),
        "",
        "ALREADY READ FRESH EVIDENCE:",
        json.dumps(fresh, ensure_ascii=False, separators=(",", ":")),
        "",
        "PIPELINE STATE:",
        json.dumps(pipeline_state or {}, ensure_ascii=False, separators=(",", ":")),
    ]

    # Na fase inicial, caminhos e papeis bastam. Na revisao de lacunas, o
    # Scout precisa ver codigo real para formular riscos e pedir leituras que
    # testem hipoteses concretas.
    if str(phase) == "gap_review" and fresh:
        config = config or {}
        cfg_llm = config.get("llm", {})
        budget_config = dict(config)
        budget_llm = dict(cfg_llm)
        budget_llm["max_tokens"] = budget_llm.get(
            "audit_scout_max_tokens", budget_llm.get("agent_max_tokens", 700)
        )
        budget_config["llm"] = budget_llm
        partes.extend(["", "FRESH CODE EVIDENCE FOR RISK/GAP REVIEW:"])
        fixed = "\n".join(partes)
        budget = calcular_orcamento_evidencias(
            budget_config, system_prompt, fixed,
        )
        selection = selecionar_evidencias(
            evidencias or [], pergunta,
            budget["evidence_budget_tokens"],
            budget["chars_per_token"],
        )
        if selection["selecionadas"]:
            for item in selection["selecionadas"]:
                partes.extend(["", item["bloco"]])
        else:
            partes.append("(no fresh code evidence fits in the Scout context)")

    partes.extend([
        "",
        "Choose a compact evidence plan. Do not answer the user.",
        "During gap_review, every additional path must test a named risk or close a concrete missing area.",
    ])
    return "\n".join(partes)

def montar_prompt_finalizer_auditoria(
    pergunta,
    *,
    goal_state=None,
    analysis_coverage=None,
    project_inventory=None,
    audit_pipeline=None,
    task_contract=None,
    evidencias=None,
    config=None,
    system_prompt="",
):
    """Compila somente a conclusao da auditoria, sem catalogo de tools."""
    partes = [
        "ORIGINAL USER REQUEST:",
        str(pergunta or ""),
        "",
        "GOAL STATE (system-owned):",
        json.dumps(_valor_para_modelo(goal_state or {}), ensure_ascii=False, separators=(",", ":")),
        "",
        "TASK INTENT (system-owned; answer only the requested profile):",
        json.dumps(_valor_para_modelo(task_contract or {}), ensure_ascii=False, separators=(",", ":")),
        "",
        "PROJECT AUDIT COVERAGE (system-calculated):",
        json.dumps(analysis_coverage or {}, ensure_ascii=False, separators=(",", ":")),
        "",
        "AUDIT PIPELINE (Scout selections and read phases; planning metadata only):",
        json.dumps(audit_pipeline or {}, ensure_ascii=False, separators=(",", ":")),
        "",
    ]
    inventory = project_inventory or {}
    partes.extend([
        "INVENTORY SUMMARY:",
        json.dumps({
            "inventory_hash": inventory.get("inventory_hash"),
            "complete": bool(inventory.get("varredura_completa") and not inventory.get("truncado")),
            "total_entries": inventory.get("total_retornado"),
            "files": inventory.get("total_arquivos"),
            "directories": inventory.get("total_diretorios"),
            "root_directories": inventory.get("diretorios_raiz") or [],
            "root_files": inventory.get("arquivos_raiz") or [],
            "extensions": inventory.get("extensoes") or {},
        }, ensure_ascii=False, separators=(",", ":")),
        "",
        "SELECTED FRESH EVIDENCE:",
    ])

    config = config or {}
    cfg_contexto = config.get("context_engine", {})
    cfg_llm = config.get("llm", {})
    prompt_sem_evidencias = "\n".join(partes)
    budget_config = dict(config)
    budget_llm = dict(cfg_llm)
    budget_llm["max_tokens"] = budget_llm.get(
        "audit_finalizer_max_tokens", budget_llm.get("max_tokens", 0)
    )
    budget_config["llm"] = budget_llm
    orcamento = calcular_orcamento_evidencias(
        budget_config,
        system_prompt,
        prompt_sem_evidencias,
    )
    selecao = selecionar_evidencias(
        evidencias or [],
        pergunta,
        orcamento["evidence_budget_tokens"],
        orcamento["chars_per_token"],
    )
    if selecao["selecionadas"]:
        for item in selecao["selecionadas"]:
            partes.extend(["", item["bloco"]])
    else:
        partes.append("(no fresh evidence fits in the finalizer context)")

    recommendation_count = (task_contract or {}).get("recommendation_count")
    recommendation_rule = (
        f"- Return exactly {recommendation_count} recommendation claims, each with type=recommendation, "
        "output=recommendations, and a concrete basis."
        if isinstance(recommendation_count, int) else
        "- Recommendation claims are allowed only when recommendations_requested=true."
    )
    partes.extend([
        "",
        "FINALIZER CONTRACT:",
        "- No tools are available in this phase.",
        "- Do not repeat release notes as proof of current behavior.",
        "- Do not claim tests passed unless an executed run_tests result is present.",
        "- Return atomic claims with visible evidence_ids and report limitations honestly.",
        "- Obey TASK INTENT: do not add recommendations when recommendations_requested=false.",
        recommendation_rule,
        "- Tag each claim with one requested output using the output field; absence claims must declare scope.",
        "- Do not return answer or claim_annotations; the system renders validated claims.",
        "- Return only the required JSON claims envelope.",
        "CONTEXT BUDGET: window={} tokens; reserved response={}; safety margin={}.".format(
            cfg_llm.get("context_window_tokens", 2048),
            cfg_llm.get("audit_finalizer_max_tokens", cfg_llm.get("max_tokens", 0)) or 0,
            cfg_contexto.get("safety_margin_tokens", 256),
        ),
    ])
    return "\n".join(partes)

def montar_prompt_finalizer_leitura(
    pergunta, *, goal_state=None, evidencias=None, actions=None, config=None, system_prompt="",
    task_contract=None, repair_feedback="", prior_claims=None,
):
    """Compila a resposta final de project_read sem catalogo de tools."""
    partes = [
        "ORIGINAL USER REQUEST:",
        str(pergunta or ""),
        "",
        "GOAL STATE (system-owned):",
        json.dumps(_valor_para_modelo(goal_state or {}), ensure_ascii=False, separators=(",", ":")),
        "",
        "TASK CONTRACT (system-owned; every required target must be covered):",
        json.dumps(_valor_para_modelo(task_contract or {}), ensure_ascii=False, separators=(",", ":")),
        "",
        "EXECUTED ACTIONS (navigation metadata; facts still require evidence_ids):",
        json.dumps(_valor_para_modelo(actions or []), ensure_ascii=False, separators=(",", ":")),
        "",
        "SELECTED FRESH EVIDENCE:",
    ]
    config = config or {}
    cfg_contexto = config.get("context_engine", {})
    cfg_llm = config.get("llm", {})
    budget_config = dict(config)
    budget_llm = dict(cfg_llm)
    budget_llm["max_tokens"] = budget_llm.get(
        "project_read_finalizer_max_tokens", budget_llm.get("max_tokens", 0)
    )
    budget_config["llm"] = budget_llm
    prompt_sem_evidencias = "\n".join(partes)
    orcamento = calcular_orcamento_evidencias(
        budget_config, system_prompt, prompt_sem_evidencias,
    )
    selecao = selecionar_evidencias(
        evidencias or [], pergunta,
        orcamento["evidence_budget_tokens"], orcamento["chars_per_token"],
    )
    for item in selecao.get("selecionadas") or []:
        partes.extend(["", item["bloco"]])
    if not selecao.get("selecionadas"):
        partes.append("(no fresh evidence fits; do not invent an answer)")
    if prior_claims:
        partes.extend([
            "",
            "PRIOR VALIDATED/REJECTED CLAIMS (repair context only):",
            json.dumps(_valor_para_modelo(prior_claims), ensure_ascii=False, separators=(",", ":")),
        ])
    if repair_feedback:
        partes.extend(["", str(repair_feedback)])
    recommendation_count = (task_contract or {}).get("recommendation_count")
    recommendation_rule = (
        f"- Return exactly {recommendation_count} recommendation claims, each with type=recommendation, "
        "output=recommendations, and a concrete basis."
        if isinstance(recommendation_count, int) else
        "- Do not include recommendation claims unless TASK CONTRACT recommendations_requested=true."
    )
    partes.extend([
        "",
        "FINALIZER CONTRACT:",
        "- No tools are available.",
        "- Answer the exact target in the original request.",
        "- Cover every TASK CONTRACT required_target and requested_output; include evidence-derived literal values when requested.",
        recommendation_rule,
        "- For code_explanation, explain only the requested file/symbol behavior; never enter a write workflow.",
        "- For code_conversation, distinguish observed facts from inferences and give every inference a basis.",
        "- A negative find_symbol observation proves explicit absence; absence claims must include their reviewed scope. BM25/search_code alone does not.",
        "- Return only the required JSON final envelope.",
        "CONTEXT BUDGET: window={} tokens; reserved response={}; safety margin={}.".format(
            cfg_llm.get("context_window_tokens", 2048),
            cfg_llm.get("project_read_finalizer_max_tokens", cfg_llm.get("max_tokens", 0)) or 0,
            cfg_contexto.get("safety_margin_tokens", 256),
        ),
    ])
    return "\n".join(partes)
