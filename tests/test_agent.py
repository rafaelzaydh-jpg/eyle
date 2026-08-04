#!/usr/bin/env python3
"""
tests/test_agent.py
--------------------
Fase 4 do plano do Agente (Atualizacao_Agente.md) -- primeiro arquivo de
teste automatizado do projeto inteiro. Cobre os 5 criterios de pronto
que as Atualizacoes 1-5/Fase 3 ja definiam, mais os das Atualizacoes
10-13 (correcao dos "erros menores" mapeados em sessao de revisao):

  1. resposta malformada da LLM -> decidir_passo faz retry e nao trava
  2. tarefa de 6+ passos -> max_steps interrompe corretamente
  3. mesma (tool, arguments) duas vezes -> guarda de repeticao barra
  4. tool WRITE -> pausa em needs_user com estado serializavel, e
     retomar=... executa a tool confirmada e continua do passo certo
     (Fase 3) -- agora tambem exige run_tests ok antes de aceitar
     "final" (Atualizacao 10, ver criterio 6)
  5. projeto sem agent_tools.py (regressao simulada) -> engine/agent.py
     cai no stub (TOOLS={}, resultado padrao com status failed) sem
     lancar excecao nem impedir o resto do modulo de funcionar
  6. Atualizacao 10 (verificador de conclusao): "final" apos escrita sem
     run_tests ok e' recusado, e so' aceito depois que run_tests roda
  7. Atualizacao 11 (circuit breaker): N erros de tool SEGUIDOS param o
     loop em needs_user; um sucesso no meio zera o contador
  8. Atualizacao 12 (fatos_importantes): fato registrado pela LLM
     sobrevive ao corte de max_entradas que ja afeta observacoes
     normais
  9. Atualizacao 13 (roteador): pergunta que menciona o projeto mas nao
     bate em nenhuma categoria especifica cai em "visao_geral" (com
     contexto), nao mais em "chat" (sem contexto nenhum) -- sem
     regressao pra mensagens realmente sem relacao com o projeto

A LLM e as tools estao SEMPRE mockadas -- nenhum teste aqui precisa de
um modelo local rodando nem de um projeto indexado de verdade. So
engine/agent.py, engine/agent_state.py e engine/roteador.py (criterio 9)
sao exercitados (por isso o arquivo se chama test_agent.py, nao
test_engine.py -- os pipelines chat/consulta/dicas/engenharia/Codar
ficam fora do escopo desta atualizacao, ver "Principios do plano" em
Atualizacao_Agente.md).

Rodar com:
    pip install pytest --break-system-packages   # ou: pip install -r requirements-dev.txt
    pytest tests/test_agent.py -v
"""
import builtins
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers de teste (nenhum aqui e' logica de produto -- so fabricas de
# mock reaproveitadas pelos 5 testes abaixo).
# ---------------------------------------------------------------------------

def _config(**overrides_agent):
    """Config minima com config['agent'] isolada por teste (dict novo a
    cada chamada -- nenhum teste compartilha ou muta a config de outro)."""
    cfg_agent = {
        "max_steps": 8,
        "max_tentativas_parse": 2,
        "require_confirmation_for_write": True,
        "require_confirmation_for_exec": False,
        "max_chars_por_observacao": 500,
    }
    cfg_agent.update(overrides_agent)
    return {"agent": cfg_agent}


def _sequencia_llm(respostas):
    """fake_llm(prompt, config) que devolve as respostas da lista, uma
    por chamada, na ordem em que decidir_passo/executar_agente pedirem.
    Pedir mais chamadas do que o previsto estoura StopIteration, o que
    deixaria o teste falhar de forma visivel (sinal de bug no loop, nao
    algo pra mascarar)."""
    it = iter(respostas)

    def fake_llm(prompt, config):
        return next(it)

    return fake_llm


# ---------------------------------------------------------------------------
# 1) Resposta malformada da LLM -> decidir_passo faz retry e nao trava
# ---------------------------------------------------------------------------

def test_retry_de_parsing_recupera_apos_formato_invalido(monkeypatch):
    """Primeira resposta da LLM nao tem JSON reconhecivel; a segunda
    (apos o reforco de formato) e valida -- o loop deve aceitar essa e
    seguir normalmente, sem tratar a primeira falha como erro fatal."""
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        "desculpa, nao entendi o que voce quer, pode reformular?",
        '{"final": "concluido apos retry"}',
    ]))
    monkeypatch.setattr(agent_mod, "TOOLS", {})
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: {"ok": True})

    status, texto, pendente = agent_mod.executar_agente("tarefa qualquer", _config(max_tentativas_parse=2))

    assert status == "success"
    assert texto == "concluido apos retry"
    assert pendente is None


