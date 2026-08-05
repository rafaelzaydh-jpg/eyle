import json

import pytest

import engine.agent as agent_mod
import llm.executar as llm_mod
from engine.agent_state import AgentState
from engine.benchmark import _avaliar_completude, _avaliar_fato, _grounding_do_detalhe
from llm.response_adapter import normalize_model_response


class _RespostaFalsa:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._bytes


def _config_agente(**agent_overrides):
    agent = {
        "enabled": True,
        "enabled_modes": ["analyze", "suggest", "edit"],
        "max_steps": 8,
        "max_no_progress_decisions": 3,
        "max_tentativas_parse": 1,
        "max_erros_consecutivos": 3,
        "max_chars_por_observacao": 1000,
        "max_fatos_importantes": 10,
        "max_tree_entries": 200,
        "max_tree_depth": 6,
        "max_read_range_lines": 400,
        "require_confirmation_for_write": True,
        "require_confirmation_for_exec": False,
        "exigir_run_tests_apos_escrita": True,
        "project_read_finalizer_enabled": True,
        "deterministic_symbol_lookup_enabled": True,
        "deterministic_post_write_enabled": True,
    }
    agent.update(agent_overrides)
    return {
        "llm": {
            "context_window_tokens": 8192,
            "max_tokens": 700,
            "project_read_finalizer_max_tokens": 1400,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
        "codar": {"fazer_backup": False, "testes": {"ativado": False}},
        "agent": agent,
    }


def test_adapter_preserva_finish_reason_usage_modelo_e_reasoning_tokens():
    resposta = normalize_model_response({
        "id": "chatcmpl-123",
        "model": "qwen3.8-max",
        "choices": [{
            "finish_reason": "length",
            "message": {"content": '{"final":"parcial"}'},
        }],
        "usage": {
            "prompt_tokens": 101,
            "completion_tokens": 512,
            "completion_tokens_details": {"reasoning_tokens": 400},
        },
    })

    assert resposta.content == '{"final":"parcial"}'
    assert resposta.finish_reason == "length"
    assert resposta.prompt_tokens == 101
    assert resposta.completion_tokens == 512
    assert resposta.reasoning_tokens == 400
    assert resposta.model == "qwen3.8-max"
    assert resposta.response_id == "chatcmpl-123"


def test_finish_reason_length_repete_com_orcamento_maior(monkeypatch):
    corpos = iter([
        {
            "id": "one", "model": "qwen3.8-max",
            "choices": [{"finish_reason": "length", "message": {"content": "parcial:"}}],
            "usage": {"completion_tokens": 512, "completion_tokens_details": {"reasoning_tokens": 420}},
        },
        {
            "id": "two", "model": "qwen3.8-max",
            "choices": [{"finish_reason": "stop", "message": {"content": "resposta completa"}}],
            "usage": {"completion_tokens": 180, "completion_tokens_details": {"reasoning_tokens": 80}},
        },
    ])
    payloads = []

    def fake_urlopen(req, timeout=None):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _RespostaFalsa(next(corpos))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_mod, "_resolver_modelo_openai", lambda *args, **kwargs: "auto")
    runtime = {"max_llm_calls": 5, "llm_calls": 0, "max_generated_tokens": 10000}
    config = {
        "llm": {
            "base_url": "http://localhost:8080", "model": "auto",
            "openai_compatible": True, "max_tokens": 512,
            "truncation_retry_multiplier": 2.0,
            "truncation_retry_max_tokens": 1400,
            "cache": {"ativado": False},
        },
        "_runtime_agent_budget": runtime,
    }

    texto = llm_mod._chamar_llm("sistema", "usuario", config)

    assert texto == "resposta completa"
    assert [p["max_tokens"] for p in payloads] == [512, 1024]
    assert runtime["llm_calls"] == 2
    assert runtime["last_llm_response"]["finish_reason"] == "stop"
    assert runtime["last_llm_response"]["configured_model"] == "auto"
    assert runtime["last_llm_response"]["resolved_model"] == "qwen3.8-max"
    assert runtime["last_llm_response"]["truncation_retry"] is True
    assert runtime["last_llm_response"]["reasoning_tokens"] == 80
    assert [item["finish_reason"] for item in runtime["llm_responses"]] == ["length", "stop"]
    assert all(item["latency_ms"] >= 0 for item in runtime["llm_responses"])
    assert runtime["generated_tokens"] == 692


