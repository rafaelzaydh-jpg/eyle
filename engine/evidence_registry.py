#!/usr/bin/env python3
"""Registro canonico de evidencias observadas pelo pipeline da Eyle.

Leitura, analise, grounding, conclusao e interface devem consultar a mesma
colecao. O registro nao cria evidencias por inferencia: ele apenas indexa e
atualiza leituras reais produzidas pelas tools.
"""
from __future__ import annotations

from copy import deepcopy


class EvidenceRegistry:
    SCHEMA_VERSION = 1

    def __init__(self, items=None):
        self.items = []
        self._by_id = {}
        for item in items or []:
            if isinstance(item, dict):
                self.add_existing(item)

    def add_existing(self, item):
        entry = dict(item)
        evidence_id = entry.get("id")
        if not isinstance(evidence_id, str) or not evidence_id:
            return None
        current = self._by_id.get(evidence_id)
        if current is not None:
            current.update(entry)
            return current
        self.items.append(entry)
        self._by_id[evidence_id] = entry
        return entry

    def register(self, item, *, evidence_id, source_tool):
        """Registra uma faixa nova ou reativa a mesma leitura/hash."""
        arquivo = item.get("arquivo")
        inicio = item.get("linha_inicio")
        fim = item.get("linha_fim")
        content_hash = item.get("content_hash")
        file_hash = item.get("file_hash")
        for entry in self.items:
            if (
                entry.get("arquivo") == arquivo
                and entry.get("linha_inicio") == inicio
                and entry.get("linha_fim") == fim
                and entry.get("content_hash") == content_hash
                and entry.get("file_hash") == file_hash
            ):
                entry["estado"] = "fresh"
                entry["source_tool"] = source_tool or entry.get("source_tool")
                return entry, False

        entry = dict(item)
        entry["id"] = evidence_id
        entry["source_tool"] = source_tool
        entry.setdefault("estado", "fresh")
        self.items.append(entry)
        self._by_id[evidence_id] = entry
        return entry, True

    def get(self, evidence_id):
        return self._by_id.get(evidence_id)

    def fresh(self):
        return [item for item in self.items if item.get("estado") == "fresh"]

    def mark_stale(self, *, arquivo=None, evidence_ids=None):
        ids = set(evidence_ids or [])
        changed = []
        for item in self.items:
            matches = (arquivo is not None and item.get("arquivo") == arquivo) or (
                ids and item.get("id") in ids
            )
            if matches and item.get("estado") != "stale":
                item["estado"] = "stale"
                changed.append(item.get("id"))
        return changed

    def public_snapshot(self, *, selected_ids=None):
        selected = set(selected_ids or [])
        output = []
        for item in self.items:
            if selected and item.get("id") not in selected:
                continue
            output.append({
                "id": item.get("id"),
                "source_tool": item.get("source_tool"),
                "arquivo": item.get("arquivo"),
                "linha_inicio": item.get("linha_inicio"),
                "linha_fim": item.get("linha_fim"),
                "total_linhas_arquivo": item.get("total_linhas_arquivo"),
                "truncado": bool(item.get("truncado")),
                "leitura_completa": bool(item.get("leitura_completa")),
                "content_hash": item.get("content_hash"),
                "file_hash": item.get("file_hash"),
                "estado": item.get("estado"),
            })
        return {
            "schema_version": self.SCHEMA_VERSION,
            "items": output,
            "evidence_ids": [item.get("id") for item in output if item.get("id")],
        }

    def export(self):
        return deepcopy(self.items)