def test_retry_de_parsing_desiste_de_forma_limpa_apos_max_tentativas(monkeypatch):
    """Se TODAS as tentativas vierem malformadas, o loop deve devolver
    'failed' -- nunca lancar excecao nem travar esperando uma resposta
    que nao vai vir."""
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        "oi", "ainda nao entendi", "de novo nao",
    ]))
    monkeypatch.setattr(agent_mod, "TOOLS", {})
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: {"ok": True})

    status, texto, pendente = agent_mod.executar_agente("tarefa qualquer", _config(max_tentativas_parse=2))

    assert status == "failed"
    assert pendente is None


# ---------------------------------------------------------------------------
# 2) Tarefa de 6+ passos -> max_steps interrompe corretamente
# ---------------------------------------------------------------------------

def test_max_steps_interrompe_tarefa_longa_no_passo_certo(monkeypatch):
    """A LLM nunca devolve 'final' -- so pede uma tool_call de leitura
    por passo (com um argumento diferente a cada vez, pra nao esbarrar
    na guarda de repeticao, testada em separado abaixo). O loop tem que
    parar EXATAMENTE em max_steps, nem um passo a mais nem a menos."""
    max_steps = 6
    respostas_llm = [
        json.dumps({"tool": "search_code", "arguments": {"pergunta": f"busca {i}"}})
        for i in range(max_steps + 3)  # mais respostas do que o loop deveria consumir
    ]
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm(respostas_llm))
    monkeypatch.setattr(agent_mod, "TOOLS", {"search_code": {"permission": "READ"}})

    chamadas = []

    def fake_executar_tool(nome, arguments, ctx):
        chamadas.append(arguments)
        return {"resultados": []}

    monkeypatch.setattr(agent_mod, "executar_tool", fake_executar_tool)

    status, texto, pendente = agent_mod.executar_agente("tarefa longa", _config(max_steps=max_steps))

    assert status == "max_steps"
    assert len(chamadas) == max_steps
    assert pendente is None


# ---------------------------------------------------------------------------
# 3) Mesma (tool, arguments) duas vezes -> guarda de repeticao barra
# ---------------------------------------------------------------------------

def test_guarda_de_repeticao_barra_mesma_tool_e_argumentos(monkeypatch):
    """A LLM pede a MESMA (tool, arguments) duas vezes seguidas -- a
    segunda vez tem que ser barrada (vira uma observacao de aviso, sem
    executar a tool de novo); a terceira resposta (diferente) fecha a
    tarefa normalmente."""
    argumentos_repetidos = {"pergunta": "onde fica a funcao X"}
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "search_code", "arguments": argumentos_repetidos}),
        json.dumps({"tool": "search_code", "arguments": argumentos_repetidos}),  # repetida
        '{"final": "achei depois de revisar as observacoes"}',
    ]))
    monkeypatch.setattr(agent_mod, "TOOLS", {"search_code": {"permission": "READ"}})

    execucoes = []

    def fake_executar_tool(nome, arguments, ctx):
        execucoes.append(arguments)
        return {"resultados": ["a.py:10-20"]}

    monkeypatch.setattr(agent_mod, "executar_tool", fake_executar_tool)

    status, texto, pendente = agent_mod.executar_agente("tarefa com repeticao", _config(max_steps=8))

    assert status == "success"
    assert texto == "achei depois de revisar as observacoes"
    assert len(execucoes) == 1  # a tool so rodou de verdade UMA vez
    assert pendente is None


# ---------------------------------------------------------------------------
# 4) Tool WRITE -> pausa em needs_user, persiste, retoma com confirmacao
#    (Fase 3 -- AgentState.to_dict()/from_dict() e executar_agente(retomar=...))
# ---------------------------------------------------------------------------

