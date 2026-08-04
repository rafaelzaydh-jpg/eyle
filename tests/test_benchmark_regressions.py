import hashlib

from engine.agent_state import AgentState
from engine.benchmark import _avaliar_fato
from engine.codar import testar_patch_em_copia as _testar_patch_em_copia
from engine.compiler import montar_prompt_agente
from engine.project_reader import ler_faixa_projeto
from engine.text_hash import hash_faixa


def _ok(detail):
    return {
        "status": "success",
        "ok": True,
        "executed": True,
        "changed": False,
        "error_code": None,
        "detail": detail,
    }


def test_hash_da_faixa_e_derivado_de_read_file_amplo(tmp_path):
    codigo = "def soma(a, b):\n    return a + b\n\ndef saudacao():\n    return 'oi'\n"
    (tmp_path / "calc.py").write_text(codigo, encoding="utf-8")
    leitura = ler_faixa_projeto(str(tmp_path), "calc.py", 1, 400)

    estado = AgentState(config={})
    estado.definir_objetivo("edite calc.py", "project_write", modo="edit")
    estado.registrar_acao(
        "read_file", {"caminho_relativo": "calc.py"}, _ok(leitura),
        contar_execucao=True,
    )

    argumentos = estado.completar_argumentos_patch(
        "test_patch_dry_run",
        {
            "caminho_relativo": "calc.py",
            "linha_inicio": 1,
            "linha_fim": 2,
            "codigo_novo": 'def soma(a, b):\n    """Soma dois valores."""\n    return a + b',
            "file_hash_esperado": "0" * 64,
            "range_hash_esperado": "f" * 64,
        },
    )

    assert argumentos["file_hash_esperado"] == leitura["file_hash"]
    assert argumentos["range_hash_esperado"] == hash_faixa(codigo, 1, 2)
    resultado = _testar_patch_em_copia(
        str(tmp_path), "calc.py", 1, 2, argumentos["codigo_novo"],
        file_hash_esperado=argumentos["file_hash_esperado"],
        range_hash_esperado=argumentos["range_hash_esperado"],
    )
    assert resultado["ok"] is True


def test_hash_canonico_nao_falha_so_por_crlf(tmp_path):
    conteudo_crlf = b"def soma(a, b):\r\n    return a + b\r\n"
    (tmp_path / "calc.py").write_bytes(conteudo_crlf)
    leitura = ler_faixa_projeto(str(tmp_path), "calc.py", 1, 2)

    resultado = _testar_patch_em_copia(
        str(tmp_path), "calc.py", 1, 2,
        'def soma(a, b):\n    return a - b',
        file_hash_esperado=leitura["file_hash"],
        range_hash_esperado=leitura["content_hash"],
    )
    assert resultado["ok"] is True


def test_benchmark_aceita_equivalentes_semanticos():
    assert _avaliar_fato(
        "02_localizar_funcao",
        "A funcao aplica strip e converte o resultado para minusculo.",
        "success", "", {}, {},
    )
    assert _avaliar_fato(
        "09_instrucao_maliciosa",
        "The token must be non-empty and have at least eight characters.",
        "success", "", {}, {},
    )


def test_prompt_explicita_proximo_gate_de_edicao():
    prompt = montar_prompt_agente(
        "adicione uma docstring em calc.py",
        goal_state={"mode": "edit", "objective": "editar", "status": "in_progress"},
        evidencias=[{
            "id": "ev-0001", "estado": "fresh", "arquivo": "calc.py",
            "linha_inicio": 1, "linha_fim": 2, "conteudo": "1 | def soma...",
            "content_hash": hashlib.sha256(b"x").hexdigest(),
            "file_hash": hashlib.sha256(b"y").hexdigest(),
        }],
        actions=[{"tool": "read_file", "ok": True, "executed": True}],
        edit_state={},
        config={"llm": {"context_window_tokens": 4096, "max_tokens": 500}},
    )
    assert "MANDATORY NEXT EDIT ACTION" in prompt
    assert "test_patch_dry_run" in prompt


def test_agente_corrige_hash_do_modelo_antes_do_dry_run(tmp_path, monkeypatch):
    import json
    import engine.agent as agent_mod

    (tmp_path / "calc.py").write_text(
        "def soma(a, b):\n    return a + b\n\ndef saudacao():\n    return 'oi'\n",
        encoding="utf-8",
    )
    respostas = iter([
        json.dumps({"tool": "read_file", "arguments": {"caminho_relativo": "calc.py"}}),
        json.dumps({
            "tool": "test_patch_dry_run",
            "arguments": {
                "caminho_relativo": "calc.py", "linha_inicio": 1, "linha_fim": 2,
                "codigo_novo": 'def soma(a, b):\n    """Soma."""\n    return a + b',
                "file_hash_esperado": "0" * 64,
                "range_hash_esperado": "f" * 64,
            },
        }),
        json.dumps({
            "tool": "apply_patch",
            "arguments": {
                "caminho_relativo": "calc.py", "linha_inicio": 1, "linha_fim": 2,
                "codigo_original_esperado": "errado",
                "codigo_novo": 'def soma(a, b):\n    """Soma."""\n    return a + b',
                "file_hash_esperado": "1" * 64,
                "range_hash_esperado": "2" * 64,
            },
        }),
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    config = {
        "llm": {"context_window_tokens": 4096, "max_tokens": 500},
        "context_engine": {"safety_margin_tokens": 256, "chars_per_token_fallback": 3},
        "codar": {"fazer_backup": False, "testes": {"ativado": False}},
        "agent": {
            "rollout_mode": "full", "enabled_modes": ["analyze", "suggest", "edit"],
            "max_steps": 8, "max_no_progress_decisions": 3,
            "max_tentativas_parse": 1, "max_erros_consecutivos": 3,
            "max_read_range_lines": 400, "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
        },
    }

    status, _, pendente, _ = agent_mod.executar_agente(
        "Em calc.py, adicione uma docstring curta a soma sem mudar o comportamento.",
        config, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="edit",
    )

    assert status == "needs_user"
    assert pendente["tool_pendente"]["tool"] == "apply_patch"
    argumentos = pendente["tool_pendente"]["arguments"]
    leitura = ler_faixa_projeto(str(tmp_path), "calc.py", 1, 2)
    assert argumentos["file_hash_esperado"] == leitura["file_hash"]
    assert argumentos["range_hash_esperado"] == leitura["content_hash"]
    assert argumentos["codigo_original_esperado"] == "def soma(a, b):\n    return a + b"
