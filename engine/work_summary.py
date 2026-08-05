#!/usr/bin/env python3
"""Resumo operacional publico de uma tarefa da Eyle.

O objetivo deste modulo e transformar o resultado estruturado do Engine em um
relato curto e auditavel para a interface. Ele nao publica prompts, chain of
thought, observacoes internas, conteudo bruto de arquivos nem respostas JSON da
LLM. Apenas descreve o trabalho observavel: objetivo, leituras, ferramentas,
evidencias, gates e limitacoes.
"""
from __future__ import annotations

from collections import OrderedDict

_MAX_TEXT = 500
_MAX_LIMITATIONS = 8
_MAX_FILES = 20


def _texto(valor, limite=_MAX_TEXT):
    if valor is None:
        return ""
    texto = " ".join(str(valor).split())
    return texto[: max(1, int(limite))]


def _unicos(valores):
    vistos = set()
    saida = []
    for valor in valores or []:
        texto = _texto(valor, 160)
        if not texto or texto in vistos:
            continue
        vistos.add(texto)
        saida.append(texto)
    return saida


def _campo(rotulo, valor):
    return {"label": _texto(rotulo, 80), "value": _texto(valor, 1000)}


def _etapa(numero, titulo, campos):
    return {
        "number": int(numero),
        "title": _texto(titulo, 80),
        "fields": [campo for campo in campos if campo.get("value")],
    }




def _itens_registry(detalhes):
    registry = detalhes.get("evidence_registry")
    if not isinstance(registry, dict):
        return []
    items = registry.get("items")
    return [item for item in (items or []) if isinstance(item, dict)]


def _rotulos_evidencia(detalhes, contexto):
    items = _itens_registry(detalhes)
    if items:
        rotulos = []
        for item in items[:_MAX_FILES * 4]:
            evidence_id = _texto(item.get("id"), 80)
            arquivo = _texto(item.get("arquivo"), 300)
            inicio = item.get("linha_inicio")
            fim = item.get("linha_fim")
            faixa = ""
            if arquivo and isinstance(inicio, int) and isinstance(fim, int):
                faixa = f"{arquivo}:{inicio}-{fim}" if inicio != fim else f"{arquivo}:{inicio}"
            estado = _texto(item.get("estado"), 40)
            base = " = ".join(valor for valor in (evidence_id, faixa) if valor)
            if base and estado and estado != "fresh":
                base += f" ({estado})"
            if base:
                rotulos.append(base)
        return _unicos(rotulos)
    return _unicos(detalhes.get("evidence_ids") or contexto.get("evidence_ids"))

def _cobertura_auditoria(detalhes):
    cobertura = detalhes.get("analysis_coverage")
    if not isinstance(cobertura, dict) or cobertura.get("task_type") != "project_audit":
        return ""
    criterios = cobertura.get("criteria") or {}
    aprovados = sum(1 for valor in criterios.values() if valor is True)
    total = len(cobertura.get("required_criteria") or criterios)
    metricas = cobertura.get("coverage") or {}
    pendentes = cobertura.get("missing") or []
    texto = (
        f"{aprovados}/{total} critérios; "
        f"nível={metricas.get('level') or 'não medido'}; "
        f"inventário={'completo' if metricas.get('inventory_complete') else 'parcial'}; "
        f"código={metricas.get('code_files_read', 0)}/{metricas.get('code_files_total', 0)}; "
        f"componentes críticos={metricas.get('critical_components_read', 0)}/"
        f"{metricas.get('critical_components_total', 0)}; "
        f"testes executados={'sim' if metricas.get('tests_executed') else 'não'}; "
        f"documentos usados={metricas.get('docs_used', 0)}"
    )
    if pendentes:
        texto += "; pendentes=" + ", ".join(str(item) for item in pendentes)
    return texto



def _pipeline_auditoria(detalhes):
    pipeline = detalhes.get("audit_pipeline")
    if not isinstance(pipeline, dict) or not pipeline:
        return ""
    initial = pipeline.get("initial_scout") or {}
    gap = pipeline.get("gap_scout") or {}
    completed = pipeline.get("completed_reads") or []
    failed = pipeline.get("failed_reads") or []
    phase = _texto(pipeline.get("phase"), 80) or "desconhecida"
    texto = (
        "Scout -> leituras automaticas -> revisao de lacunas -> Finalizer; "
        f"fase={phase}; selecionados iniciais={len(initial.get('selected_paths') or [])}; "
        f"lacunas adicionais={len(gap.get('selected_paths') or [])}; "
        f"leituras concluidas={len(completed)}; finalizer_calls={pipeline.get('finalizer_calls') or 0}"
    )
    if failed:
        texto += f"; leituras com falha={len(failed)}"
    return texto