def test_tool_write_pausa_persiste_e_retoma_com_confirmacao(monkeypatch):
    """apply_patch (permission=WRITE) tem que pausar o loop em
    'needs_user' com um estado_pendente pronto pra virar JSON de verdade
    (o que engine/engine.py salva em context/agent_pendente.json) --
    SEM executar a tool ainda. Retomando com esse mesmo dict (apos ida e
    volta real por json.dumps/json.loads, simulando o disco), a tool
    pendente e' executada e SO ENTAO o loop continua, sem reexecutar
    nenhum passo anterior."""
    monkeypatch.setattr(agent_mod, "TOOLS", {
        "apply_patch": {"permission": "WRITE"},
        "run_tests": {"permission": "EXEC"},
    })

    execucoes = []

    def fake_executar_tool(nome, arguments, ctx):
        execucoes.append((nome, arguments))
        if nome == "run_tests":
            return {"status": "success", "ok": True, "executed": True, "changed": False,
                    "error_code": None, "detail": "1 passed"}
        return {"status": "success", "ok": True, "executed": True, "changed": True,
                "error_code": None, "detail": "patch aplicado"}

    monkeypatch.setattr(agent_mod, "executar_tool", fake_executar_tool)

    argumentos_patch = {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 2, "codigo_novo": "pass"}
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": argumentos_patch}),
    ]))

    status, texto, estado_pendente = agent_mod.executar_agente("corrige o bug", _config())

    assert status == "needs_user"
    assert execucoes == []  # ainda NAO executou -- so pausou esperando confirmacao
    assert estado_pendente["tool_pendente"] == {"tool": "apply_patch", "arguments": argumentos_patch}
    assert estado_pendente["objetivo"] == "corrige o bug"

    # ida e volta real por JSON, igual context/agent_pendente.json faria
    estado_pendente_via_disco = json.loads(json.dumps(estado_pendente))

    # Atualizacao 10: apos a escrita confirmada, o loop so aceita "final"
    # depois de 'run_tests' rodar com sucesso -- por isso a sequencia
    # abaixo tem uma tool_call de run_tests antes do final, diferente de
    # antes da Atualizacao 10 (quando "final" direto ja bastava).
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "run_tests", "arguments": {}}),
        '{"final": "patch confirmado, testado e aplicado"}',
    ]))

    status2, texto2, pendente2 = agent_mod.executar_agente(
        estado_pendente_via_disco["objetivo"], _config(), retomar=estado_pendente_via_disco,
    )

    assert status2 == "success"
    assert texto2 == "patch confirmado, testado e aplicado"
    # apply_patch so executou na retomada; run_tests rodou logo depois, antes do final
    assert execucoes == [("apply_patch", argumentos_patch), ("run_tests", {})]
    assert pendente2 is None


def test_final_apos_escrita_sem_run_tests_e_recusado(monkeypatch):
    """Atualizacao 10 -- se a LLM tenta {"final": ...} logo apos uma
    escrita (apply_patch) SEM ter rodado 'run_tests' com sucesso antes,
    o loop tem que recusar (nao aceitar de primeira) e dar mais um passo
    em vez de confiar na palavra da LLM. So' aceita depois que run_tests
    roda e devolve ok=True."""
    monkeypatch.setattr(agent_mod, "TOOLS", {
        "apply_patch": {"permission": "WRITE"},
        "run_tests": {"permission": "EXEC"},
    })

    execucoes = []

    def fake_executar_tool(nome, arguments, ctx):
        execucoes.append(nome)
        if nome == "run_tests":
            return {"status": "success", "ok": True, "executed": True, "changed": False,
                    "error_code": None, "detail": "ok"}
        return {"status": "success", "ok": True, "executed": True, "changed": True,
                "error_code": None, "detail": "patch aplicado"}

    monkeypatch.setattr(agent_mod, "executar_tool", fake_executar_tool)

    argumentos_patch = {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 2, "codigo_novo": "pass"}
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": argumentos_patch}),
    ]))
    status, _, estado_pendente = agent_mod.executar_agente("corrige o bug", _config())
    assert status == "needs_user"

    estado_pendente_via_disco = json.loads(json.dumps(estado_pendente))

    # A LLM tenta finalizar DIRETO, sem rodar run_tests -- deve ser
    # recusada uma vez (observacao pedindo run_tests) e so' aceita depois
    # que a segunda resposta chama run_tests e a terceira finaliza.
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        '{"final": "terminei sem rodar teste"}',
        json.dumps({"tool": "run_tests", "arguments": {}}),
        '{"final": "terminei de verdade, apos run_tests"}',
    ]))

    status2, texto2, pendente2 = agent_mod.executar_agente(
        estado_pendente_via_disco["objetivo"], _config(), retomar=estado_pendente_via_disco,
    )

    assert status2 == "success"
    assert texto2 == "terminei de verdade, apos run_tests"
    assert "run_tests" in execucoes
    assert pendente2 is None


