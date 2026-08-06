#!/usr/bin/env python3
"""Contrato mínimo e cobertura de alvos para tarefas da Eyle.

A Rev4 não cria um planejador ou agente paralelo. Este módulo somente transforma partes
literais do pedido em obrigações verificáveis e confere se as claims finais
cobriram essas obrigações usando evidências frescas.
"""
from __future__ import annotations

import re
import unicodedata

_FILE_RE = re.compile(
    r"(?<![\w./\-])([\w./\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|yml|yaml))\b",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_ASSIGN_STRING_RE = re.compile(
    r"(?m)^\s*([A-Z][A-Z0-9_]*)\s*=\s*(['\"])(.*?)\2\s*(?:#.*)?$"
)
_ASSIGN_NUMBER_RE = re.compile(
    r"(?m)^\s*([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:#.*)?$"
)

_STOPWORDS = {
    "a", "o", "as", "os", "e", "de", "do", "da", "dos", "das", "em",
    "um", "uma", "com", "sem", "para", "por", "que", "como", "onde",
    "vem", "valor", "prefixo", "funcao", "função", "metodo", "método",
    "codigo", "código", "arquivo", "inteiro", "linhas", "linha", "citacoes",
    "citações", "explique", "analise", "localize", "verifique", "somente",
    "comportamento", "real", "fresco", "fresca", "usando", "entre", "forma",
    "identificador", "projeto", "read", "file", "explain", "analyze", "with",
    "from", "the", "and", "function", "symbol", "method", "whole", "fresh",
}




_RECOMMENDATION_RE = re.compile(
    r"\b(?:sugir|sugest|recomend|melhorias?|como melhorar|refator|otimiz)\w*\b",
    re.IGNORECASE,
)
_ISSUE_RE = re.compile(
    r"\b(?:problemas?|bugs?|falhas?|riscos?|vulnerabil|revis[aã]o cr[ií]tica|code review|audite?)\w*\b",
    re.IGNORECASE,
)
_INVESTIGATE_RE = re.compile(
    r"\b(?:investig|diagnostic|por que|porque|causa|erro|bug|falha)\w*\b",
    re.IGNORECASE,
)
_DISCUSS_RE = re.compile(
    r"\b(?:convers|discut|debater|o que acha|vamos falar|talk about|discuss)\w*\b",
    re.IGNORECASE,
)
_PLAN_RE = re.compile(r"\b(?:planej|plano|roadmap|estrat[eé]gia)\w*\b", re.IGNORECASE)
_CREATE_RE = re.compile(r"\b(?:cri|constru|implemente do zero|novo projeto|new project)\w*\b", re.IGNORECASE)
_RECOMMENDATION_COUNT_RE = re.compile(
    r"\b(\d{1,2})\s+(?:melhorias?|sugest(?:o|õ)es|recomenda(?:c|ç)(?:a|ã)oes?)\b",
    re.IGNORECASE,
)
_RECOMMENDATION_LANGUAGE_RE = re.compile(
    r"\b(?:recomendo|sugiro|deveria(?:mos)?|seria melhor|ideal seria|recommend|suggest|should)\b",
    re.IGNORECASE,
)


_RESPONSE_SECTION_ORDER = {
    "code_analysis": [
        "plain_language_summary",
        "main_behavior",
        "important_components",
        "component_relationships",
        "verified_limitations",
    ],
    "code_explanation": ["explanation", "verified_limitations"],
    "code_conversation": ["grounded_discussion", "verified_limitations"],
    "code_review": ["analysis", "problems", "recommendations", "verified_limitations"],
    "code_investigation": ["investigation_result", "verified_limitations"],
    "code_plan": ["plan", "verified_limitations"],
    "code_change": [
        "analysis", "problems", "implemented_change", "verification_result",
        "final_state", "explanation",
    ],
}


def _output_requirements(profile, outputs):
    """Separa o contrato semântico mínimo das seções enriquecedoras.

    A resposta pode tentar cobrir todas as ``requested_outputs``, mas apenas
    ``required_outputs`` bloqueiam a publicação. Em análise geral, resumo em
    linguagem natural e comportamento principal definem a aderência mínima;
    componentes, relações e limitações enriquecem a resposta quando a evidência
    realmente os sustenta.
    """
    requested = list(outputs or [])
    if profile == "code_analysis":
        required = [
            item for item in ("plain_language_summary", "main_behavior")
            if item in requested
        ]
    else:
        required = list(requested)
    optional = [item for item in requested if item not in required]
    return required, optional


def _classify_task_intent(objective, task_type):
    text = str(objective or "")
    normalized = _norm(text)
    recommendations_requested = bool(_RECOMMENDATION_RE.search(text))
    issue_detection_requested = bool(_ISSUE_RE.search(text))

    count_match = _RECOMMENDATION_COUNT_RE.search(text)
    recommendation_count = int(count_match.group(1)) if count_match else None

    if task_type == "chat":
        intent = "chat"
        profile = "code_conversation"
        outputs = ["grounded_discussion"]
        write_allowed = False
    elif task_type == "project_write":
        intent = "create" if _CREATE_RE.search(text) else "edit"
        profile = "code_change"
        outputs = []
        if "analis" in normalized or "analy" in normalized:
            outputs.append("analysis")
        if issue_detection_requested:
            outputs.append("problems")
        outputs.extend(["implemented_change", "verification_result", "final_state"])
        if re.search(r"\b(?:explique|explicar|explain)\w*\b", text, re.IGNORECASE):
            outputs.append("explanation")
        write_allowed = True
    elif recommendations_requested and (issue_detection_requested or "analis" in normalized or "review" in normalized):
        intent = "review"
        profile = "code_review"
        # Analise + melhorias nao implica automaticamente uma secao de
        # problemas. ``problems`` so e obrigatorio quando o usuario pediu
        # explicitamente falhas, riscos, bugs ou revisao critica.
        outputs = ["analysis"]
        if issue_detection_requested:
            outputs.append("problems")
        outputs.append("recommendations")
        write_allowed = False
    elif recommendations_requested:
        intent = "suggest"
        profile = "code_review"
        outputs = ["recommendations"]
        write_allowed = False
    elif _DISCUSS_RE.search(text):
        intent = "discuss"
        profile = "code_conversation"
        outputs = ["grounded_discussion"]
        write_allowed = False
    elif _PLAN_RE.search(text):
        intent = "plan"
        profile = "code_plan"
        outputs = ["plan"]
        write_allowed = False
    elif _INVESTIGATE_RE.search(text) and task_type != "project_audit":
        intent = "investigate"
        profile = "code_investigation"
        outputs = ["investigation_result", "verified_limitations"]
        write_allowed = False
    elif task_type == "project_audit":
        intent = "review" if issue_detection_requested else "analyze"
        profile = "code_review" if issue_detection_requested else "code_analysis"
        outputs = (
            ["analysis", "problems", "verified_limitations"]
            if issue_detection_requested
            else [
                "plain_language_summary",
                "main_behavior",
                "important_components",
                "component_relationships",
                "verified_limitations",
            ]
        )
        write_allowed = False
    else:
        intent = "explain"
        profile = "code_explanation"
        outputs = ["explanation"]
        write_allowed = False

    required_outputs, optional_outputs = _output_requirements(profile, outputs)
    return {
        "intent": intent,
        "domain": "code",
        "response_profile": profile,
        "write_allowed": write_allowed,
        "requested_outputs": outputs,
        "required_outputs": required_outputs,
        "optional_outputs": optional_outputs,
        "response_sections": list(_RESPONSE_SECTION_ORDER.get(profile, outputs)),
        "recommendations_requested": recommendations_requested,
        "recommendation_count": recommendation_count,
        "issue_detection_requested": issue_detection_requested,
    }


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def _explicit_files(objective):
    return list(dict.fromkeys(item.replace("\\", "/") for item in _FILE_RE.findall(str(objective or ""))))


def _symbol_candidates(objective):
    text = str(objective or "")
    candidates = []

    # Identificadores com underscore são quase sempre símbolos literais.
    candidates.extend(token for token in _IDENTIFIER_RE.findall(text) if "_" in token)

    # Símbolos declarados após palavras de código.
    pattern = re.compile(
        r"\b(?:fun[cç][aã]o|function|s[ií]mbolo|symbol|m[eé]todo|method)\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    candidates.extend(pattern.findall(text))

    # Identificadores marcados explicitamente como codigo.
    candidates.extend(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))
    candidates.extend(re.findall(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{1,})(?![A-Za-z0-9_])", text))

    # Lista curta e inequívoca: "explique tocar e limitar_volume". Exigimos
    # ao menos um identificador forte (underscore/backtick/constante) para nao
    # transformar linguagem natural como "criação e inicialização" em símbolos.
    for match in re.finditer(
        r"\b(?:explique|explain)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:e|and)\s+([A-Za-z_][A-Za-z0-9_]*)",
        text,
        re.IGNORECASE,
    ):
        left, right = match.groups()
        trecho = match.group(0)
        strong = (
            "_" in left or "_" in right
            or f"`{left}`" in trecho or f"`{right}`" in trecho
            or left.isupper() or right.isupper()
        )
        if strong:
            candidates.extend((left, right))

    # "comportamento real de validar_token" e formas equivalentes.
    for match in re.finditer(
        r"\b(?:de|do|da)\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)\b",
        text,
        re.IGNORECASE,
    ):
        token = match.group(1)
        if "_" in token:
            candidates.append(token)

    files = {item.rsplit("/", 1)[-1].split(".", 1)[0].casefold() for item in _explicit_files(text)}
    result = []
    for token in candidates:
        normalized = _norm(token)
        if normalized in {_norm(item) for item in _STOPWORDS} or normalized in files:
            continue
        if token not in result:
            result.append(token)
    return result


def build_task_contract(objective, task_type="project_read"):
    """Extrai somente obrigações literais ou semanticamente inequívocas."""
    objective_text = str(objective or "").strip()
    normalized = _norm(objective_text)
    files = _explicit_files(objective_text)
    symbols = _symbol_candidates(objective_text)
    targets = []

    for path in files:
        targets.append({
            "id": f"file:{path}",
            "kind": "file_read",
            "label": f"ler {path}",
            "path": path,
        })
    for symbol in symbols:
        targets.append({
            "id": f"symbol:{symbol}",
            "kind": "symbol_explanation",
            "label": f"explicar {symbol}",
            "symbol": symbol,
        })

    asks_origin = any(term in normalized for term in (
        "de onde vem", "qual a origem", "origem do", "origem de", "where does",
    ))
    mentions_prefix = "prefix" in normalized
    if asks_origin and mentions_prefix:
        targets.append({
            "id": "concept:origin_and_literal_value",
            "kind": "origin_and_literal_value",
            "label": "explicar a origem e o valor literal do prefixo",
        })

    if len(symbols) >= 2 and any(term in normalized for term in (
        " e ", "relacao", "relação", "como", "relationship", "between",
    )):
        targets.append({
            "id": "relation:" + "+".join(symbols[:4]),
            "kind": "symbol_relationship",
            "label": "explicar a relação entre " + ", ".join(symbols),
            "symbols": symbols,
        })

    if files and any(term in normalized for term in ("inteiro", "completo", "whole", "entire")):
        targets.append({
            "id": "scope:complete_files",
            "kind": "complete_file_scope",
            "label": "cobrir integralmente os arquivos solicitados",
            "paths": files,
        })

    # Pedidos gerais continuam pequenos: o gate de utilidade valida a análise.
    if not targets and task_type in {"project_read", "project_audit"}:
        targets.append({
            "id": "result:useful_analysis",
            "kind": "useful_analysis",
            "label": "entregar uma análise útil relacionada ao pedido",
        })

    task_intent = _classify_task_intent(objective_text, task_type)
    return {
        "version": 2,
        "task_type": task_type,
        "original_request": objective_text,
        **task_intent,
        "explicit_files": files,
        "explicit_symbols": symbols,
        "required_targets": targets,
    }


def _evidence_registry(evidence):
    return {
        str(item.get("id")): item
        for item in evidence or []
        if isinstance(item, dict) and item.get("id") and item.get("estado") == "fresh"
    }


def _assignments_from_evidence(evidence):
    values = []
    for item in evidence or []:
        if not isinstance(item, dict) or item.get("estado") != "fresh":
            continue
        content = str(
            item.get("conteudo_raw") or item.get("conteudo") or item.get("content") or ""
        )
        for identifier, _, literal in _ASSIGN_STRING_RE.findall(content):
            values.append({"identifier": identifier, "literal": literal, "evidence_id": item.get("id")})
        for identifier, literal in _ASSIGN_NUMBER_RE.findall(content):
            values.append({"identifier": identifier, "literal": literal, "evidence_id": item.get("id")})
    return values


def evaluate_target_coverage(contract, claims, evidence, answer=""):
    """Confere cobertura sem usar outra LLM.

    ``file_read`` e escopo completo são provados pelas evidências. Símbolos e
    relações são conferidos no texto validado das claims. Para pedidos de
    origem, constantes literais observadas nas evidências tornam-se obrigações.
    """
    contract = contract or {}
    claims = claims or []
    registry = _evidence_registry(evidence)
    text = str(answer or "\n".join(str(item.get("text") or "") for item in claims))
    normalized_text = _norm(text)
    read_paths = {
        str(item.get("arquivo") or "").replace("\\", "/")
        for item in registry.values()
        if item.get("arquivo")
    }
    results = []
    assignments = _assignments_from_evidence(evidence)

    for target in contract.get("required_targets") or []:
        kind = target.get("kind")
        covered = False
        reason = ""
        expected = []

        if kind == "file_read":
            path = str(target.get("path") or "").replace("\\", "/")
            covered = path in read_paths or any(item.rsplit("/", 1)[-1] == path.rsplit("/", 1)[-1] for item in read_paths)
            reason = "fresh evidence exists" if covered else "arquivo solicitado não foi lido"
        elif kind == "symbol_explanation":
            symbol = str(target.get("symbol") or "")
            covered = _norm(symbol) in normalized_text
            reason = "símbolo aparece nas claims" if covered else f"as claims não explicam {symbol}"
        elif kind == "symbol_relationship":
            symbols = [str(item) for item in target.get("symbols") or []]
            expected = symbols
            claim_texts = [_norm(item.get("text")) for item in claims]
            covered = any(all(_norm(symbol) in claim_text for symbol in symbols) for claim_text in claim_texts)
            if not covered:
                # Duas claims factuais complementares ainda cobrem a relação se
                # o texto final contém todos os símbolos e um verbo relacional.
                relational = any(term in normalized_text for term in (
                    "chama", "usa", "utiliza", "depende", "importa", "passa", "antes", "depois",
                    "calls", "uses", "depends", "imports",
                ))
                covered = relational and all(_norm(symbol) in normalized_text for symbol in symbols)
            reason = "relação explícita" if covered else "faltou relacionar os símbolos solicitados"
        elif kind == "origin_and_literal_value":
            expected = assignments
            literal_covered = bool(assignments) and any(
                _norm(item["literal"]) in normalized_text and _norm(item["identifier"]) in normalized_text
                for item in assignments
            )
            origin_covered = any(term in normalized_text for term in (
                "define", "definido", "definida", "vem", "origem", "importa", "from config",
                "defined", "comes from", "imports",
            ))
            covered = literal_covered and origin_covered
            reason = "origem e valor literal presentes" if covered else "faltou declarar a origem e o valor literal observado"
        elif kind == "complete_file_scope":
            paths = target.get("paths") or []
            complete = []
            for path in paths:
                matching = [
                    item for item in registry.values()
                    if str(item.get("arquivo") or "").replace("\\", "/").rsplit("/", 1)[-1]
                    == str(path).replace("\\", "/").rsplit("/", 1)[-1]
                ]
                complete.append(bool(matching) and any(item.get("leitura_completa") is True for item in matching))
            covered = bool(complete) and all(complete)
            reason = "arquivos lidos por completo" if covered else "algum arquivo não foi lido integralmente"
        elif kind == "useful_analysis":
            covered = bool(normalized_text.strip()) and len(normalized_text.split()) >= 6
            reason = "há análise textual" if covered else "resposta não contém análise suficiente"
        else:
            reason = "tipo de alvo desconhecido"

        results.append({
            "id": target.get("id"),
            "kind": kind,
            "label": target.get("label"),
            "covered": bool(covered),
            "reason": reason,
            "expected": expected,
        })

    missing = [item for item in results if not item["covered"]]
    return {
        "ok": not missing,
        "failure_code": None if not missing else "ANSWER_TARGETS_NOT_COVERED",
        "targets": results,
        "missing": missing,
        "covered_count": len(results) - len(missing),
        "required_count": len(results),
    }


def claim_output_tags(claim):
    declared = claim.get("output", claim.get("requested_output"))
    if isinstance(declared, str) and declared.strip():
        return {declared.strip()}
    if isinstance(declared, list):
        return {str(item).strip() for item in declared if str(item).strip()}

    claim_type = str(claim.get("type") or "")
    text = _norm(claim.get("text") or "")
    tags = set()
    if claim_type == "recommendation":
        tags.add("recommendations")
    if claim_type == "risk":
        tags.update(("problems", "analysis"))
    if claim_type == "absence":
        tags.update(("verified_limitations", "analysis"))
    if claim_type in {"fact", "inference", "hypothesis"}:
        tags.add("analysis")
        if any(term in text for term in (
            "projeto e", "projeto é", "consiste", "aplicacao e", "aplicação é",
            "sistema e", "sistema é", "servico web", "serviço web",
            "project is", "application is", "consists of",
        )):
            tags.add("plain_language_summary")
        if any(term in text for term in ("retorna", "chama", "usa", "utiliza", "inicia", "define", "importa", "responde", "expoe", "expõe", "return", "calls", "uses", "starts", "defines", "imports", "responds", "exposes")):
            tags.update(("behavior", "main_behavior", "explanation"))
        if any(term in text for term in (
            "arquivo", "modulo", "módulo", "classe", "funcao", "função",
            "rota", "endpoint", "componente", "file", "module", "class",
            "function", "route", "component",
        )):
            tags.add("important_components")
        if any(term in text for term in ("depende", "relacao", "relação", "origem", "importa", "chama", "uses", "depends", "relationship")):
            tags.add("component_relationships")
        if any(term in text for term in ("projeto", "aplicacao", "aplicação", "sistema", "project", "application")):
            tags.add("explanation")
        if any(term in text for term in ("resultado", "causa", "erro", "falha", "root cause")):
            tags.add("investigation_result")
    if claim_type == "decision":
        tags.update(("plan", "final_state"))
    return tags


def evaluate_intent_coverage(contract, claims, limitations=None):
    """Valida o perfil solicitado sem pedir outra opiniao a uma LLM."""
    contract = contract or {}
    claims = claims or []
    requested = list(contract.get("requested_outputs") or [])
    required = list(contract.get("required_outputs") or requested)
    optional = list(contract.get("optional_outputs") or [
        item for item in requested if item not in required
    ])
    tags = set()
    for claim in claims:
        if isinstance(claim, dict):
            tags.update(claim_output_tags(claim))
    if limitations:
        tags.add("verified_limitations")

    intent = contract.get("intent")
    if intent == "analyze" and claims:
        tags.add("analysis")
    if intent == "explain" and claims:
        tags.add("explanation")
    if intent == "discuss" and claims:
        tags.add("grounded_discussion")
    if intent == "review" and claims:
        tags.add("analysis")

    missing = [item for item in required if item not in tags]
    missing_optional = [item for item in optional if item not in tags]
    recommendation_claims = [
        item for item in claims
        if isinstance(item, dict) and (
            item.get("type") == "recommendation"
            or _RECOMMENDATION_LANGUAGE_RE.search(str(item.get("text") or ""))
        )
    ]
    unsolicited = bool(recommendation_claims and not contract.get("recommendations_requested"))
    count_expected = contract.get("recommendation_count")
    count_ok = True
    if contract.get("recommendations_requested") and isinstance(count_expected, int):
        count_ok = len(recommendation_claims) == count_expected

    ok = not missing and not unsolicited and count_ok
    failure = None
    if unsolicited:
        failure = "UNSOLICITED_RECOMMENDATIONS"
    elif not count_ok:
        failure = "RECOMMENDATION_COUNT_MISMATCH"
    elif missing:
        failure = "INTENT_OUTPUTS_NOT_COVERED"
    return {
        "ok": ok,
        "failure_code": failure,
        "requested_outputs": requested,
        "required_outputs": required,
        "optional_outputs": optional,
        "covered_outputs": sorted(tags),
        "missing_outputs": missing,
        "missing_optional_outputs": missing_optional,
        "recommendations_requested": bool(contract.get("recommendations_requested")),
        "recommendation_count_expected": count_expected,
        "recommendation_count_actual": len(recommendation_claims),
        "unsolicited_recommendations": unsolicited,
    }


def order_claims_for_response(contract, claims):
    """Ordena claims pela estrutura semântica do perfil, preservando estabilidade."""
    contract = contract or {}
    profile = str(contract.get("response_profile") or "")
    order = list(contract.get("response_sections") or _RESPONSE_SECTION_ORDER.get(profile, []))
    if not order:
        return list(claims or [])
    ranks = {name: index for index, name in enumerate(order)}

    def rank(item):
        tags = claim_output_tags(item if isinstance(item, dict) else {})
        candidates = [ranks[tag] for tag in tags if tag in ranks]
        return min(candidates) if candidates else len(order)

    return [item for _, item in sorted(
        enumerate(claims or []), key=lambda pair: (rank(pair[1]), pair[0])
    )]


def render_claims_with_segments(contract, claims):
    """Renderiza e preserva o vínculo determinístico claim -> parágrafo."""
    ordered = order_claims_for_response(contract, claims)
    profile = str((contract or {}).get("response_profile") or "")
    sections = list((contract or {}).get("response_sections") or _RESPONSE_SECTION_ORDER.get(profile, []))
    if not sections:
        segments = []
        for index, item in enumerate(ordered, start=1):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            segments.append({
                "segment_id": f"segment-{index:03d}",
                "section": "unclassified",
                "claim_ids": [str(item.get("claim_id") or f"claim-{index:03d}")],
                "text": str(item.get("text") or "").strip(),
            })
        return {"text": "\n".join(item["text"] for item in segments).strip(), "segments": segments}

    buckets = {name: [] for name in sections}
    remainder = []
    for index, claim in enumerate(ordered, start=1):
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text") or "").strip()
        if not text:
            continue
        entry = {
            "claim_id": str(claim.get("claim_id") or f"claim-{index:03d}"),
            "text": text,
        }
        tags = claim_output_tags(claim)
        destination = next((name for name in sections if name in tags), None)
        if destination is None:
            remainder.append(entry)
        else:
            buckets[destination].append(entry)

    segments = []
    for name in sections:
        entries = buckets[name]
        if not entries:
            continue
        segments.append({
            "segment_id": f"segment-{len(segments)+1:03d}",
            "section": name,
            "claim_ids": [item["claim_id"] for item in entries],
            "text": " ".join(item["text"] for item in entries),
        })
    if remainder:
        segments.append({
            "segment_id": f"segment-{len(segments)+1:03d}",
            "section": "unclassified",
            "claim_ids": [item["claim_id"] for item in remainder],
            "text": " ".join(item["text"] for item in remainder),
        })
    return {"text": "\n\n".join(item["text"] for item in segments).strip(), "segments": segments}


def render_claims_for_response(contract, claims):
    """Compatibilidade: devolve apenas o texto da renderização auditável."""
    return render_claims_with_segments(contract, claims)["text"]


def render_intent_feedback(coverage):
    coverage = coverage or {}
    lines = ["TASK INTENT REPAIR (system-owned):"]
    if coverage.get("unsolicited_recommendations"):
        lines.append("- Remove recommendation language because the user did not request recommendations.")
    for output in coverage.get("missing_outputs") or []:
        lines.append(f"- Missing requested output: {output}")
    expected = coverage.get("recommendation_count_expected")
    if isinstance(expected, int) and coverage.get("recommendation_count_actual") != expected:
        lines.append(
            f"- Return exactly {expected} recommendation claims; current count is "
            f"{coverage.get('recommendation_count_actual', 0)}."
        )
    lines.append("Return the complete structured claims envelope once, using only fresh evidence.")
    return "\n".join(lines)


def render_target_feedback(coverage):
    missing = (coverage or {}).get("missing") or []
    if not missing:
        return ""
    lines = ["TARGET COVERAGE REPAIR (system-owned):"]
    for item in missing:
        lines.append(f"- {item.get('label')}: {item.get('reason')}")
        if item.get("expected"):
            lines.append(f"  Expected evidence-derived values: {item.get('expected')}")
    lines.extend([
        "Repair only the missing targets using the supplied evidence.",
        "Do not repeat unsupported claims and do not omit literal values requested by the user.",
    ])
    return "\n".join(lines)


def project_read_fast_path_ready(contract, evidence):
    """Retorna True quando todas as leituras explícitas já estão disponíveis."""
    contract = contract or {}
    files = contract.get("explicit_files") or []
    if not files:
        return False
    registry = _evidence_registry(evidence)
    read_paths = {
        str(item.get("arquivo") or "").replace("\\", "/")
        for item in registry.values()
        if item.get("arquivo")
    }
    for path in files:
        normalized = str(path).replace("\\", "/")
        if normalized not in read_paths and not any(
            item.rsplit("/", 1)[-1] == normalized.rsplit("/", 1)[-1]
            for item in read_paths
        ):
            return False
    return bool(registry)
