#!/usr/bin/env python3
"""Preservação auditável de informação ponta a ponta.

A Rev4.5 rastreia cada obrigação do pedido do contrato até a resposta renderizada:

target/output -> fresh evidence -> structured claim -> rendered segment

O módulo é totalmente determinístico. Ele não decide se uma frase "soa correta";
essa responsabilidade continua nos gates de grounding. Aqui a pergunta é apenas:
uma informação obrigatória ou essencial desapareceu silenciosamente no caminho?
"""
from __future__ import annotations

import json
import os
import unicodedata
from collections import defaultdict

from engine.task_contract import claim_output_tags

LEVEL_REQUIRED = "required"
LEVEL_ESSENTIAL = "essential"
LEVEL_OPTIONAL = "optional"
_LEVEL_RANK = {LEVEL_REQUIRED: 0, LEVEL_ESSENTIAL: 1, LEVEL_OPTIONAL: 2}
_BLOCKING_LEVELS = {LEVEL_REQUIRED, LEVEL_ESSENTIAL}


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def _fresh_evidence_registry(evidence):
    return {
        str(item.get("id")): item
        for item in evidence or []
        if isinstance(item, dict) and item.get("id") and item.get("estado") == "fresh"
    }


def _claim_id(claim, index):
    return str((claim or {}).get("claim_id") or f"claim-{index:03d}")


def rejected_claims_from_grounding(claims, verification):
    """Converte relatórios do grounding em um registro persistível de rejeições."""
    reports = {
        int(item.get("claim_index")): item
        for item in (verification or {}).get("claims") or []
        if isinstance(item, dict) and item.get("claim_index") is not None
    }
    rejected = []
    for index, claim in enumerate(claims or [], start=1):
        report = reports.get(index) or {}
        errors = list(report.get("errors") or [])
        if not errors:
            continue
        rejected.append({
            "claim_id": _claim_id(claim, index),
            "type": claim.get("type"),
            "text": claim.get("text"),
            "evidence_ids": list(claim.get("evidence_ids") or []),
            "output": claim.get("output") or "",
            "errors": errors,
            "warnings": list(report.get("warnings") or []),
            "status": "rejected",
        })
    return rejected


def _target_claim_matches(target, claim):
    kind = str(target.get("kind") or "")
    text = _norm(claim.get("text"))
    if kind == "symbol_explanation":
        return _norm(target.get("symbol")) in text
    if kind == "symbol_relationship":
        symbols = [_norm(item) for item in target.get("symbols") or []]
        return bool(symbols) and all(symbol in text for symbol in symbols)
    if kind == "origin_and_literal_value":
        return any(term in text for term in ("define", "definid", "origem", "importa", "comes from", "defined"))
    if kind == "useful_analysis":
        # É uma obrigação da resposta como um todo, não torna cada claim
        # individualmente obrigatória.
        return False
    if kind in {"file_read", "complete_file_scope"}:
        return False
    label = _norm(target.get("label"))
    return bool(label and label in text)


def _contract_items(contract):
    contract = contract or {}
    items = []
    for target in contract.get("required_targets") or []:
        item = dict(target)
        item.update({
            "ledger_id": str(target.get("id") or f"target:{len(items)+1}"),
            "source": "required_target",
            "level": str(target.get("level") or LEVEL_REQUIRED),
        })
        items.append(item)
    for output in contract.get("required_outputs") or []:
        items.append({
            "ledger_id": f"output:{output}",
            "id": f"output:{output}",
            "kind": "response_output",
            "label": output,
            "output": output,
            "source": "required_output",
            "level": LEVEL_ESSENTIAL,
        })
    for output in contract.get("optional_outputs") or []:
        items.append({
            "ledger_id": f"output:{output}",
            "id": f"output:{output}",
            "kind": "response_output",
            "label": output,
            "output": output,
            "source": "optional_output",
            "level": LEVEL_OPTIONAL,
        })
    for target in contract.get("must_preserve") or []:
        if not isinstance(target, dict):
            continue
        item = dict(target)
        item.update({
            "ledger_id": str(target.get("id") or f"must_preserve:{len(items)+1}"),
            "source": "must_preserve",
            "level": str(target.get("level") or LEVEL_REQUIRED),
            "kind": str(target.get("kind") or "manifest_fact"),
            "label": str(target.get("label") or target.get("description") or target.get("id") or "must preserve"),
        })
        items.append(item)
    unique = {}
    for item in items:
        unique[item["ledger_id"]] = item
    return list(unique.values())