# ---------------------------------------------------------------------------
# 5) Projeto sem agent_tools.py (regressao simulada) -> cai no stub sem
#    quebrar o restante do sistema
# ---------------------------------------------------------------------------

def test_regressao_sem_agent_tools_cai_no_stub_sem_quebrar(monkeypatch):
    """Se 'from engine.agent_tools import TOOLS, executar_tool' falhar
    (arquivo removido/corrompido -- regressao), engine/agent.py precisa
    continuar IMPORTAVEL e funcional: TOOLS vira {} e executar_tool vira
    o stub que devolve o contrato padrao de falha em vez de propagar o ImportError.
    Reidrata o modulo de verdade (importlib.reload) com o import real de
    'engine.agent_tools' bloqueado, pra exercitar o try/except de
    producao -- nao um substituto escrito so pro teste."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "engine.agent_tools":
            raise ImportError("simulando engine/agent_tools.py removido (regressao)")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        importlib.reload(agent_mod)
        assert agent_mod.TOOLS == {}
        resultado = agent_mod.executar_tool("apply_patch", {}, {})
        assert resultado["status"] == "failed"
        assert resultado["error_code"] == "TOOL_REGISTRY_UNAVAILABLE"
    finally:
        monkeypatch.undo()  # restaura builtins.__import__ ANTES do reload seguinte
        importlib.reload(agent_mod)  # restaura TOOLS/executar_tool reais pros demais testes


# ---------------------------------------------------------------------------
# 6) Atualizacao 11 -- circuit breaker de erro consecutivo
# ---------------------------------------------------------------------------

def test_circuit_breaker_para_apos_n_erros_consecutivos(monkeypatch):
    """3 erros de tool SEGUIDOS (tools/argumentos diferentes entre si, pra
    nao esbarrar na guarda de repeticao da Atualizacao 3) tem que parar o
    loop em 'needs_user' -- mesmo a LLM tendo "passos" sobrando no
    max_steps configurado."""
    monkeypatch.setattr(agent_mod, "TOOLS", {"search_code": {"permission": "READ"}})
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "search_code", "arguments": {"pergunta": "busca 1"}}),
        json.dumps({"tool": "search_code", "arguments": {"pergunta": "busca 2"}}),
        json.dumps({"tool": "search_code", "arguments": {"pergunta": "busca 3"}}),
        json.dumps({"tool": "search_code", "arguments": {"pergunta": "busca 4 -- nao deveria rodar"}}),
    ]))
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: {"erro": "falha simulada"})

    status, texto, pendente = agent_mod.executar_agente(
        "tarefa com erro persistente", _config(max_steps=8, max_erros_consecutivos=3),
    )

    assert status == "needs_user"
    assert "3 erro" in texto
    # Atualizacao 49: todo needs_user, inclusive circuit breaker, conserva
    # uma continuacao retomavel em vez de obrigar o usuario a recomecar.
    assert pendente["continuation_kind"] == "user_input"
    assert pendente["estado"]["erros_consecutivos"] == 3


def test_circuit_breaker_zera_apos_sucesso_no_meio(monkeypatch):
    """2 erros, depois 1 sucesso, depois mais 2 erros -- o contador tem
    que ZERAR no sucesso do meio, entao 4 erros no total NAO deveriam
    estourar um limite de 3 (nunca chegam a 3 SEGUIDOS)."""
    monkeypatch.setattr(agent_mod, "TOOLS", {"search_code": {"permission": "READ"}})
    resultados = iter([
        {"erro": "falha 1"}, {"erro": "falha 2"}, {"resultados": ["ok"]},
        {"erro": "falha 3"}, {"erro": "falha 4"},
    ])
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: next(resultados))
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "search_code", "arguments": {"pergunta": f"busca {i}"}})
        for i in range(4)
    ] + ['{"final": "concluido apos recuperar"}']))

    status, texto, pendente = agent_mod.executar_agente(
        "tarefa com erro intermitente", _config(max_steps=8, max_erros_consecutivos=3),
    )

    assert status == "success"
    assert texto == "concluido apos recuperar"
    assert pendente is None


# ---------------------------------------------------------------------------
# 7) Atualizacao 12 -- fatos_importantes sempre entram no prompt, mesmo
#    apos o corte de max_entradas das observacoes normais
# ---------------------------------------------------------------------------

def test_fato_importante_e_registrado_e_sobrevive_ao_corte_de_observacoes(monkeypatch):
    """A LLM registra um fato_importante no primeiro passo; varios passos
    depois (mais que max_entradas), o prompt ainda tem que conter esse
    fato -- mesmo com a observacao original ja fora do HISTORICO
    RECENTE."""
    monkeypatch.setattr(agent_mod, "TOOLS", {"search_code": {"permission": "READ"}})
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: {"resultados": ["a.py:1-10"]})

    respostas = [json.dumps({
        "tool": "search_code",
        "arguments": {"pergunta": "busca 1"},
        "fato_importante": "o projeto usa pytest, comando: pytest tests/",
    })]
    respostas += [
        json.dumps({"tool": "search_code", "arguments": {"pergunta": f"busca {i}"}})
        for i in range(2, 7)
    ]
    respostas.append('{"final": "concluido"}')
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm(respostas))

    prompts_vistos = []
    real_montar = agent_mod.montar_prompt_agente

    def montar_prompt_espiao(*args, **kwargs):
        prompt = real_montar(*args, **kwargs)
        prompts_vistos.append(prompt)
        return prompt

    monkeypatch.setattr(agent_mod, "montar_prompt_agente", montar_prompt_espiao)

    status, texto, pendente = agent_mod.executar_agente("tarefa longa", _config(max_steps=8))

    assert status == "success"
    # o fato apareceu no ULTIMO prompt (o mais distante do passo em que
    # foi registrado), mesmo a observacao original ja tendo saido do
    # HISTORICO RECENTE (max_entradas default = 4)
    assert "pytest tests/" in prompts_vistos[-1]


# ---------------------------------------------------------------------------
# 8) Atualizacao 13 -- roteador nao deixa pergunta sobre o projeto cair
#    silenciosamente em "chat" (sem nenhum contexto)
# ---------------------------------------------------------------------------

def test_roteador_nao_deixa_pergunta_de_melhoria_cair_em_chat_sem_contexto():
    """Caso real que motivou a correcao: 'Como melhorar o projeto? Me de
    3 caminhos' nao batia em PALAVRAS_DICAS nem em nenhuma outra
    categoria, e caia direto em 'chat' (zero contexto do projeto) --
    agora tem que cair em 'visao_geral' (que le estrutura.json/
    entendimento.json antes de responder)."""
    import engine.roteador as roteador_mod

    tipo, motivo = roteador_mod.classificar_pergunta(
        "Como melhorar o projeto? Me de 3 caminhos", estrutura={}, entendimento={},
    )
    assert tipo == "visao_geral"


def test_roteador_mensagem_sem_nenhuma_relacao_com_projeto_continua_em_chat():
    """Rede de seguranca da Atualizacao 13 e' especifica pra mencao ao
    projeto -- uma mensagem generica sem relacao nenhuma ('oi, tudo
    bem?') continua caindo em 'chat' normalmente, sem regressao."""
    import engine.roteador as roteador_mod

    tipo, motivo = roteador_mod.classificar_pergunta("oi, tudo bem?", estrutura={}, entendimento={})
    assert tipo == "chat"


# ---------------------------------------------------------------------------
# 9) Atualizacao 16 -- circuit breaker tambem conta {"ok": False}, nao so
#    {"erro": ...} (buraco real na Atualizacao 11, achado em auditoria)
# ---------------------------------------------------------------------------

def test_circuit_breaker_conta_ok_false_mesmo_sem_chave_erro(monkeypatch):
    """3 falhas seguidas de apply_patch no formato {"ok": False, ...}
    (sem chave "erro" nenhuma -- formato real que apply_patch/run_tests
    usam pra reportar falha de negocio) tem que acionar o breaker, nao
    so falhas no formato {"erro": ...}.

    Cada apply_patch e' uma tool WRITE -- exige seu proprio ciclo
    needs_user/retomar (nao da pra encadear duas tentativas de escrita
    numa unica chamada de executar_agente), entao o teste faz o
    vai-e-volta 3 vezes, uma por tentativa."""
    monkeypatch.setattr(agent_mod, "TOOLS", {"apply_patch": {"permission": "WRITE"}})
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: {
        "status": "failed", "ok": False, "executed": True, "changed": False,
        "error_code": "PATCH_FAILED", "detail": "patch nao aplicou",
    })

    def argumentos(variacao):
        return {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": variacao, "codigo_novo": "pass"}

    # 1a chamada: so pede confirmacao, ainda nao executa nada
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": argumentos(2)}),
    ]))
    status, _, pendente = agent_mod.executar_agente("aplica um patch", _config(max_erros_consecutivos=3))
    assert status == "needs_user"

    # retomada 1: executa a 1a tentativa (falha, erros_consecutivos=1),
    # depois a LLM decide tentar nova variacao -> pausa de novo (pendente 2)
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": argumentos(3)}),
    ]))
    status, _, pendente = agent_mod.executar_agente(
        "aplica um patch", _config(max_erros_consecutivos=3), retomar=json.loads(json.dumps(pendente)),
    )
    assert status == "needs_user"

    # retomada 2: executa a 2a tentativa (falha, erros_consecutivos=2),
    # LLM tenta mais uma variacao -> pausa de novo (pendente 3)
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": argumentos(4)}),
    ]))
    status, _, pendente = agent_mod.executar_agente(
        "aplica um patch", _config(max_erros_consecutivos=3), retomar=json.loads(json.dumps(pendente)),
    )
    assert status == "needs_user"

    # retomada 3: executa a 3a tentativa (falha, erros_consecutivos=3) --
    # o circuit breaker tem que disparar IMEDIATAMENTE apos essa execucao,
    # sem nem precisar chamar a LLM de novo pra decidir o proximo passo
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([]))
    status, texto, pendente = agent_mod.executar_agente(
        "aplica um patch", _config(max_erros_consecutivos=3), retomar=json.loads(json.dumps(pendente)),
    )

    assert status == "needs_user"
    assert "3 erro" in texto


