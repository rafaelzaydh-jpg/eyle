import hashlib

import engine.agent as agent_mod
from engine.agent_state import AgentState
from engine.grounding import verify_conclusion
from engine.project_reader import ler_faixa_projeto
from engine.structured_claims import claims_to_annotations, render_claims


def _ok(detail):
    return {
        "status": "success", "ok": True, "executed": True,
        "changed": False, "error_code": None, "detail": detail,
    }


def test_structured_grounding_uses_annotations_as_canonical_claim_units():
    claims = [{
        "type": "absence",
        "text": "Não foram encontradas rotas Flask no arquivo analisado.",
        "evidence_ids": ["ev-0001"],
        "basis": "",
        "scope": "app.py:1-3",
        "output": "verified_limitations",
    }]
    evidence = [{
        "id": "ev-0001", "estado": "fresh", "arquivo": "app.py",
        "linha_inicio": 1, "linha_fim": 3,
        "conteudo": "1 | from flask import Flask\n2 | app = Flask(__name__)\n3 | app.run()",
    }]
    result = verify_conclusion(
        render_claims(claims), evidence,
        {"enabled": True, "block_unsupported_anchors": True},
        claim_annotations=claims_to_annotations(claims),
    )
    assert result["ok"] is True
    assert result["claims"][0]["claim_type"] == "absence"
    assert result["claims"][0]["evidence_ids"] == ["ev-0001"]


def test_structured_grounding_filter_preserves_valid_analysis_claims():
    conclusion = {
        "resposta": "",
        "claims": [
            {
                "type": "fact", "text": "app.py cria uma instância Flask.",
                "evidence_ids": ["ev-0001"], "basis": "", "output": "analysis",
            },
            {
                "type": "fact", "text": "O projeto usa o símbolo inventado `ghost_runtime`.",
                "evidence_ids": ["ev-0001"], "basis": "", "output": "analysis",
            },
        ],
        "evidence_ids": ["ev-0001"],
        "claim_annotations": [],
        "limitacoes": [],
    }
    conclusion["resposta"] = render_claims(conclusion["claims"])
    conclusion["claim_annotations"] = claims_to_annotations(conclusion["claims"])
    grounding = {
        "claims": [
            {"claim_index": 1, "errors": []},
            {"claim_index": 2, "errors": ["unsupported_objective_anchor"]},
        ]
    }
    filtered = agent_mod._filtrar_conclusao_estruturada_por_grounding(
        conclusion, grounding,
    )
    assert filtered is not None
    assert len(filtered["claims"]) == 1
    assert "instância Flask" in filtered["resposta"]
    assert "ghost_runtime" not in filtered["resposta"]
    assert filtered["evidence_ids"] == ["ev-0001"]


def test_apply_patch_reuses_last_approved_dry_run_and_derives_original(tmp_path):
    code = "from flask import Flask\napp = Flask(__name__)\n"
    (tmp_path / "app.py").write_text(code, encoding="utf-8")
    reading = ler_faixa_projeto(str(tmp_path), "app.py", 1, 2)

    state = AgentState(config={})
    state.definir_objetivo("Adicione /health", "project_write", modo="edit")
    state.registrar_acao(
        "read_file", {"caminho_relativo": "app.py"}, _ok(reading),
        contar_execucao=True,
    )
    new_code = (
        "from flask import Flask\napp = Flask(__name__)\n\n"
        "@app.get('/health')\ndef health():\n    return {'status': 'ok'}"
    )
    dry_args = state.completar_argumentos_patch("test_patch_dry_run", {
        "caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 2,
        "codigo_novo": new_code,
        "file_hash_esperado": "0" * 64, "range_hash_esperado": "f" * 64,
    })
    state.registrar_acao(
        "test_patch_dry_run", dry_args, _ok({"message": "dry-run aprovado"}),
        contar_execucao=True,
    )

    completed = state.completar_argumentos_patch("apply_patch", {
        "caminho_relativo": "errado.py", "linha_inicio": 99, "linha_fim": 99,
        "codigo_original_esperado": "", "codigo_novo": "errado",
        "file_hash_esperado": hashlib.sha256(b"x").hexdigest(),
        "range_hash_esperado": hashlib.sha256(b"y").hexdigest(),
    })
    assert completed["caminho_relativo"] == "app.py"
    assert completed["linha_inicio"] == 1
    assert completed["linha_fim"] == 2
    assert completed["codigo_novo"] == new_code
    assert completed["codigo_original_esperado"] == code.rstrip("\n")
    ok, _ = state.validar_precondicoes_patch(completed)
    assert ok is True


def test_project_audit_publishes_valid_claims_when_one_extra_claim_is_rejected(tmp_path, monkeypatch):
    import json
    from tests.test_project_audit_55_17 import _config

    (tmp_path / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda prompt, config: json.dumps({
        "final": {
            "answer": "plano",
            "selected_paths": ["app.py"] if "SCOUT PHASE: initial" in prompt else [],
            "risk_hypotheses": [], "gaps": [], "rationale": "ler app.py",
        }
    }))
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", lambda *args: json.dumps({
        "final": {
            "claims": [
                {
                    "type": "fact",
                    "text": "Este projeto é uma aplicação web mínima criada com Flask.",
                    "evidence_ids": ["ev-0001"], "basis": "",
                    "output": "plain_language_summary",
                },
                {
                    "type": "fact",
                    "text": "A execução de app.py cria a instância Flask armazenada na variável app.",
                    "evidence_ids": ["ev-0001"], "basis": "",
                    "output": "main_behavior",
                },
                {
                    "type": "fact",
                    "text": "O arquivo app.py contém a criação da instância Flask.",
                    "evidence_ids": ["ev-0001"], "basis": "",
                    "output": "important_components",
                },
                {
                    "type": "fact",
                    "text": "A execução do módulo utiliza a instância Flask definida no mesmo arquivo.",
                    "evidence_ids": ["ev-0001"], "basis": "",
                    "output": "component_relationships",
                },
                {
                    "type": "absence",
                    "text": "Não foram encontrados arquivos de teste no inventário analisado.",
                    "evidence_ids": ["ev-0001"], "basis": "",
                    "scope": "inventário completo do projeto",
                    "output": "verified_limitations",
                },
                {
                    "type": "fact",
                    "text": "O projeto usa o símbolo inventado `ghost_runtime`.",
                    "evidence_ids": ["ev-0001"], "basis": "", "output": "analysis",
                },
            ],
            "verification": "app.py lido",
            "limitations": ["O comportamento em runtime não foi executado."],
        }
    }))
    cfg = _config()
    cfg["agent"].update({
        "intent_output_gate_enabled": True,
        "semantic_grounding": {
            "enabled": True,
            "block_unsupported_anchors": True,
            "require_inline_citations": False,
            "require_inference_evidence": True,
        },
    })
    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto",
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "instância Flask" in text
    assert "ghost_runtime" not in text
    assert details["semantic_grounding"]["ok"] is True
    assert details["fallback_cause"].endswith("structured_claim_filter")