def _coverage_by_id(target_coverage):
    return {
        str(item.get("id")): item
        for item in (target_coverage or {}).get("targets") or []
        if isinstance(item, dict) and item.get("id")
    }


def _segments_by_claim(rendered_segments):
    result = defaultdict(list)
    for segment in rendered_segments or []:
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id") or "")
        for claim_id in segment.get("claim_ids") or []:
            result[str(claim_id)].append(segment_id)
    return result


def _importance_for_claim(claim, contract_items):
    matched = []
    tags = claim_output_tags(claim)
    for item in contract_items:
        if item.get("kind") == "response_output" and item.get("output") in tags:
            matched.append(item.get("level"))
        elif _target_claim_matches(item, claim):
            matched.append(item.get("level"))
    return min(matched, key=lambda value: _LEVEL_RANK.get(value, 99)) if matched else LEVEL_OPTIONAL


def build_target_coverage_ledger(
    contract, claims, evidence, rendered_segments, *, target_coverage=None,
    intent_coverage=None, rejected_claims=None,
):
    """Cria o recibo target/evidence/claim/render sem usar LLM."""
    contract_items = _contract_items(contract)
    evidence_registry = _fresh_evidence_registry(evidence)
    coverage_by_id = _coverage_by_id(target_coverage)
    segments_by_claim = _segments_by_claim(rendered_segments)
    claims = [item for item in claims or [] if isinstance(item, dict)]
    claim_records = []
    evidence_to_claims = defaultdict(list)

    for index, claim in enumerate(claims, start=1):
        claim_id = _claim_id(claim, index)
        evidence_ids = list(dict.fromkeys(str(item) for item in claim.get("evidence_ids") or []))
        valid_evidence_ids = [item for item in evidence_ids if item in evidence_registry]
        for evidence_id in valid_evidence_ids:
            evidence_to_claims[evidence_id].append(claim_id)
        claim_records.append({
            "claim_id": claim_id,
            "type": claim.get("type"),
            "text": claim.get("text"),
            "output_tags": sorted(claim_output_tags(claim)),
            "importance": _importance_for_claim(claim, contract_items),
            "evidence_ids": evidence_ids,
            "valid_evidence_ids": valid_evidence_ids,
            "rendered_segment_ids": list(segments_by_claim.get(claim_id) or []),
            "status": "approved",
        })

    target_records = []
    for item in contract_items:
        kind = item.get("kind")
        level = item.get("level") if item.get("level") in _LEVEL_RANK else LEVEL_REQUIRED
        matched_claims = []
        if kind == "response_output":
            output = str(item.get("output") or "")
            matched_claims = [record for record in claim_records if output in record["output_tags"]]
        elif kind == "useful_analysis":
            matched_claims = list(claim_records)
        else:
            matched_claims = [
                record for record, claim in zip(claim_records, claims)
                if _target_claim_matches(item, claim)
            ]

        evidence_ids = sorted({
            evidence_id for record in matched_claims for evidence_id in record["valid_evidence_ids"]
        })
        rendered_ids = sorted({
            segment_id for record in matched_claims for segment_id in record["rendered_segment_ids"]
        })
        target_result = coverage_by_id.get(str(item.get("id") or item.get("ledger_id"))) or {}

        if kind == "file_read":
            requested_path = str(item.get("path") or "").replace("\\", "/")
            evidence_ok = any(
                str(ev.get("arquivo") or "").replace("\\", "/") == requested_path
                or str(ev.get("arquivo") or "").replace("\\", "/").rsplit("/", 1)[-1]
                == requested_path.rsplit("/", 1)[-1]
                for ev in evidence_registry.values()
            )
            claim_ok = True
            rendered_ok = True
        elif kind == "complete_file_scope":
            requested_paths = [str(path).replace("\\", "/") for path in item.get("paths") or []]
            evidence_ok = bool(requested_paths) and all(
                any(
                    (
                        str(ev.get("arquivo") or "").replace("\\", "/") == path
                        or str(ev.get("arquivo") or "").replace("\\", "/").rsplit("/", 1)[-1]
                        == path.rsplit("/", 1)[-1]
                    )
                    and ev.get("leitura_completa") is True
                    for ev in evidence_registry.values()
                )
                for path in requested_paths
            )
            claim_ok = True
            rendered_ok = True
        else:
            evidence_ok = bool(evidence_ids)
            claim_ok = bool(matched_claims)
            rendered_ok = bool(rendered_ids)

        # O gate de intenção já classifica cobertura de outputs; o ledger mantém
        # esse dado como diagnóstico, sem reinterpretar texto livre.
        if kind == "response_output" and intent_coverage:
            covered_outputs = set(intent_coverage.get("covered_outputs") or [])
            claim_ok = claim_ok and str(item.get("output")) in covered_outputs

        status = "covered"
        if not evidence_ok:
            status = "missing_evidence"
        elif not claim_ok:
            status = "missing_claim"
        elif not rendered_ok:
            status = "missing_render"
        target_records.append({
            "id": item.get("ledger_id"),
            "source": item.get("source"),
            "kind": kind,
            "label": item.get("label"),
            "level": level,
            "evidence_ids": evidence_ids,
            "claim_ids": [record["claim_id"] for record in matched_claims],
            "rendered_segment_ids": rendered_ids,
            "status": status,
            "covered": status == "covered",
        })

    rejected_records = []
    for item in rejected_claims or []:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        tags = claim_output_tags(record)
        importance = LEVEL_OPTIONAL
        for target in contract_items:
            if target.get("kind") == "response_output" and target.get("output") in tags:
                importance = min(
                    (importance, target.get("level")),
                    key=lambda value: _LEVEL_RANK.get(value, 99),
                )
            elif _target_claim_matches(target, record):
                importance = min(
                    (importance, target.get("level")),
                    key=lambda value: _LEVEL_RANK.get(value, 99),
                )
        record["importance"] = importance
        rejected_tags = claim_output_tags(record)
        rejected_evidence = set(str(item) for item in record.get("evidence_ids") or [])
        replacements = []
        for approved in claim_records:
            same_output = bool(rejected_tags & set(approved.get("output_tags") or []))
            same_evidence = bool(rejected_evidence & set(approved.get("valid_evidence_ids") or []))
            if same_output and same_evidence:
                replacements.append(approved.get("claim_id"))
        record["replacement_claim_ids"] = replacements
        record["silently_discarded"] = bool(
            importance in _BLOCKING_LEVELS and not replacements
        )
        rejected_records.append(record)

    blocking = [
        item for item in target_records
        if item["level"] in _BLOCKING_LEVELS and not item["covered"]
    ]
    approved_not_rendered = [
        item for item in claim_records
        if not item["rendered_segment_ids"]
    ]
    blocking_claims = [
        item for item in approved_not_rendered if item["importance"] in _BLOCKING_LEVELS
    ]
    optional_missing = [
        item for item in target_records
        if item["level"] == LEVEL_OPTIONAL and not item["covered"]
    ]
    rejected_silent_discards = [
        item for item in rejected_records if item.get("silently_discarded")
    ]
    silent_discards = list(blocking_claims) + rejected_silent_discards

    gate_ok = not blocking and not blocking_claims and not rejected_silent_discards
    summary = {
        "targets_total": len(target_records),
        "targets_covered": sum(1 for item in target_records if item["covered"]),
        "required_total": sum(1 for item in target_records if item["level"] == LEVEL_REQUIRED),
        "required_covered": sum(1 for item in target_records if item["level"] == LEVEL_REQUIRED and item["covered"]),
        "essential_total": sum(1 for item in target_records if item["level"] == LEVEL_ESSENTIAL),
        "essential_covered": sum(1 for item in target_records if item["level"] == LEVEL_ESSENTIAL and item["covered"]),
        "optional_total": sum(1 for item in target_records if item["level"] == LEVEL_OPTIONAL),
        "optional_covered": sum(1 for item in target_records if item["level"] == LEVEL_OPTIONAL and item["covered"]),
        "claims_approved": len(claim_records),
        "claims_rejected": len(rejected_records),
        "claims_rendered": sum(1 for item in claim_records if item["rendered_segment_ids"]),
        "silent_discards": len(silent_discards),
    }
    return {
        "schema_version": 1,
        "gate": {
            "ok": gate_ok,
            "failure_code": None if gate_ok else "INFORMATION_PRESERVATION_FAILED",
            "blocking_targets": blocking,
            "blocking_claims": blocking_claims,
            "rejected_essential_without_replacement": rejected_silent_discards,
            "optional_missing": optional_missing,
        },
        "summary": summary,
        "targets": target_records,
        "claims": claim_records,
        "rejected_claims": rejected_records,
        "rendered_segments": list(rendered_segments or []),
        "evidence_to_claims": dict(evidence_to_claims),
    }