# ---------------------------------------------------------------------------
# 10) Atualizacao 17 -- verificador exige executado=True E ok=True, nao
#     so ok=True (buraco real na Atualizacao 10, achado em auditoria)
# ---------------------------------------------------------------------------

def test_final_recusado_quando_run_tests_nao_executou_de_verdade(monkeypatch):
    """run_tests com {"executado": False, "ok": True} (testes desligados
    ou nao configurados no projeto) NAO pode satisfazer o verificador --
    e' o mesmo caso de nao ter rodado run_tests nenhuma."""
    monkeypatch.setattr(agent_mod, "TOOLS", {
        "apply_patch": {"permission": "WRITE"},
        "run_tests": {"permission": "EXEC"},
    })

    def fake_executar_tool(nome, arguments, ctx):
        if nome == "run_tests":
            return {"status": "skipped", "ok": True, "executed": False, "changed": False,
                    "error_code": None, "detail": "testes desligados no config"}
        return {"status": "success", "ok": True, "executed": True, "changed": True,
                "error_code": None, "detail": "patch aplicado"}

    monkeypatch.setattr(agent_mod, "executar_tool", fake_executar_tool)

    argumentos = {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 2, "codigo_novo": "pass"}
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": argumentos}),
    ]))
    status, _, pendente = agent_mod.executar_agente("corrige o bug", _config())
    assert status == "needs_user"

    # run_tests roda mas com executado=False -- final tem que ser
    # recusado igual ao caso de nao ter rodado run_tests nenhuma;
    # so fecha quando o proprio needs_user explicito da LLM aparece.
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "run_tests", "arguments": {}}),
        '{"final": "terminei mesmo com testes desligados"}',
        '{"needs_user": "os testes deste projeto estao desligados, nao consigo verificar a escrita"}',
    ]))
    status2, texto2, pendente2 = agent_mod.executar_agente(
        "corrige o bug", _config(),
        retomar=json.loads(json.dumps(pendente)),
    )

    assert status2 == "needs_user"
    assert "desligados" in texto2


