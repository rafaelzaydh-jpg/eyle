#!/usr/bin/env python3
"""
tests/test_engine.py
---------------------
Atualizacoes 14, 20, 22 e 31 -- cobre a limpeza de erros legados, o contrato
atual de falha da LLM, confirmações vinculadas à pendência/projeto e a
mensagem atual aparecendo uma unica vez no prompt de chat.

    Quando o servidor local da LLM falha (timeout, conexao recusada
    etc.), llm/executar.py:_chamar_llm devolvia uma string "[erro] ..."
    -- e engine/engine.py:_processar_chat salvava isso em
    memory/conversa.json exatamente como qualquer resposta real do
    assistente. Na proxima mensagem, o HISTORICO RECENTE mandado pra
    LLM incluia esse erro como se fosse conversa de verdade, fazendo o
    modelo reagir a ele. A Atualizacao 20 agora levanta ErroLLM e devolve
    status failed sem salvar fala de assistente nem chamar Verify; o
    filtro antigo permanece para limpar conversas produzidas antes dela.

A LLM e a persistencia em disco estao SEMPRE mockadas -- nenhum teste
aqui precisa de um modelo local rodando nem escreve em memory/*.json de
verdade.

Rodar com:
    pip install pytest --break-system-packages   # ou: pip install -r requirements-dev.txt
    pytest tests/test_engine.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.engine as engine_mod  # noqa: E402
from llm.executar import ErroLLM  # noqa: E402


# ---------------------------------------------------------------------------
# 1) _historico_sem_erros_llm -- funcao pura, sem mock necessario
# ---------------------------------------------------------------------------

def test_historico_sem_erros_llm_remove_so_as_mensagens_de_erro():
    mensagens = [
        {"id": 1, "role": "user", "text": "oi"},
        {"id": 2, "role": "assistant", "text": "[erro] Falha ao chamar a LLM local: timed out"},
        {"id": 3, "role": "user", "text": "oi de novo"},
        {"id": 4, "role": "assistant", "text": "[erro] Nao foi possivel conectar em http://localhost:11434."},
        {"id": 5, "role": "user", "text": "ola"},
        {"id": 6, "role": "assistant", "text": "Ola! Como posso ajudar?"},
    ]

    filtradas = engine_mod._historico_sem_erros_llm(mensagens)

    assert [m["id"] for m in filtradas] == [1, 3, 5, 6]
    assert all(not m["text"].startswith("[erro]") for m in filtradas)


def test_historico_sem_erros_llm_nao_quebra_com_lista_vazia_ou_sem_erro():
    assert engine_mod._historico_sem_erros_llm([]) == []
    mensagens = [{"id": 1, "role": "user", "text": "oi"}, {"id": 2, "role": "assistant", "text": "ola!"}]
    assert engine_mod._historico_sem_erros_llm(mensagens) == mensagens


# ---------------------------------------------------------------------------
# 2) _processar_chat -- o historico que chega em executar_chat nao pode
#    conter as mensagens de erro, mesmo com elas presentes em
#    memory/conversa.json (mockado) e dentro da janela das ultimas 6
# ---------------------------------------------------------------------------

def test_processar_chat_nao_repassa_erro_llm_anterior_como_historico(monkeypatch):
    conversa_salva = [
        {"id": 1, "role": "user", "text": "Como melhorar o projeto?"},
        {"id": 2, "role": "assistant", "text": "[erro] Falha ao chamar a LLM local: timed out"},
        {"id": 3, "role": "user", "text": "oi"},
        {"id": 4, "role": "assistant", "text": "[erro] Falha ao chamar a LLM local: conexao recusada"},
        {"id": 5, "role": "user", "text": "ola"},
    ]
    monkeypatch.setattr(engine_mod, "carregar_conversa", lambda: conversa_salva)
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda role, texto: 99)

    historico_recebido = {}

    def fake_executar_chat(pergunta, config, historico=None):
        historico_recebido["valor"] = historico
        return "Ola! Como posso ajudar?"

    monkeypatch.setattr(engine_mod, "executar_chat", fake_executar_chat)

    resultado = engine_mod._processar_chat("ola", {}, "mensagem nao parece precisar do contexto do projeto")

    assert resultado["resposta"] == "Ola! Como posso ajudar?"
    textos_no_historico = [m["text"] for m in historico_recebido["valor"]]
    assert not any(t.startswith("[erro]") for t in textos_no_historico)
    assert textos_no_historico == ["Como melhorar o projeto?", "oi"]


def test_historico_remove_so_a_mensagem_atual_e_preserva_repeticao_antiga():
    mensagens = [
        {"id": 1, "role": "user", "text": "oi"},
        {"id": 2, "role": "assistant", "text": "ola"},
        {"id": 3, "role": "user", "text": "oi"},
    ]

    historico = engine_mod._historico_sem_mensagem_atual(mensagens, "oi")

    assert [m["id"] for m in historico] == [1, 2]


def test_processar_chat_nao_duplica_mensagem_atual_no_historico(monkeypatch):
    snapshot = [
        {"id": 1, "role": "user", "text": "antes"},
        {"id": 2, "role": "assistant", "text": "resposta"},
        {"id": 3, "role": "user", "text": "agora"},
    ]
    chamada = {}
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *args: None)

    def fake_executar_chat(pergunta, config, historico=None):
        chamada["pergunta"] = pergunta
        chamada["historico"] = historico
        return "ok"

    monkeypatch.setattr(engine_mod, "executar_chat", fake_executar_chat)

    engine_mod._processar_chat("agora", {}, "chat", historico_snapshot=snapshot)

    assert chamada["pergunta"] == "agora"
    assert [m["text"] for m in chamada["historico"]] == ["antes", "resposta"]


def test_status_success_do_agente_nao_fabrica_confianca(monkeypatch, tmp_path):
    historicos = []
    monkeypatch.setattr(engine_mod, "CONTEXT_DIR", str(tmp_path))
    monkeypatch.setattr(
        engine_mod, "executar_agente", lambda *args, **kwargs: ("success", "feito", None),
    )
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *args: None)
    monkeypatch.setattr(
        engine_mod, "registrar_historico",
        lambda *args, **kwargs: historicos.append(args[3]),
    )

    resultado = engine_mod._processar_agente(
        "analise", {}, {"caminho_origem": "/tmp/projeto"}, {}, "agente",
    )

    assert resultado["agente_status"] == "success"
    assert resultado["confianca"] is None
    assert resultado["citation_validity"] is None
    assert resultado["coverage"] is None
    assert resultado["grounding"] is None
    assert historicos[0]["grounding"] is None


# ---------------------------------------------------------------------------
# 3) Atualizacao 20 -- ErroLLM encerra como failed, sem mensagem/Verify
# ---------------------------------------------------------------------------

def test_processar_chat_retorna_failed_sem_salvar_fala_de_erro(monkeypatch):
    mensagens_registradas = []
    monkeypatch.setattr(engine_mod, "carregar_conversa", lambda: [])
    monkeypatch.setattr(
        engine_mod, "registrar_mensagem",
        lambda role, texto: mensagens_registradas.append((role, texto)),
    )

    def falhar(*args, **kwargs):
        raise ErroLLM("servidor indisponivel")

    monkeypatch.setattr(engine_mod, "executar_chat", falhar)

    resultado = engine_mod._processar_chat("oi", {}, "chat")

    assert resultado["status"] == "failed"
    assert resultado["confianca"] is None
    assert mensagens_registradas == []


def test_erro_llm_na_consulta_nao_chama_verify_nem_salva_assistente(monkeypatch):
    monkeypatch.setattr(
        engine_mod, "buscar",
        lambda *args, **kwargs: {
            "pergunta": "explique",
            "tokens_usados": 3,
            "trechos": [{
                "arquivo": "app.py", "linhas": "1-2", "score": 1.0,
                "conteudo": "print('ok')",
            }],
            "arquivos_relevantes": ["app.py"],
        },
    )
    monkeypatch.setattr(engine_mod, "carregar_evidencias", lambda: {"entidades": []})
    monkeypatch.setattr(
        engine_mod, "executar_executor",
        lambda *args, **kwargs: (_ for _ in ()).throw(ErroLLM("timeout")),
    )
    monkeypatch.setattr(
        engine_mod, "validar_resposta",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Verify nao deveria rodar")),
    )
    mensagens_registradas = []
    monkeypatch.setattr(
        engine_mod, "registrar_mensagem",
        lambda role, texto: mensagens_registradas.append((role, texto)),
    )

    resultado = engine_mod._processar_consulta(
        "explique", {}, {"nome": "teste"}, {}, {"componentes": {}}, "consulta",
    )

    assert resultado["status"] == "failed"
    assert mensagens_registradas == []


def test_fronteira_da_engenharia_converte_erro_llm_em_failed(monkeypatch):
    monkeypatch.setattr(
        engine_mod, "_processar_engenharia_impl",
        lambda *args, **kwargs: (_ for _ in ()).throw(ErroLLM("backend caiu")),
    )

    resultado = engine_mod._processar_engenharia("mude", {}, {}, {}, {}, "engenharia")

    assert resultado["status"] == "failed"
    assert resultado["roteador"]["tipo"] == "engenharia"


def test_processar_de_ponta_a_ponta_nao_grava_erro_como_assistente(monkeypatch):
    mensagens_registradas = []
    monkeypatch.setattr(engine_mod, "carregar_config", lambda: {})
    monkeypatch.setattr(engine_mod, "carregar_projeto", lambda: None)
    monkeypatch.setattr(engine_mod, "carregar_conversa", lambda: [])
    monkeypatch.setattr(
        engine_mod, "classificar_pergunta",
        lambda *args, **kwargs: ("chat", "conversa livre"),
    )
    monkeypatch.setattr(
        engine_mod, "registrar_mensagem",
        lambda role, texto: mensagens_registradas.append((role, texto)),
    )
    monkeypatch.setattr(
        engine_mod, "executar_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(ErroLLM("timeout")),
    )

    resultado = engine_mod.processar("oi")

    assert resultado["status"] == "failed"
    assert mensagens_registradas == [("user", "oi")]


# ---------------------------------------------------------------------------
# 4) Atualizacao 22 -- confirmacao vinculada a uma pendencia e projeto
# ---------------------------------------------------------------------------

def _pendencia(tipo, codigo, projeto, expira_em="2099-01-01T00:00:00Z"):
    return {
        "id": codigo,
        "tipo_pendencia": tipo,
        "criado_em": "2026-07-31T20:00:00Z",
        "expira_em": expira_em,
        "projeto_hash": engine_mod._hash_projeto(projeto),
    }


def _preparar_processar_pendencias(monkeypatch, proposta, agente, projeto):
    mensagens = []
    monkeypatch.setattr(engine_mod, "carregar_config", lambda: {})
    monkeypatch.setattr(engine_mod, "carregar_projeto", lambda: projeto)
    monkeypatch.setattr(engine_mod, "carregar_proposta_pendente", lambda: proposta)
    monkeypatch.setattr(engine_mod, "carregar_agent_pendente", lambda: agente)
    monkeypatch.setattr(
        engine_mod, "registrar_mensagem",
        lambda role, texto: mensagens.append((role, texto)),
    )
    return mensagens


def test_duas_pendencias_rejeitam_sim_sem_id(monkeypatch):
    projeto = {"projeto": "A", "caminho_origem": "/tmp/projeto-a"}
    proposta = _pendencia("proposta", "7F3A", projeto)
    agente = _pendencia("agente", "9B2C", projeto)
    _preparar_processar_pendencias(monkeypatch, proposta, agente, projeto)
    monkeypatch.setattr(
        engine_mod, "_aplicar_proposta_pendente",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nao deveria aplicar")),
    )
    monkeypatch.setattr(
        engine_mod, "_retomar_agente_pendente",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nao deveria retomar")),
    )

    resultado = engine_mod.processar("sim")

    assert "mais de uma pendencia" in resultado["resposta"]
    assert "7F3A" in resultado["resposta"]
    assert "9B2C" in resultado["resposta"]


def test_id_errado_e_rejeitado_sem_executar(monkeypatch):
    projeto = {"projeto": "A", "caminho_origem": "/tmp/projeto-a"}
    proposta = _pendencia("proposta", "7F3A", projeto)
    agente = _pendencia("agente", "9B2C", projeto)
    _preparar_processar_pendencias(monkeypatch, proposta, agente, projeto)
    monkeypatch.setattr(
        engine_mod, "_aplicar_proposta_pendente",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nao deveria aplicar")),
    )

    resultado = engine_mod.processar("confirmar FFFF")

    assert "Nao existe pendencia ativa com o ID FFFF" in resultado["resposta"]


def test_confirmar_id_escolhe_a_pendencia_certa(monkeypatch):
    projeto = {"projeto": "A", "caminho_origem": "/tmp/projeto-a"}
    proposta = _pendencia("proposta", "7F3A", projeto)
    agente = _pendencia("agente", "9B2C", projeto)
    _preparar_processar_pendencias(monkeypatch, proposta, agente, projeto)
    monkeypatch.setattr(
        engine_mod, "_aplicar_proposta_pendente",
        lambda dados, config: {"selecionada": dados["id"]},
    )
    monkeypatch.setattr(
        engine_mod, "_retomar_agente_pendente",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("escolheu a pendencia errada")),
    )

    resultado = engine_mod.processar("confirmar 7f3a")

    assert resultado == {"selecionada": "7F3A"}


def test_pendencia_expirada_e_descartada_sem_aplicar(monkeypatch):
    projeto = {"projeto": "A", "caminho_origem": "/tmp/projeto-a"}
    proposta = _pendencia("proposta", "7F3A", projeto, expira_em="2020-01-01T00:00:00Z")
    _preparar_processar_pendencias(monkeypatch, proposta, None, projeto)
    limpezas = []
    monkeypatch.setattr(engine_mod, "limpar_proposta_pendente", lambda: limpezas.append(True))
    monkeypatch.setattr(
        engine_mod, "_aplicar_proposta_pendente",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nao deveria aplicar")),
    )

    resultado = engine_mod.processar("sim")

    assert "expirou" in resultado["resposta"]
    assert limpezas == [True]


def test_pendencia_de_outro_projeto_e_rejeitada(monkeypatch):
    projeto_a = {"projeto": "A", "caminho_origem": "/tmp/projeto-a"}
    projeto_b = {"projeto": "B", "caminho_origem": "/tmp/projeto-b"}
    proposta = _pendencia("proposta", "7F3A", projeto_a)
    _preparar_processar_pendencias(monkeypatch, proposta, None, projeto_b)
    monkeypatch.setattr(engine_mod, "limpar_proposta_pendente", lambda: None)

    resultado = engine_mod.processar("sim")

    assert "outro projeto" in resultado["resposta"]


def test_salvar_pendencia_inclui_id_datas_e_hash_do_projeto(monkeypatch):
    projeto = {"projeto": "A", "caminho_origem": "/tmp/projeto-a"}
    gravados = []
    instante = engine_mod.datetime(2026, 7, 31, 20, 0, tzinfo=engine_mod.timezone.utc)
    monkeypatch.setattr(engine_mod, "_novo_id_pendencia", lambda: "7F3A")
    monkeypatch.setattr(engine_mod, "_agora_utc", lambda: instante)
    monkeypatch.setattr(engine_mod, "_salvar_json", lambda caminho, dados: gravados.append(dict(dados)))

    salva = engine_mod.salvar_proposta_pendente(
        {"arquivo": "a.py"}, projeto=projeto,
        config={"confirmacoes": {"expiracao_segundos": 60}},
    )

    assert salva["id"] == "7F3A"
    assert salva["criado_em"] == "2026-07-31T20:00:00Z"
    assert salva["expira_em"] == "2026-07-31T20:01:00Z"
    assert salva["projeto_hash"] == engine_mod._hash_projeto(projeto)
    assert gravados[0] == salva


# ---------------------------------------------------------------------------
# 5) Atualizacao 23 -- ler/ignorar filtram e rodadas acumulam evidencias
# ---------------------------------------------------------------------------

def _atual(pergunta, *trechos):
    return {
        "version": "1.0",
        "pergunta": pergunta,
        "tokens_usados": 999,
        "arquivos_relevantes": [t["arquivo"] for t in trechos],
        "trechos": list(trechos),
        "historico_relacionado": [],
    }


def _trecho(arquivo, conteudo, simbolo="run", linhas="1-2"):
    return {
        "arquivo": arquivo,
        "simbolo": simbolo,
        "linhas": linhas,
        "score": 1.0,
        "conteudo": conteudo,
    }


def test_ciclo_analista_filtra_ignorados_e_acumula_aprovados(monkeypatch, tmp_path):
    rodada_1 = _atual(
        "mude o fluxo",
        _trecho("a.py", "def a():\n    pass"),
        _trecho("ruido.py", "def ruido():\n    pass"),
    )
    rodada_2 = _atual(
        "mude o fluxo helper",
        _trecho("helper.py", "def helper():\n    pass"),
        _trecho("outro.py", "def outro():\n    pass"),
    )
    resultados = iter([rodada_1, rodada_2])
    monkeypatch.setattr(engine_mod, "buscar", lambda *a, **k: next(resultados))
    monkeypatch.setattr(engine_mod, "CONTEXT_DIR", str(tmp_path))
    respostas = iter([
        '{"ler":["a.py:1-2"],"ignorar":["ruido.py:1-2"],'
        '"faltando":["helper"],"riscos":[],"motivo":"falta helper"}',
        '{"ler":["helper.py:1-2"],"ignorar":["outro.py"],'
        '"faltando":[],"riscos":[],"motivo":"suficiente"}',
    ])
    monkeypatch.setattr(engine_mod, "executar_analista", lambda *a, **k: next(respostas))

    config = {
        "engine": {"max_iteracoes_analista": 2, "atalho_analista_ativado": False},
        "context": {"chars_per_token": 4},
    }
    atual, decisoes = engine_mod.ciclo_analista(
        "mude o fluxo", config, estrutura={}, evidencias=[], entendimento={},
    )

    assert [t["arquivo"] for t in atual["trechos"]] == ["a.py", "helper.py"]
    assert atual["arquivos_relevantes"] == ["a.py", "helper.py"]
    assert atual["pergunta"] == "mude o fluxo"
    assert atual["tokens_usados"] != 999
    assert len(decisoes) == 2

    salvo = engine_mod._carregar_json(tmp_path / "atual.json", {})
    assert [t["arquivo"] for t in salvo["trechos"]] == ["a.py", "helper.py"]
    assert "ruido.py" not in str(salvo)
    assert "outro.py" not in str(salvo)


def test_filtro_aceita_seletor_estruturado_e_ignorar_tem_prioridade():
    trechos = [
        _trecho("classes.py", "class A: pass", simbolo="A.run", linhas="10-12"),
        _trecho("classes.py", "class B: pass", simbolo="B.run", linhas="20-22"),
    ]
    decisao = {
        "ler": [
            {"arquivo": "classes.py", "simbolo": "A.run"},
            {"arquivo": "classes.py", "simbolo": "B.run"},
        ],
        "ignorar": ["B.run"],
    }

    filtrados = engine_mod._filtrar_trechos_decisao(trechos, decisao)

    assert [t["simbolo"] for t in filtrados] == ["A.run"]

    apenas_ignorar = engine_mod._filtrar_trechos_decisao(
        trechos, {"ler": [], "ignorar": ["B.run"]},
    )
    assert [t["simbolo"] for t in apenas_ignorar] == ["A.run"]


def test_contexto_acumulado_continua_respeitando_token_budget():
    trechos = [
        _trecho("a.py", "a" * 20),
        _trecho("b.py", "b" * 20),
    ]

    atual = engine_mod._montar_atual_aprovado(
        "objetivo", {}, trechos, [],
        {"context": {"chars_per_token": 4, "token_budget": 6}},
    )

    assert [t["arquivo"] for t in atual["trechos"]] == ["a.py"]
    assert atual["tokens_usados"] == 5
    assert atual["trechos_aprovados_fora_do_orcamento"] == 1