def public_ledger(ledger):
    """Mantém o diagnóstico útil sem repetir o conteúdo bruto das evidências."""
    ledger = ledger or {}
    return {
        "schema_version": ledger.get("schema_version", 1),
        "gate": ledger.get("gate") or {},
        "summary": ledger.get("summary") or {},
        "targets": ledger.get("targets") or [],
        "claims": ledger.get("claims") or [],
        "rejected_claims": ledger.get("rejected_claims") or [],
        "rendered_segments": ledger.get("rendered_segments") or [],
        "evidence_to_claims": ledger.get("evidence_to_claims") or {},
    }


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("must_preserve"), list):
        raise ValueError("manifesto precisa conter must_preserve como lista")
    return data


def evaluate_must_preserve_manifest(manifest, ledger, answer=""):
    """Avalia fixtures sem depender da redação exata da resposta."""
    answer_norm = _norm(answer)
    evidence_ids = set((ledger or {}).get("evidence_to_claims") or {})
    claims = (ledger or {}).get("claims") or []
    claim_text = _norm("\n".join(str(item.get("text") or "") for item in claims))
    results = []
    for item in manifest.get("must_preserve") or []:
        required_terms = [_norm(term) for term in item.get("terms") or [] if _norm(term)]
        evidence_required = bool(item.get("requires_evidence", True))
        claim_ok = all(term in claim_text for term in required_terms)
        render_ok = all(term in answer_norm for term in required_terms)
        evidence_ok = bool(evidence_ids) if evidence_required else True
        covered = claim_ok and render_ok and evidence_ok
        results.append({
            "id": item.get("id"),
            "level": item.get("level", LEVEL_REQUIRED),
            "terms": item.get("terms") or [],
            "evidence_ok": evidence_ok,
            "claim_ok": claim_ok,
            "render_ok": render_ok,
            "covered": covered,
        })
    blocking = [
        item for item in results
        if item.get("level") in _BLOCKING_LEVELS and not item.get("covered")
    ]
    return {
        "ok": not blocking,
        "failure_code": None if not blocking else "MUST_PRESERVE_REGRESSION",
        "items": results,
        "blocking": blocking,
        "covered_count": sum(1 for item in results if item.get("covered")),
        "total": len(results),
    }