# ---------------------------------------------------------------------------
# 11) Atualizacao 21 -- WRITE so conta como escrita quando changed=True
# ---------------------------------------------------------------------------

def test_apply_patch_com_rollback_nao_marca_houve_escrita(monkeypatch):
    """Uma WRITE que falhou e restaurou o arquivo nao pode invalidar o
    verificador como se algo tivesse mudado de verdade."""
    monkeypatch.setattr(agent_mod, "TOOLS", {"apply_patch": {"permission": "WRITE"}})
    monkeypatch.setattr(agent_mod, "executar_tool", lambda *a, **k: {
        "status": "failed", "ok": False, "executed": True, "changed": False,
        "error_code": "PATCH_FAILED", "detail": "falhou e foi revertido",
    })
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": {"arquivo": "a.py"}}),
        '{"final": "nada mudou no projeto"}',
    ]))

    chamadas_registrar_escrita = []
    original = agent_mod.AgentState.registrar_escrita

    def registrar_escrita_espiao(estado):
        chamadas_registrar_escrita.append(True)
        return original(estado)

    monkeypatch.setattr(agent_mod.AgentState, "registrar_escrita", registrar_escrita_espiao)

    status, texto, pendente = agent_mod.executar_agente(
        "tente aplicar", _config(require_confirmation_for_write=False),
    )

    assert status == "success"
    assert texto == "nada mudou no projeto"
    assert chamadas_registrar_escrita == []
    assert pendente is None