def test_dupla_truncagem_falha_com_codigo_especifico(monkeypatch):
    payloads = []

    def fake_urlopen(req, timeout=None):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _RespostaFalsa({
            "model": "qwen3.8-max",
            "choices": [{"finish_reason": "length", "message": {"content": "ainda parcial:"}}],
            "usage": {"completion_tokens": payloads[-1].get("max_tokens", 512)},
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    runtime = {"max_llm_calls": 5, "llm_calls": 0, "max_generated_tokens": 10000}
    config = {
        "llm": {
            "base_url": "http://localhost:8080", "model": "qwen3.8-max",
            "openai_compatible": True, "max_tokens": 512,
            "truncation_retry_multiplier": 2.0,
            "truncation_retry_max_tokens": 1400,
            "cache": {"ativado": False},
        },
        "_runtime_agent_budget": runtime,
    }

    with pytest.raises(llm_mod.ErroLLM) as erro:
        llm_mod._chamar_llm("sistema", "usuario", config)

    assert erro.value.error_code == "MODEL_OUTPUT_TRUNCATED"
    assert len(payloads) == 2
    assert runtime["last_llm_response"]["finish_reason"] == "length"
    assert runtime["last_llm_response"]["truncation_retry"] is True
    assert [item["finish_reason"] for item in runtime["llm_responses"]] == ["length", "length"]


def test_project_read_usa_finalizer_separado(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("valor = 1\n", encoding="utf-8")
    respostas_agente = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"a.py"}}',
        '{"ready_to_finalize":true}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas_agente))
    chamadas_finalizer = []

    def fake_finalizer(prompt, config):
        chamadas_finalizer.append(prompt)
        return json.dumps({
            "final": {
                "claims": [{
                    "type": "fact",
                    "text": "Em a.py:1, `valor` recebe o inteiro `1`.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                }],
                "verification": "Leitura fresca de a.py:1.",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_project_read_finalizer_llm", fake_finalizer)

    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "Explique a.py", _config_agente(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert pendente is None
    assert texto == "Em a.py:1, `valor` recebe o inteiro `1`."
    assert "rascunho monolitico" not in texto
    assert len(chamadas_finalizer) == 1
    assert "ORIGINAL USER REQUEST:" in chamadas_finalizer[0]
    assert "Return only the required JSON final envelope" in chamadas_finalizer[0]
    assert detalhes["tools_called"] == ["read_file"]


def test_consulta_exata_de_simbolo_nao_depende_do_agente(tmp_path, monkeypatch):
    (tmp_path / "symbols.py").write_text("def existente():\n    return 1\n", encoding="utf-8")

    def agente_nao_deve_ser_chamado(*args, **kwargs):
        raise AssertionError("consulta exata deveria ser roteada deterministicamente")

    monkeypatch.setattr(agent_mod, "executar_agente_llm", agente_nao_deve_ser_chamado)

    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "Verifique se a funcao fantasma existe em symbols.py",
        _config_agente(), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert pendente is None
    assert "fantasma" in texto.lower()
    assert "nao encontr" in texto.lower() or "não encontr" in texto.lower()
    assert detalhes["tools_called"] == ["find_symbol"]
    assert detalhes["negative_evidence"]["error_code"] == "SYMBOL_NOT_FOUND"
    assert agent_mod._simbolo_explicito_objetivo("Check whether function ghost exists in symbols.py") == "ghost"


def test_pos_write_deterministico_forca_testes_e_releitura():
    config = {"agent": {"deterministic_post_write_enabled": True}}
    estado = AgentState(config=config)
    estado.definir_objetivo("edite a.py", "project_write", modo="edit")
    estado.edit_state = {
        "status": "applied_pending_tests",
        "arquivo": "a.py",
        "linha_inicio": 2,
        "linha_fim_original": 4,
        "linha_fim_final": 5,
    }

    assert agent_mod._acao_obrigatoria_goal_state(estado, "edite a.py", config) == {
        "tool": "run_tests", "arguments": {},
    }

    estado.edit_state["status"] = "tests_passed"
    acao = agent_mod._acao_obrigatoria_goal_state(estado, "edite a.py", config)
    assert acao == {
        "tool": "read_range",
        "arguments": {"caminho_relativo": "a.py", "linha_inicio": 2, "linha_fim": 5},
    }


def test_benchmark_separa_factual_grounding_e_completude():
    texto = "Avalia `token and len(token) >= 8`.\nComportamento real observado:"
    assert _avaliar_fato("09_instrucao_maliciosa", texto, "success", "", {}, {}) is True
    assert _avaliar_completude("09_instrucao_maliciosa", texto, "success") is False
    caso = {"id": "09_instrucao_maliciosa"}
    assert _grounding_do_detalhe({"semantic_grounding": {"ok": True}}, caso, True, []) is True
    assert _grounding_do_detalhe({"semantic_grounding": {"ok": False}}, caso, True, []) is False


def test_retomada_de_write_forca_testes_e_releitura_sem_nova_decisao_do_modelo(tmp_path, monkeypatch):
    import engine.agent_tools as tools_mod
    from engine.project_reader import ler_faixa_projeto

    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    leitura = ler_faixa_projeto(str(tmp_path), "a.py", 1, 1)
    proposta = {
        "caminho_relativo": "a.py",
        "linha_inicio": 1,
        "linha_fim": 1,
        "codigo_novo": "valor = 2",
        "file_hash_esperado": leitura["file_hash"],
        "range_hash_esperado": leitura["content_hash"],
    }
    respostas = iter([
        json.dumps({"tool": "read_range", "arguments": {
            "caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1,
        }}),
        json.dumps({"tool": "test_patch_dry_run", "arguments": proposta}),
        json.dumps({"tool": "apply_patch", "arguments": {
            **proposta, "codigo_original_esperado": "valor = 1",
        }}),
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    config = _config_agente()
    config["codar"]["testes"]["ativado"] = True

    status, _, pendente, _ = agent_mod.executar_agente(
        "altere a.py para valor 2", config,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="edit",
    )
    assert status == "needs_user"
    assert pendente["tool_pendente"]["tool"] == "apply_patch"

    chamadas_modelo_retomada = []
    def final_unico(prompt, config):
        chamadas_modelo_retomada.append(prompt)
        return json.dumps({
            "final": {
                "answer": "Em a.py:1, `valor` agora recebe `2`.",
                "evidence_ids": ["ev-0002"],
                "verification": "Testes executados e faixa relida.",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_agente_llm", final_unico)
    monkeypatch.setattr(tools_mod, "rodar_testes_projeto", lambda *a, **k: {
        "executado": True, "ok": True, "detalhe": "suite passou",
    })

    status, texto, _, detalhes = agent_mod.executar_agente(
        "altere a.py para valor 2", config,
        projeto={"caminho_origem": str(tmp_path)},
        retomar=json.loads(json.dumps(pendente)),
        retornar_detalhes=True, modo="edit",
    )

    assert status == "success"
    assert texto == "Em a.py:1, `valor` agora recebe `2`."
    assert arquivo.read_text(encoding="utf-8") == "valor = 2\n"
    assert chamadas_modelo_retomada and len(chamadas_modelo_retomada) == 1
    assert detalhes["tools_called"][-3:] == ["apply_patch", "run_tests", "read_range"]
    assert detalhes["edit_state"]["status"] == "tests_passed"
    assert detalhes["edit_state"]["post_write_evidence_id"] == "ev-0002"
    evidencia_final = next(item for item in detalhes["evidence_registry"]["items"] if item["id"] == "ev-0002")
    assert evidencia_final["arquivo"] == "a.py"


def test_benchmark_acumula_chamadas_llm_antes_e_depois_da_confirmacao(tmp_path, monkeypatch):
    import engine.benchmark as benchmark_mod

    chamadas = []

    def fake_agente(*args, **kwargs):
        retomar = kwargs.get("retomar")
        chamadas.append(retomar is not None)
        if retomar is None:
            return (
                "needs_user",
                "confirma?",
                {
                    "id": "pending-1",
                    "tool_pendente": {
                        "tool": "apply_patch",
                        "arguments": {
                            "caminho_relativo": "calc.py",
                            "linha_inicio": 1,
                            "linha_fim": 2,
                            "codigo_novo": "def soma(a,b):\n    return a+b",
                            "file_hash_esperado": "a" * 64,
                            "range_hash_esperado": "b" * 64,
                        },
                    },
                    "estado": {},
                },
                {"llm_responses": [{"resolved_model": "qwen3.8-max", "finish_reason": "stop", "latency_ms": 10}]},
            )
        return (
            "success",
            "Alteracao concluida.",
            None,
            {
                "llm_responses": [{"resolved_model": "qwen3.8-max", "finish_reason": "stop", "latency_ms": 20}],
                "edit_state": {"status": "tests_passed", "post_write_evidence_id": "ev-0002"},
                "semantic_grounding": {"ok": True},
            },
        )

    monkeypatch.setattr(benchmark_mod, "ingerir", lambda *a, **k: None)
    monkeypatch.setattr(agent_mod, "executar_agente", fake_agente)

    resultado = benchmark_mod._rodar_caso(
        {"id": "06_edicao_confirmada", "modo": "edit", "leitura": True},
        _config_agente(), str(tmp_path / "caso"),
    )

    assert chamadas == [False, True]
    assert resultado["llm_calls"] == 2
    assert resultado["llm_latency_total_ms"] == 30
    assert resultado["resolved_model"] == "qwen3.8-max"
    assert resultado["finish_reasons"] == ["stop", "stop"]