def _case_map(report):
    result = {}
    for run in (report or {}).get("runs") or []:
        role = str(run.get("papel") or "principal")
        for case in run.get("resultados") or []:
            if not isinstance(case, dict) or not case.get("id"):
                continue
            result[(role, str(case["id"]))] = case
    return result


def compare_release_coverage(baseline, candidate):
    """Detecta perda de cobertura entre dois relatórios de benchmark."""
    baseline_cases = _case_map(baseline)
    candidate_cases = _case_map(candidate)
    comparisons = []
    regressions = []
    for key, base_case in sorted(baseline_cases.items()):
        candidate_case = candidate_cases.get(key)
        if candidate_case is None:
            regression = {
                "role": key[0], "case_id": key[1],
                "reason": "case_missing_in_candidate",
            }
            regressions.append(regression)
            comparisons.append({**regression, "regression": True})
            continue
        base_info = base_case.get("information_preservation") or {}
        cand_info = candidate_case.get("information_preservation") or {}
        base_summary = base_info.get("summary") or {}
        cand_summary = cand_info.get("summary") or {}
        reasons = []
        if (base_info.get("gate") or {}).get("ok") and not (cand_info.get("gate") or {}).get("ok"):
            reasons.append("preservation_gate_regressed")
        for field in ("required_covered", "essential_covered"):
            if int(cand_summary.get(field) or 0) < int(base_summary.get(field) or 0):
                reasons.append(f"{field}_decreased")
        if int(cand_summary.get("silent_discards") or 0) > int(base_summary.get("silent_discards") or 0):
            reasons.append("silent_discards_increased")
        item = {
            "role": key[0],
            "case_id": key[1],
            "baseline": base_summary,
            "candidate": cand_summary,
            "reasons": reasons,
            "regression": bool(reasons),
        }
        comparisons.append(item)
        if reasons:
            regressions.append(item)
    return {
        "schema_version": 1,
        "ok": not regressions,
        "failure_code": None if not regressions else "RELEASE_COVERAGE_REGRESSION",
        "comparisons": comparisons,
        "regressions": regressions,
        "baseline_cases": len(baseline_cases),
        "candidate_cases": len(candidate_cases),
    }


def compare_release_coverage_files(baseline_path, candidate_path, output_path=None):
    with open(baseline_path, "r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    with open(candidate_path, "r", encoding="utf-8") as handle:
        candidate = json.load(handle)
    result = compare_release_coverage(baseline, candidate)
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return result