def test_exec_tem_gate_proprio_e_retoma_run_tests(monkeypatch):
    monkeypatch.setattr(agent_mod, "TOOLS", {
        "run_tests": {"permission": "EXEC"},
    })
    execucoes = []

    def fake_tool(nome, arguments, ctx):
        execucoes.append(nome)
        return {
            "status": "success", "ok": True, "executed": True,
            "changed": False, "error_code": None, "detail": "ok",
        }

    monkeypatch.setattr(agent_mod, "executar_tool", fake_tool)
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "run_tests", "arguments": {}}),
    ]))

    status, _, pendente = agent_mod.executar_agente(
        "rode os testes", _config(require_confirmation_for_exec=True),
    )
    assert status == "needs_user"
    assert execucoes == []

    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        '{"final": "testes executados"}',
    ]))
    status2, texto2, _ = agent_mod.executar_agente(
        "rode os testes", _config(require_confirmation_for_exec=True),
        retomar=json.loads(json.dumps(pendente)),
    )
    assert status2 == "success"
    assert texto2 == "testes executados"
    assert execucoes == ["run_tests"]


def test_argumento_write_invalido_e_rejeitado_antes_da_confirmacao(monkeypatch):
    """Atualizacao 40: nao pedir ao usuario para confirmar uma chamada
    que o schema ja sabe que e invalida."""
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({"tool": "apply_patch", "arguments": {"arquivo": "a.py"}}),
        '{"final":"a chamada invalida nao foi executada"}',
    ]))

    status, texto, pendente = agent_mod.executar_agente(
        "tente corrigir", _config(max_steps=2, require_confirmation_for_write=True),
    )

    assert status == "success"
    assert "nao foi executada" in texto
    assert pendente is None


def test_alias_write_e_salvo_na_pendencia_em_forma_canonica(monkeypatch):
    monkeypatch.setattr(
        agent_mod.AgentState, "validar_precondicoes_patch",
        lambda self, arguments: (True, "ev-legado"),
    )
    monkeypatch.setattr(agent_mod, "executar_agente_llm", _sequencia_llm([
        json.dumps({
            "tool": "apply_patch",
            "arguments": {
                "arquivo": "a.py",
                "linha_inicio": 1,
                "linha_fim": 1,
                    "codigo_original_esperado": "x = 1",
                    "codigo_novo": "x = 2",
                    "file_hash_esperado": "a" * 64,
                    "range_hash_esperado": "b" * 64,
            },
        }),
    ]))

    status, _, pendente = agent_mod.executar_agente(
        "corrija", _config(require_confirmation_for_write=True),
    )

    assert status == "needs_user"
    argumentos = pendente["tool_pendente"]["arguments"]
    assert argumentos["caminho_relativo"] == "a.py"
    assert "arquivo" not in argumentos


def test_parser_encontra_json_valido_entre_texto_e_outro_objeto():
    bruto = (
        'Vou pensar {"rascunho":true}. Agora a decisao: '
        '{"tool":"list_tree","arguments":{}} fim.'
    )
    assert agent_mod._parse_decisao_agente(bruto) == {
        "tool": "list_tree", "arguments": {},
    }