def _erros_ferramenta(detalhes):
    items = detalhes.get("tool_errors")
    if not isinstance(items, list):
        return ""
    lines = []
    for item in items[-3:]:
        if not isinstance(item, dict):
            continue
        tool = _texto(item.get("tool"), 80) or "desconhecida"
        code = _texto(item.get("error_code"), 120) or "TOOL_FAILED"
        detail = _texto(item.get("error_detail"), 320) or "sem detalhe"
        retryable = item.get("retryable")
        retry_text = "sim" if retryable is True else "não" if retryable is False else "não informado"
        lines.append(f"{tool}: {code}; retryable={retry_text}; {detail}")
    return "\n".join(lines)


def _task_intent(detalhes):
    intent = detalhes.get("task_intent")
    if not isinstance(intent, dict):
        contract = detalhes.get("task_contract")
        intent = contract if isinstance(contract, dict) else {}
    return intent


def _sim_nao(value):
    if value is True:
        return "sim"
    if value is False:
        return "não"
    return "não informado"


def _modo(resultado, detalhes, contexto):
    roteador = resultado.get("roteador") if isinstance(resultado, dict) else {}
    roteador = roteador if isinstance(roteador, dict) else {}
    for valor in (
        detalhes.get("mode"), roteador.get("modo"), contexto.get("modo"),
    ):
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    tipo = str(roteador.get("tipo") or "").strip()
    if tipo == "agente":
        return "analyze"
    return tipo or "unknown"


def _leituras(detalhes, contexto):
    evidencias = _itens_registry(detalhes)
    if not evidencias:
        evidencias = detalhes.get("evidencias_usadas")
    if not isinstance(evidencias, list) or not evidencias:
        evidencias = contexto.get("arquivos_lidos")
    if not isinstance(evidencias, list):
        evidencias = []

    agrupadas = OrderedDict()
    for item in evidencias[:_MAX_FILES * 4]:
        if not isinstance(item, dict):
            continue
        arquivo = _texto(item.get("arquivo"), 300)
        if not arquivo:
            continue
        inicio = item.get("linha_inicio")
        fim = item.get("linha_fim")
        total = item.get("total_linhas_arquivo")
        completo_explicito = item.get("leitura_completa")
        truncado = bool(item.get("truncado"))
        atual = agrupadas.setdefault(arquivo, {
            "arquivo": arquivo,
            "inicio": None,
            "fim": None,
            "total": None,
            "completo": False,
        })
        if isinstance(inicio, int) and inicio >= 1:
            atual["inicio"] = inicio if atual["inicio"] is None else min(atual["inicio"], inicio)
        if isinstance(fim, int) and fim >= 1:
            atual["fim"] = fim if atual["fim"] is None else max(atual["fim"], fim)
        if isinstance(total, int) and total >= 0:
            atual["total"] = total
        if isinstance(completo_explicito, bool):
            atual["completo"] = atual["completo"] or completo_explicito
        elif (
            not truncado
            and atual["inicio"] == 1
            and isinstance(atual["fim"], int)
            and isinstance(atual["total"], int)
            and atual["fim"] >= atual["total"]
        ):
            atual["completo"] = True
    return list(agrupadas.values())[:_MAX_FILES]


def _campos_leitura(leituras, ferramentas):
    if not leituras:
        if any(tool in {"list_tree", "read_metadata"} for tool in ferramentas):
            return [
                _campo("Arquivo lido", "nenhum arquivo confirmado"),
                _campo("Leitura completa", "não; apenas a estrutura foi consultada"),
            ]
        return [
            _campo("Arquivo lido", "nenhum arquivo registrado"),
            _campo("Leitura completa", "não"),
        ]

    if len(leituras) == 1:
        leitura = leituras[0]
        inicio = leitura.get("inicio")
        fim = leitura.get("fim")
        faixa = "não registrada"
        if isinstance(inicio, int) and isinstance(fim, int):
            faixa = f"{inicio}–{fim}"
        return [
            _campo("Arquivo lido", leitura["arquivo"]),
            _campo("Linhas", faixa),
            _campo("Leitura completa", "sim" if leitura.get("completo") else "não"),
        ]

    linhas = []
    for leitura in leituras:
        faixa = "faixa não registrada"
        if isinstance(leitura.get("inicio"), int) and isinstance(leitura.get("fim"), int):
            faixa = f"linhas {leitura['inicio']}–{leitura['fim']}"
        sufixo = "completa" if leitura.get("completo") else "parcial"
        linhas.append(f"{leitura['arquivo']} ({faixa}, {sufixo})")
    return [
        _campo("Arquivos lidos", "\n".join(linhas)),
        _campo(
            "Leitura completa",
            "sim" if all(item.get("completo") for item in leituras) else "parcial",
        ),
    ]


