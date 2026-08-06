from engine.agent_state import AgentState
from engine.compiler import montar_prompt_agente
from engine.project_reader import listar_arvore_projeto


def _config(max_chars=80):
    return {
        "agent": {
            "max_chars_por_observacao": max_chars,
            "max_fatos_importantes": 10,
            "semantic_repeat_overlap": 0.95,
        },
        "llm": {
            "context_window_tokens": 65536,
            "max_tokens": 1500,
        },
        "context_engine": {
            "safety_margin_tokens": 256,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
    }


def _inventario(total=143, truncado=False):
    entradas = [
        {"caminho": "engine", "tipo": "diretorio", "profundidade": 1},
        {"caminho": "engine/agent.py", "tipo": "arquivo", "profundidade": 2},
        {"caminho": "llm", "tipo": "diretorio", "profundidade": 1},
        {"caminho": "llm/executar.py", "tipo": "arquivo", "profundidade": 2},
        {"caminho": "tests", "tipo": "diretorio", "profundidade": 1},
        {"caminho": "tests/test_agent.py", "tipo": "arquivo", "profundidade": 2},
    ]
    while len(entradas) < total:
        numero = len(entradas)
        entradas.append({
            "caminho": f"pkg/modulo_{numero:03d}.py",
            "tipo": "arquivo",
            "profundidade": 2,
        })
    return {
        "schema_version": 1,
        "inventory_hash": "a" * 64,
        "entradas": entradas,
        "total_retornado": len(entradas),
        "total_arquivos": sum(1 for item in entradas if item["tipo"] == "arquivo"),
        "total_diretorios": sum(1 for item in entradas if item["tipo"] == "diretorio"),
        "limite": 200,
        "profundidade_maxima": 6,
        "filtro": None,
        "truncado": truncado,
        "varredura_completa": not truncado,
        "ignorados_por_motivo": {"gitignore": 2, "segredo": 1},
        "diretorios_raiz": ["engine", "llm", "tests"],
        "arquivos_raiz": ["README.md"],
        "extensoes": {".py": 140},
    }


def _envelope(inventario):
    return {
        "status": "success",
        "ok": True,
        "executed": True,
        "changed": False,
        "error_code": None,
        "detail": inventario,
    }


def test_list_tree_completo_nao_e_perdido_no_resumo_de_500_caracteres():
    estado = AgentState(config=_config(max_chars=80))
    inventario = _inventario()

    acao = estado.registrar_acao("list_tree", {}, _envelope(inventario), contar_execucao=True)
    observacao = estado.observar("list_tree", _envelope(inventario))

    assert len(estado.project_inventory["entradas"]) == 143
    assert estado.project_inventory["entradas"][1]["caminho"] == "engine/agent.py"
    assert estado.project_inventory["entradas"][3]["caminho"] == "llm/executar.py"
    assert estado.project_inventory["entradas"][5]["caminho"] == "tests/test_agent.py"
    assert estado.project_inventory["entradas"][-1]["caminho"] == "pkg/modulo_142.py"
    assert acao["project_inventory_entries"] == 143
    assert acao["project_inventory_complete"] is True
    assert "engine/agent.py" not in observacao["resumo"]


def test_prompt_recebe_todas_as_entradas_do_inventario_estruturado():
    estado = AgentState(config=_config())
    inventario = _inventario()
    estado.registrar_acao("list_tree", {}, _envelope(inventario), contar_execucao=True)
    estado.observar("list_tree", _envelope(inventario))

    prompt = montar_prompt_agente(
        "Faça a análise do projeto",
        observacoes=estado.observacoes,
        goal_state={
            "objective": "Faça a análise do projeto",
            "task_type": "project_read",
            "mode": "analyze",
            "status": "in_progress",
        },
        project_inventory=estado.project_inventory,
        config=_config(),
    )

    assert "PROJECT INVENTORY" in prompt
    assert "PROJECT INVENTORY SUMMARY" in prompt
    assert "F engine/agent.py" not in prompt
    assert '"inventory_hash"' in prompt
    assert '"files":' in prompt
    assert "COVERAGE: complete" in prompt
    bloco = prompt.split("PROJECT INVENTORY", 1)[1].split("TOOL CATALOG", 1)[0]
    assert "caracteres omitidos" not in bloco
    assert "content omitted" not in bloco


def test_inventario_sobrevive_checkpoint_sem_perder_entradas():
    estado = AgentState(config=_config())
    inventario = _inventario()
    estado.registrar_acao("list_tree", {}, _envelope(inventario), contar_execucao=True)

    restaurado = AgentState.from_dict(estado.to_dict(), config=_config())

    assert restaurado.project_inventory == estado.project_inventory
    assert len(restaurado.project_inventory["entradas"]) == 143
    assert restaurado.project_inventory["inventory_hash"] == "a" * 64


def test_prompt_marca_inventario_truncado_como_cobertura_parcial():
    estado = AgentState(config=_config())
    inventario = _inventario(total=20, truncado=True)
    estado.registrar_acao("list_tree", {}, _envelope(inventario), contar_execucao=True)

    prompt = montar_prompt_agente(
        "Analise o projeto",
        project_inventory=estado.project_inventory,
        config=_config(),
    )

    assert '"complete":false' in prompt
    assert "COVERAGE: partial" in prompt
    assert "do not infer absence" in prompt


def test_project_reader_adiciona_metadados_e_hash_estavel(tmp_path):
    (tmp_path / "engine").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "engine" / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_agent.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Projeto\n", encoding="utf-8")

    primeiro = listar_arvore_projeto(tmp_path, limite=50, profundidade=6)
    segundo = listar_arvore_projeto(tmp_path, limite=50, profundidade=6)

    assert primeiro["varredura_completa"] is True
    assert primeiro["total_retornado"] == len(primeiro["entradas"])
    assert primeiro["total_arquivos"] == 3
    assert primeiro["total_diretorios"] == 2
    assert primeiro["diretorios_raiz"] == ["engine", "tests"]
    assert primeiro["arquivos_raiz"] == ["README.md"]
    assert primeiro["extensoes"][".py"] == 2
    assert primeiro["inventory_hash"] == segundo["inventory_hash"]
    assert len(primeiro["inventory_hash"]) == 64
