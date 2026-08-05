#!/usr/bin/env python3
"""Contrato mínimo e cobertura de alvos para tarefas da Eyle.

A Rev3 não cria um planejador paralelo. Este módulo somente transforma partes
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

    # Segmentos de explicação costumam listar funções: "explique tocar e limitar_volume".
    for match in re.finditer(
        r"\b(?:explique|explain|analise|analyze)\s+(.+?)(?:\s+com\s+(?:cita|codigo|código)|[.;]|$)",
        text,
        re.IGNORECASE,
    ):
        for token in _IDENTIFIER_RE.findall(match.group(1)):
            if _norm(token) not in {_norm(item) for item in _STOPWORDS}:
                candidates.append(token)

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

    return {
        "version": 1,
        "task_type": task_type,
        "original_request": objective_text,
        "intent": "explain" if task_type == "project_read" else task_type,
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