def _validacao(resultado, detalhes, status):
    gate = detalhes.get("completion_gate")
    if status == "needs_user" and isinstance(gate, dict) and gate.get("requires_user") is True:
        return "proposta aprovada; aguardando confirmação"
    aprovado = resultado.get("verificacao_aprovada") if isinstance(resultado, dict) else None
    if aprovado is True:
        return "aprovada"
    if aprovado is False:
        return "reprovada"
    if isinstance(gate, dict):
        if gate.get("passed") is True:
            return "aprovada"
        if gate.get("passed") is False:
            return "reprovada"
    return "não executada" if status != "success" else "não informada"


def construir_resumo_trabalho(evento, resultado, duracao_segundos, projeto=None, status_job="completed"):
    """Monta o bloco de quatro etapas exibido no chat.

    Retorna ``None`` para eventos que nao representam uma pergunta. O contrato e
    deliberadamente pequeno para poder ser serializado no SQLite e publicado por
    ``GET /jobs/<id>`` sem expor o resultado completo do Engine.
    """
    if not isinstance(evento, dict) or evento.get("tipo") != "pergunta":
        return None
    resultado = resultado if isinstance(resultado, dict) else {}
    detalhes = resultado.get("agente_conclusao")
    detalhes = detalhes if isinstance(detalhes, dict) else {}
    contexto = resultado.get("trabalho_contexto")
    contexto = contexto if isinstance(contexto, dict) else {}
    projeto = projeto if isinstance(projeto, dict) else {}

    objetivo = _texto(evento.get("texto"), 1000) or "objetivo não registrado"
    ferramentas = _unicos(detalhes.get("tools_called") or contexto.get("ferramentas"))
    evidencias = _rotulos_evidencia(detalhes, contexto)
    leituras = _leituras(detalhes, contexto)

    agente_status = resultado.get("agente_status")
    if not agente_status:
        agente_status = "success" if status_job == "completed" else "failed"
    agente_status = _texto(agente_status, 80)

    roteador = resultado.get("roteador")
    roteador = roteador if isinstance(roteador, dict) else {}
    fallback = (
        detalhes.get("fallback_cause")
        or roteador.get("fallback_cause")
        or contexto.get("fallback_cause")
    )
    fallback_texto = "não" if not fallback else f"sim — {_texto(fallback, 220)}"

    limitacoes = []
    for origem in (
        detalhes.get("limitacoes"), contexto.get("limitacoes"), resultado.get("avisos"),
    ):
        if isinstance(origem, list):
            limitacoes.extend(origem)
    if projeto.get("arquivos") == 1:
        limitacoes.append("projeto contém apenas um arquivo")
    limitacoes = _unicos(limitacoes)[:_MAX_LIMITATIONS]

    try:
        duracao = max(0.0, round(float(duracao_segundos), 2))
    except (TypeError, ValueError):
        duracao = 0.0

    intent = _task_intent(detalhes)

    return {
        "schema_version": 3,
        "title": "Trabalho concluído" if status_job == "completed" else "Trabalho interrompido",
        "duration_seconds": duracao,
        "steps": [
            _etapa(1, "Entendimento", [
                _campo("Objetivo", objetivo),
                _campo("Intenção detectada", intent.get("intent")),
                _campo("Perfil de resposta", intent.get("response_profile")),
                _campo("Escrita permitida", _sim_nao(intent.get("write_allowed"))),
                _campo("Recomendações solicitadas", _sim_nao(intent.get("recommendations_requested"))),
                _campo("Saídas obrigatórias", ", ".join(intent.get("requested_outputs") or [])),
            ]),
            _etapa(2, "Leitura", _campos_leitura(leituras, ferramentas)),
            _etapa(3, "Análise", [
                _campo("Modo", _modo(resultado, detalhes, contexto)),
                _campo("Ferramentas utilizadas", ", ".join(ferramentas) or "nenhuma registrada"),
                _campo("Evidências", ", ".join(evidencias) or "nenhuma registrada"),
                _campo("Cobertura real", _cobertura_auditoria(detalhes)),
                _campo("Pipeline de auditoria", _pipeline_auditoria(detalhes)),
                _campo("Erros de ferramentas", _erros_ferramenta(detalhes)),
            ]),
            _etapa(4, "Conclusão", [
                _campo("Status", agente_status),
                _campo("Fallback utilizado", fallback_texto),
                _campo("Validação", _validacao(resultado, detalhes, agente_status)),
                _campo("Limitações", "; ".join(limitacoes) or "nenhuma registrada"),
            ]),
        ],
    }
