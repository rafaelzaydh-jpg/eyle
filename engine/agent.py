#!/usr/bin/env python3
"""
agent.py
--------
Agente minimo da Eyle -- Atualizacoes 1 a 4 (auto-correcao de parsing;
observacoes resumidas + historico limitado; ordem de preferencia de
ferramentas + guarda de chamada repetida; max_steps + loop principal +
rastro de execucao).

Este arquivo nao existia no eyle-base 0.8: e' o "Agente minimo" descrito
em Atualizacao_Agente_Plano_v3_Implementacao.md, que traduz um plano v2
anterior (nao incluido neste pacote) em 4 atualizacoes.

- decidir_passo (Atualizacao 1): um unico passo decide/retry -- chama a
  LLM, tenta parsear a decisao, reforca o prompt e tenta de novo ate
  max_tentativas_parse antes de desistir.
- executar_agente (Atualizacao 4, nesta versao): o loop principal em si
  -- junta decidir_passo com engine/agent_state.py:AgentState (Atualizacao
  2: observacoes ja resumidas; Atualizacao 3: guarda de chamada repetida)
  e engine/compiler.py:montar_prompt_agente, controla max_steps, para em
  needs_user antes de qualquer tool WRITE (se
  config["agent"]["require_confirmation_for_write"]), e grava um rastro
  de depuracao em context/agent_trace.jsonl a cada passo.

Atualizacao 5 (fecha a pendencia registrada acima): engine/agent_tools.py
agora existe -- TOOLS/executar_tool sao importados de la (o bloco
try/except abaixo continua so como rede de seguranca, caso
agent_tools.py falhe ao importar por algum motivo externo). Para as
tools que tocam o codigo real no disco (read_file, find_symbol,
test_patch_dry_run, run_tests, apply_patch) funcionarem, quem chama
executar_agente() precisa passar `projeto` (memory/projeto.json
carregado) -- ver o parametro novo abaixo. Sem `projeto`, o Agente
ainda roda, mas essas tools devolvem {"erro": "nenhum projeto
indexado..."}.

Atualizacao 49 amplia a persistencia: todo `needs_user`, inclusive pergunta
livre e circuit breaker, devolve continuacao serializavel por `task_id`.
Checkpoints antes/depois das tools permitem retomar do passo correto. Uma
WRITE interrompida e inspecionada contra o arquivo final: codigo ja aplicado
nao e escrito de novo e divergencia vira STALE_PATCH.

Atualizacao 10 -- verificador de conclusao objetivo: {"final": ...} so'
e' aceito de primeira se a tarefa nao escreveu nada no projeto. Se
escreveu (tool WRITE, hoje so' apply_patch), so' e' aceito depois que
'run_tests' rodou e devolveu ok=True depois dessa escrita -- caso
contrario o loop devolve um passo extra (observacao pedindo pra rodar
run_tests) em vez de confiar na palavra da LLM. Flag
config["agent"]["exigir_run_tests_apos_escrita"] (default True).

Atualizacao 11 -- circuit breaker de erro consecutivo: alem da guarda
de chamada repetida (Atualizacao 3, que so' pega a MESMA tool+argumentos
de novo), agora conta QUALQUER erro de tool em sequencia -- se passar de
config["agent"]["max_erros_consecutivos"] (default 3), o loop para em
needs_user em vez de deixar a LLM continuar tentando variacoes quebradas
do mesmo passo.

Atualizacao 12 -- fatos_importantes: a decisao da LLM (tool_call, final
ou needs_user) pode incluir opcionalmente uma chave "fato_importante";
diferente das observacoes normais (cortadas por max_entradas no
prompt), fatos_importantes sempre entra inteiro no proximo prompt --
ver engine/agent_state.py e engine/compiler.py:montar_prompt_agente.

Atualizacao 21 -- contrato unico de tools: o loop so chama
`registrar_escrita()` quando o resultado padrao informa
`changed=True`. Uma tool WRITE apenas confirmada ou executada nao conta
mais como escrita se falhou/fez rollback e deixou o arquivo intacto.

Atualizacoes 42-43 -- contexto virtual e grounding: leituras reais viram
evidencias persistentes por ID/faixa/hash; cada prompt recebe somente a selecao
que cabe em `llm.context_window_tokens`. Tarefas de projeto recusam `final`
ate validar pelo menos uma evidencia fresca e qualquer citacao de linha contra
as faixas declaradas.
"""
import json
import os
import re
import sys
import time
import uuid

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from llm.executar import (  # noqa: E402
    PROMPT_AGENTE,
    executar_agente as executar_agente_llm,
)
from engine.agent_state import AgentState, GoalState  # noqa: E402
from engine.text_hash import hash_texto, normalizar_quebras  # noqa: E402
from engine.compiler import montar_prompt_agente  # noqa: E402
from engine.grounding import (  # noqa: E402
    build_safe_grounded_answer,
    format_grounding_feedback,
    verify_conclusion,
)
from engine.project_reader import ErroLeituraProjeto, ler_faixa_projeto  # noqa: E402
from engine.retencao import rotacionar_arquivo  # noqa: E402
from engine.roteador import classificar_modo_projeto  # noqa: E402
from engine.seguranca import _resolver_caminho_seguro  # noqa: E402
from engine import telemetry  # noqa: E402
from engine import progress as job_progress  # noqa: E402

try:
    from engine.agent_tools import (  # noqa: E402
        TOOLS,
        executar_tool,
        gerar_catalogo_tools,
        reverter_patch_confirmado,
        validar_chamada_tool,
    )
except ImportError:
    # Atualizacao 5: engine/agent_tools.py ja existe e e' importado
    # normalmente na linha acima. Este except so continua como rede de
    # seguranca (ex: pacote corrompido/arquivo removido em algum
    # deployment) -- nao e' mais o caminho esperado.
    TOOLS = {}

    def executar_tool(nome, arguments, ctx):
        return {
            "status": "failed", "ok": False, "executed": False,
            "changed": False, "error_code": "TOOL_REGISTRY_UNAVAILABLE",
            "detail": f"tool '{nome}' indisponivel: engine/agent_tools.py nao pode ser importado",
        }

    def gerar_catalogo_tools(registro=None, config=None):
        return []

    def validar_chamada_tool(nome, arguments, registro=None):
        if not isinstance(arguments, dict):
            return None, executar_tool(nome, arguments, {})
        return dict(arguments), None

    def reverter_patch_confirmado(snapshot, ctx):
        return {
            "ok": False, "changed": True, "error_code": "TOOL_REGISTRY_UNAVAILABLE",
            "detalhe": "rollback indisponivel: registro de tools nao carregou",
        }


_RE_JSON_BLOCO = re.compile(r"\{.*\}", re.DOTALL)

_ROTULOS_TOOL_PROGRESSO = {
    "list_tree": "Lendo a estrutura do projeto",
    "search_code": "Procurando codigo relevante",
    "read_range": "Lendo um trecho de codigo",
    "read_file": "Lendo um arquivo do projeto",
    "find_symbol": "Localizando um simbolo no codigo",
    "test_patch_dry_run": "Testando a alteracao em uma copia",
    "apply_patch": "Aplicando a alteracao confirmada",
    "run_tests": "Executando os testes do projeto",
}

def _publicar_tool(config, tool, step=None, concluida=False):
    rotulo = _ROTULOS_TOOL_PROGRESSO.get(tool, f"Executando {tool}")
    if concluida:
        rotulo = rotulo + " concluido; avaliando o resultado"
    job_progress.publicar(
        config, "tool_result" if concluida else "tool", rotulo,
        tool=tool, step=step,
    )

_RE_INTENCAO_ESCRITA = re.compile(
    r"\b(implement|alter|corrig|consert|edit|modific|cri|adicion|remov|apag|"
    r"substitu|atualiz|refator|patch|fix)\w*\b",
    re.IGNORECASE,
)


def _decisao_estruturalmente_valida(dados):
    """Valida o envelope antes de escolher um objeto entre varios JSONs."""
    if not isinstance(dados, dict):
        return False
    ramos = [chave for chave in ("tool", "final", "needs_user") if chave in dados]
    if len(ramos) != 1:
        return False
    ramo = ramos[0]
    if ramo == "tool":
        return (
            isinstance(dados.get("tool"), str)
            and bool(dados["tool"].strip())
            and isinstance(dados.get("arguments"), dict)
        )
    if ramo == "needs_user":
        return isinstance(dados.get("needs_user"), str) and bool(dados["needs_user"].strip())
    final = dados.get("final")
    if isinstance(final, str):
        return bool(final.strip())
    if not isinstance(final, dict):
        return False
    resposta = final.get("answer", final.get("resposta"))
    return isinstance(resposta, str) and bool(resposta.strip())


def _limite_runtime(config):
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    deadline = runtime.get("deadline_monotonic")
    if deadline is not None and time.monotonic() >= float(deadline):
        return "TASK_DEADLINE_EXCEEDED"
    max_chamadas = runtime.get("max_llm_calls")
    if max_chamadas is not None and int(runtime.get("llm_calls", 0)) >= int(max_chamadas):
        return "MAX_LLM_CALLS_EXCEEDED"
    max_tokens = runtime.get("max_generated_tokens")
    if max_tokens is not None and int(runtime.get("generated_tokens", 0)) >= int(max_tokens):
        return "MAX_GENERATED_TOKENS_EXCEEDED"
    return None


def _consumir_chamada_runtime(config, resposta=None):
    runtime = (config or {}).get("_runtime_agent_budget")
    if not isinstance(runtime, dict):
        return
    if resposta is None:
        runtime["llm_calls"] = int(runtime.get("llm_calls", 0)) + 1
        return
    chars_por_token = max(
        1, int((config or {}).get("context_engine", {}).get("chars_per_token_fallback", 3)),
    )
    estimativa = (len(str(resposta)) + chars_por_token - 1) // chars_por_token
    runtime["generated_tokens"] = int(runtime.get("generated_tokens", 0)) + estimativa
_RE_CITACAO_CODIGO = re.compile(
    r"(?P<arquivo>[\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|yml|yaml))"
    r":(?P<inicio>\d+)(?:-(?P<fim>\d+))?",
    re.IGNORECASE,
)


def classificar_tarefa_agente(objetivo, projeto=None, modo=None):
    """Classifica o gate da Atualizacao 43 sem depender da opiniao da LLM."""
    caminho_projeto = (projeto or {}).get("caminho_origem")
    if not caminho_projeto:
        return "chat"
    modo = modo or classificar_modo_projeto(objetivo)
    if modo == "edit" or _RE_INTENCAO_ESCRITA.search(objetivo or ""):
        return "project_write"
    return "project_read"


def _atualizar_frescor_evidencias(estado, projeto):
    """Rele faixas e invalida hashes que mudaram fora/dentro do loop."""
    caminho_projeto = (projeto or {}).get("caminho_origem")
    if not caminho_projeto:
        return []
    invalidadas = []
    for evidencia in estado.evidencias_frescas():
        try:
            leitura = ler_faixa_projeto(
                caminho_projeto,
                evidencia.get("arquivo"),
                evidencia.get("linha_inicio"),
                evidencia.get("linha_fim"),
                max_linhas=max(
                    evidencia.get("linha_fim", 0) - evidencia.get("linha_inicio", 1) + 1,
                    1,
                ),
            )
        except (ErroLeituraProjeto, TypeError, ValueError):
            evidencia["estado"] = "stale"
            estado.liberar_releitura(evidencia)
            invalidadas.append(evidencia.get("id"))
            continue
        if (
            leitura.get("linha_inicio") != evidencia.get("linha_inicio")
            or leitura.get("linha_fim") != evidencia.get("linha_fim")
            or leitura.get("content_hash") != evidencia.get("content_hash")
            or (
                evidencia.get("file_hash") is not None
                and leitura.get("file_hash") != evidencia.get("file_hash")
            )
        ):
            evidencia["estado"] = "stale"
            estado.liberar_releitura(evidencia)
            invalidadas.append(evidencia.get("id"))
    if invalidadas:
        necessarias = estado.goal_state.setdefault("evidence_needed", [])
        if "codigo_fresco_relevante" not in necessarias:
            necessarias.append("codigo_fresco_relevante")
        GoalState.replanejar(
            estado.goal_state, "file_changed",
            f"evidencias {invalidadas} mudaram no disco",
        )
    return invalidadas


def _normalizar_conclusao(decisao, estado, task_type):
    """Converte o JSON da LLM no contrato interno da Atualizacao 43."""
    valor = decisao.get("final")
    if isinstance(valor, dict):
        # English is the canonical model protocol; Portuguese keys remain
        # accepted for checkpoints and older model outputs.
        resposta = valor.get("answer", valor.get("resposta"))
        evidence_ids = valor.get("evidence_ids")
        verificacao = valor.get("verification", valor.get("verificacao"))
        limitacoes = valor.get("limitations", valor.get("limitacoes", []))
        claim_annotations = valor.get(
            "claim_annotations", valor.get("anotacoes_afirmacoes", [])
        )
    else:
        resposta = valor
        evidence_ids = decisao.get("evidence_ids")
        verificacao = decisao.get("verification", decisao.get("verificacao"))
        limitacoes = decisao.get("limitations", decisao.get("limitacoes", []))
        claim_annotations = decisao.get(
            "claim_annotations", decisao.get("anotacoes_afirmacoes", [])
        )

    if not isinstance(resposta, str) or not resposta.strip():
        return None, "o campo final.resposta precisa ser texto nao vazio"
    if not isinstance(limitacoes, list) or not all(isinstance(item, str) for item in limitacoes):
        return None, "final.limitacoes precisa ser uma lista de textos"
    if claim_annotations is None:
        claim_annotations = []
    if not isinstance(claim_annotations, list) or not all(
        isinstance(item, dict) for item in claim_annotations
    ):
        return None, "final.claim_annotations precisa ser uma lista de objetos"

    # Compatibilidade com respostas antigas: depois de uma leitura real,
    # uma string final e normalizada internamente com todas as evidencias
    # frescas. O prompt novo pede IDs explicitos; o gate continua objetivo.
    if evidence_ids is None and task_type != "chat":
        evidence_ids = [item.get("id") for item in estado.evidencias_frescas()]
    if evidence_ids is None:
        evidence_ids = []
    if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
        return None, "final.evidence_ids precisa ser uma lista de IDs"

    return {
        "resposta": resposta.strip(),
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "verificacao": verificacao,
        "limitacoes": limitacoes,
        "claim_annotations": claim_annotations,
        "task_type": task_type,
    }, None


def _validar_conclusao_projeto(conclusao, estado, projeto):
    """Valida presenca, faixa e hash fresco de cada evidencia declarada."""
    _atualizar_frescor_evidencias(estado, projeto)
    ids = conclusao.get("evidence_ids") or []
    if not ids:
        return False, "nenhuma evidence_id fresca de codigo real foi informada", []

    usadas = []
    erros = []
    for evidence_id in ids:
        evidencia = estado.evidencia_por_id(evidence_id)
        if evidencia is None:
            erros.append(f"evidence_id inexistente: {evidence_id}")
            continue
        if evidencia.get("estado") != "fresh":
            erros.append(f"evidencia stale: {evidence_id}")
            continue
        campos_validos = (
            isinstance(evidencia.get("arquivo"), str)
            and isinstance(evidencia.get("linha_inicio"), int)
            and isinstance(evidencia.get("linha_fim"), int)
            and evidencia.get("linha_fim") >= evidencia.get("linha_inicio")
            and isinstance(evidencia.get("content_hash"), str)
            and bool(evidencia.get("conteudo"))
        )
        if not campos_validos:
            erros.append(f"evidencia estruturalmente invalida: {evidence_id}")
            continue
        usadas.append(evidencia)

    if erros:
        return False, "; ".join(erros), usadas

    for citacao in _RE_CITACAO_CODIGO.finditer(conclusao.get("resposta") or ""):
        arquivo = citacao.group("arquivo").replace("\\", "/")
        inicio = int(citacao.group("inicio"))
        fim = int(citacao.group("fim") or inicio)
        cobre = any(
            (
                item.get("arquivo") == arquivo
                or str(item.get("arquivo") or "").rsplit("/", 1)[-1] == arquivo.rsplit("/", 1)[-1]
            )
            and item.get("linha_inicio") <= inicio <= fim <= item.get("linha_fim")
            for item in usadas
        )
        if not cobre:
            return (
                False,
                f"citacao fora das faixas das evidencias usadas: {arquivo}:{inicio}-{fim}",
                usadas,
            )
    return True, "arquivo, faixa e hash das evidencias foram conferidos no disco", usadas


def _resumo_confirmacao_patch(arguments, entendimento):
    arquivo = arguments.get("caminho_relativo")
    impactados = []
    for caminho, info in ((entendimento or {}).get("arquivos") or {}).items():
        if arquivo in (info.get("depende_de") or []):
            impactados.append(caminho)
    codigo_novo = arguments.get("codigo_novo") or ""
    linhas_novas = len(codigo_novo.split("\n"))
    sufixo_impacto = (
        ", dependentes mapeados: " + ", ".join(impactados[:3])
        if impactados else ", nenhum dependente mapeado no entendimento atual"
    )
    return (
        f"{arquivo}:{arguments.get('linha_inicio')}-{arguments.get('linha_fim')} "
        f"sera substituido por {linhas_novas} linha(s); "
        f"hash arquivo={str(arguments.get('file_hash_esperado') or '')[:12]}, "
        f"hash faixa={str(arguments.get('range_hash_esperado') or '')[:12]}"
        f"{sufixo_impacto}. Dry-run aprovado."
    )


def _resultado_pos_testes(estado, resultado_tool, projeto):
    """Aplica rollback automatico quando a verificacao real falha."""
    if not estado.edit_state or resultado_tool.get("ok") is not False:
        return None
    snapshot = estado.edit_state.get("rollback_snapshot")
    rollback = reverter_patch_confirmado(
        snapshot,
        {"projeto": projeto},
    )
    estado.registrar_rollback(rollback)
    arquivo = estado.edit_state.get("arquivo")
    estado.marcar_evidencias_stale(arquivo)
    if rollback.get("ok") is True:
        return (
            "needs_user",
            "A verificacao falhou e a alteracao foi revertida atomicamente. "
            f"Teste executado: {resultado_tool.get('executed') is True}. "
            f"Detalhe: {resultado_tool.get('detail')}",
        )
    return (
        "needs_user",
        "A verificacao falhou e o rollback automatico tambem falhou; a tarefa foi bloqueada. "
        f"Detalhe do rollback: {rollback.get('detalhe')}",
    )


def _argumentos_decisao(valor):
    """Converte argumentos comuns de backends locais para ``dict``.

    Alguns templates OpenAI-compatible devolvem ``arguments`` como JSON em
    string, mesmo quando o prompt pediu um objeto. A conversao e deliberadamente
    estreita: somente um objeto JSON valido e aceito; texto livre continua
    rejeitado.
    """
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        try:
            convertido = json.loads(valor)
        except json.JSONDecodeError:
            return None
        return convertido if isinstance(convertido, dict) else None
    return None


def _normalizar_decisao_agente(dados):
    """Normaliza envelopes JSON comuns sem afrouxar o gate de seguranca.

    Modelos locais variam bastante no nome do envelope mesmo sob JSON mode.
    Aceitamos apenas aliases mecanicos e inequivocos para os tres ramos do
    protocolo. Objetos que misturam tool/final/needs_user continuam recusados.
    """
    if _decisao_estruturalmente_valida(dados):
        return dados
    if not isinstance(dados, dict):
        return None

    # Nunca escolha silenciosamente um ramo quando o modelo misturou ramos
    # canonicos. Isso preserva a exclusividade do protocolo original.
    ramos_canonicos = [
        chave for chave in ("tool", "final", "needs_user") if chave in dados
    ]
    if len(ramos_canonicos) > 1:
        return None

    # OpenAI/native-tool-like envelope serializado no content.
    tool_calls = dados.get("tool_calls")
    if isinstance(tool_calls, list) and len(tool_calls) == 1:
        chamada = tool_calls[0]
        if isinstance(chamada, dict):
            funcao = chamada.get("function", chamada)
            if isinstance(funcao, dict):
                nome = funcao.get("name")
                argumentos = _argumentos_decisao(
                    funcao.get("arguments", funcao.get("parameters", {}))
                )
                candidata = {"tool": nome, "arguments": argumentos}
                if _decisao_estruturalmente_valida(candidata):
                    return candidata

    # Envelopes comuns: {"tool_call": {...}} ou {"function": {...}}.
    for chave in ("tool_call", "function"):
        chamada = dados.get(chave)
        if not isinstance(chamada, dict):
            continue
        nome = chamada.get("name", chamada.get("tool"))
        argumentos = _argumentos_decisao(
            chamada.get("arguments", chamada.get("parameters", chamada.get("args", {})))
        )
        candidata = {"tool": nome, "arguments": argumentos}
        if _decisao_estruturalmente_valida(candidata):
            return candidata

    # Aliases planos usados por modelos ReAct e templates simples.
    nome = dados.get("name", dados.get("action"))
    if isinstance(nome, str) and nome.strip():
        argumentos = _argumentos_decisao(
            dados.get(
                "arguments",
                dados.get("parameters", dados.get("args", dados.get("action_input", {}))),
            )
        )
        candidata = {"tool": nome, "arguments": argumentos}
        if _decisao_estruturalmente_valida(candidata):
            return candidata

    # Final textual com nomes alternativos. O gate de evidencias do Agente
    # ainda decide se essa conclusao pode ser aceita para uma tarefa de projeto.
    for chave in ("answer", "resposta", "response", "result", "output"):
        valor = dados.get(chave)
        if isinstance(valor, str) and valor.strip():
            candidata = {"final": valor}
            if _decisao_estruturalmente_valida(candidata):
                return candidata

    pergunta = dados.get("question", dados.get("ask_user"))
    if isinstance(pergunta, str) and pergunta.strip():
        candidata = {"needs_user": pergunta}
        if _decisao_estruturalmente_valida(candidata):
            return candidata
    return None


def _parse_decisao_agente(texto):
    """Extrai a decisao JSON final reconhecivel da resposta da LLM.

    O decoder incremental ignora objetos auxiliares invalidos e percorre toda
    a resposta. Quando um backend sem gramatica devolve mais de uma decisao
    valida, a ultima e escolhida: modelos locais costumam emitir um rascunho e
    depois se autocorrigir no fim. Envelopes JSON equivalentes usados por
    templates locais sao normalizados antes da validacao final.

    A validacao estrutural continua exigindo exatamente um ramo por objeto;
    portanto um unico envelope contendo ``tool`` e ``final`` segue rejeitado.
    """
    bruto = str(texto or "")
    decoder = json.JSONDecoder()
    candidatas = []
    intervalos_canonicos = []
    for match in re.finditer(r"\{", bruto):
        inicio = match.start()
        try:
            dados, fim_relativo = decoder.raw_decode(bruto[inicio:])
        except json.JSONDecodeError:
            continue
        fim = inicio + fim_relativo
        canonica = _decisao_estruturalmente_valida(dados)
        normalizada = dados if canonica else _normalizar_decisao_agente(dados)
        if normalizada is not None:
            candidatas.append({
                "inicio": inicio,
                "fim": fim,
                "canonica": canonica,
                "decisao": normalizada,
            })
            if canonica:
                intervalos_canonicos.append((inicio, fim))

    # Nao trate um objeto interno (ex.: o dict dentro de ``final``) como uma
    # segunda decisao alias. Ele pertence ao envelope canonico externo.
    filtradas = []
    for item in candidatas:
        if not item["canonica"] and any(
            inicio <= item["inicio"] and item["fim"] <= fim
            for inicio, fim in intervalos_canonicos
        ):
            continue
        filtradas.append(item)

    if len(filtradas) > 1:
        telemetry.record(
            "internal", "agent_json_parse", "multiple_valid_last_selected",
            metadata={"valid_objects": len(filtradas)},
        )
    return filtradas[-1]["decisao"] if filtradas else None


def prompt_reforco_formato(prompt, resposta_llm):
    """
    Concatena um aviso curto de formato invalido ao prompt original, pra
    reenviar na proxima tentativa. So concatena -- sem logica extra, sem
    tentar "consertar" a resposta anterior.
    """
    aviso = (
        "\n\n[FORMAT ERROR] Your previous response was not valid agent JSON. "
        "Return ONLY one allowed JSON object, with no text before or after it: "
        "{\"tool\":\"...\",\"arguments\":{...}} or "
        "{\"final\":{\"answer\":\"...\",\"evidence_ids\":[],"
        "\"verification\":\"...\",\"limitations\":[],\"claim_annotations\":[]}} or "
        "{\"final\":\"...\"} or {\"needs_user\":\"...\"}. "
        "Previous response (possibly truncated): "
        f"{(resposta_llm or '').strip()[:300]}"
    )
    return prompt + aviso


def decidir_passo(prompt_usuario, config):
    """
    Executa o loop de retry de parsing de um unico passo do Agente:

        while decisao is None and tentativa < max_tentativas_parse:
            chama a LLM, tenta parsear
            se falhar, reforca o prompt e tenta de novo

    Devolve um dict com:
      - "decisao": o JSON parseado (dict) ou None se esgotou as tentativas
      - "tentativas": quantas chamadas de LLM foram feitas
      - "falhou": True se nao conseguiu parsear apos max_tentativas_parse
      - "resposta_bruta": a ultima resposta crua da LLM (util pra log/trace)

    Quem chama esta funcao (o loop principal do Agente, Atualizacao 4) decide
    o que fazer quando "falhou" for True -- aqui so' garantimos que nao se
    morre na primeira resposta mal formatada.
    """
    cfg_agente = config.get("agent", {})
    max_tentativas = cfg_agente.get("max_tentativas_parse", 2)

    prompt_atual = prompt_usuario
    tentativa = 0
    decisao = None
    resposta_bruta = ""

    while decisao is None and tentativa < max_tentativas:
        limite_runtime = _limite_runtime(config)
        if limite_runtime:
            return {
                "decisao": None,
                "tentativas": tentativa,
                "falhou": True,
                "resposta_bruta": resposta_bruta,
                "budget_exhausted": limite_runtime,
            }
        tentativa += 1
        resposta_bruta = executar_agente_llm(prompt_atual, config)
        decisao = _parse_decisao_agente(resposta_bruta)
        if decisao is None and tentativa < max_tentativas:
            prompt_atual = prompt_reforco_formato(prompt_atual, resposta_bruta)

    return {
        "decisao": decisao,
        "tentativas": tentativa,
        "falhou": decisao is None,
        "resposta_bruta": resposta_bruta,
    }


# ---------------------------------------------------------------------------
# Atualizacao 4 -- rastro de execucao (context/agent_trace.jsonl).
#
# Efemero: sobrescrito (truncado) no INICIO de cada chamada a
# executar_agente, nunca acumula entre tarefas -- mesmo padrao de
# context/atual.json. Serve so pra depuracao (ver exatamente a sequencia
# de passos/decisoes de uma tarefa), nao e' memoria de longo prazo.
# ---------------------------------------------------------------------------

_CONTEXT_DIR = os.path.join(BASE_DIR, "context")
_TRACE_PATH = os.path.join(_CONTEXT_DIR, "agent_trace.jsonl")


def _iniciar_trace(config=None):
    """Trunca context/agent_trace.jsonl no inicio de uma nova tarefa."""
    try:
        os.makedirs(_CONTEXT_DIR, exist_ok=True)
        max_files = (config or {}).get("retention", {}).get("trace_max_files", 5)
        rotacionar_arquivo(_TRACE_PATH, max_files=max_files)
        with open(_TRACE_PATH, "w", encoding="utf-8"):
            pass
    except OSError as erro:
        telemetry.record("internal", "agent_trace_init", "failed", metadata={"detail": str(erro)[:300]})


def _continuar_trace():
    """Fase 3: quando a tarefa esta sendo RETOMADA (nao e' nova), garante
    so que o diretorio existe -- NAO trunca context/agent_trace.jsonl,
    porque e' a mesma tarefa que pausou, e o rastro dela ate aqui ainda e'
    util pra depuracao."""
    try:
        os.makedirs(_CONTEXT_DIR, exist_ok=True)
    except OSError as erro:
        telemetry.record("internal", "agent_trace_continue", "failed", metadata={"detail": str(erro)[:300]})


def _registrar_trace(entrada):
    """Acrescenta uma linha JSON ao rastro da tarefa atual. Nunca levanta
    excecao -- o trace e' so visibilidade de depuracao, uma falha de
    escrita aqui nao deve derrubar o loop do Agente."""
    try:
        os.makedirs(_CONTEXT_DIR, exist_ok=True)
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except OSError as erro:
        telemetry.record("internal", "agent_trace_write", "failed", metadata={"detail": str(erro)[:300]})


def _registrar_trace_estado(estado, entrada):
    """Todo evento explica objetivo, passo e pendencias (Atualizacao 45)."""
    goal = estado.goal_state if estado is not None else {}
    enriquecida = dict(entrada)
    enriquecida["goal_state"] = {
        "objective": goal.get("objective"),
        "mode": goal.get("mode"),
        "current_step": goal.get("current_step"),
        "actions_executed": goal.get("actions_executed", 0),
        "blockers": list(goal.get("blockers") or []),
        "evidence_needed": list(goal.get("evidence_needed") or []),
        "status": goal.get("status"),
    }
    _registrar_trace(enriquecida)


def _sem_progresso(estado, limite, step, tipo, motivo):
    """Guarda separada: max_steps continua contando somente ações reais."""
    total = estado.registrar_sem_progresso()
    _registrar_trace_estado(estado, {
        "step": step,
        "tipo": tipo,
        "motivo": motivo,
        "decisoes_sem_progresso": total,
    })
    return total >= limite


def _retorno_agente_base(status, texto, estado_pendente, detalhes, retornar_detalhes):
    if retornar_detalhes:
        return status, texto, estado_pendente, detalhes
    return status, texto, estado_pendente


def _edit_state_publico(estado):
    return {
        chave: valor for chave, valor in (estado.edit_state or {}).items()
        if chave != "rollback_snapshot"
    }


def _inspecionar_write_pendente(arguments, projeto):
    """Distingue escrita ainda nao aplicada, ja aplicada e estado divergente.

    E usada somente na recuperacao. Uma faixa que ja contem ``codigo_novo``
    nunca e escrita de novo; uma faixa/hash divergente falha fechado.
    """
    arguments = arguments or {}
    raiz = (projeto or {}).get("caminho_origem")
    relativo = arguments.get("caminho_relativo")
    if not raiz or not relativo or not arguments.get("file_hash_esperado"):
        # Estados legados/mocks nao possuem hashes suficientes para a
        # inspecao 49; a propria tool continua sendo a fonte de verdade.
        return "not_applied", {"detail": "inspecao de recuperacao indisponivel"}
    caminho = _resolver_caminho_seguro(raiz, relativo)
    if caminho is None or not os.path.isfile(caminho):
        return "stale", {"detail": "arquivo da escrita pendente nao existe mais"}
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as arquivo:
            conteudo = normalizar_quebras(arquivo.read())
    except OSError as erro:
        return "stale", {"detail": f"nao foi possivel reler o arquivo: {erro}"}

    hash_atual = hash_texto(conteudo)
    if hash_atual == arguments.get("file_hash_esperado"):
        return "not_applied", {"file_hash_atual": hash_atual}

    try:
        inicio = int(arguments.get("linha_inicio"))
    except (TypeError, ValueError):
        return "stale", {"file_hash_atual": hash_atual, "detail": "linha inicial invalida"}
    novas = str(arguments.get("codigo_novo") or "").split("\n")
    linhas = conteudo.split("\n")
    trecho_atual = "\n".join(linhas[inicio - 1:inicio - 1 + len(novas)])
    codigo_novo = str(arguments.get("codigo_novo") or "")
    if codigo_novo and trecho_atual == codigo_novo:
        return "already_applied", {
            "file_hash_atual": hash_atual,
            "linha_fim_final": inicio + len(novas) - 1,
        }
    return "stale", {
        "file_hash_atual": hash_atual,
        "detail": "o arquivo nao corresponde nem ao estado anterior nem ao patch ja aplicado",
    }


def _needs_user_antes_de_leitura(estado, task_type):
    """Impede a LLM de alegar falta de contexto sem tentar ler o projeto.

    ``needs_user`` continua valido para chat, confirmacoes geradas pelo sistema
    e bloqueios reais de ferramenta. Em tarefas de projeto, porem, a primeira
    resposta nao pode transferir ao usuario um trabalho que as tools READ
    conseguem fazer. Uma falha real de tool libera a pergunta.
    """
    if task_type not in ("project_read", "project_write"):
        return False
    # Evidencia stale prova que houve leitura e que o bloqueio nasceu de uma
    # mudanca real no disco; nesse caso a pausa existente da retomada continua
    # valida. O bug corrigido aqui e a fuga com zero tentativa de codigo.
    if estado.evidence:
        return False
    for acao in estado.actions:
        if not acao.get("action_number"):
            continue
        if acao.get("ok") is False or acao.get("error_code"):
            return False
    return True


def _analise_geral_ainda_precisa_list_tree(estado):
    """Diz se o primeiro passo deterministico da analise geral esta pendente."""
    if estado.acoes_executadas != 0:
        return False
    plano = estado.goal_state.get("plan") or []
    if not plano or not isinstance(plano[0], dict):
        return False
    return "list_tree" in str(plano[0].get("description") or "")


def _acao_obrigatoria_goal_state(estado):
    """Executa transicoes que o proprio GoalState ja tornou obrigatorias.

    Isto nao e um atalho por tamanho de projeto. A mesma regra vale para
    qualquer analise geral: o plano e o validador exigem ``list_tree`` como
    primeira acao, portanto nao faz sentido gastar uma chamada LLM apenas
    para ela repetir uma decisao que o sistema ja conhece.
    """
    if _analise_geral_ainda_precisa_list_tree(estado):
        return {"tool": "list_tree", "arguments": {}}
    return None


def _arquivos_explicitos_objetivo(objetivo):
    """Extrai caminhos de arquivo citados literalmente, preservando a ordem."""
    encontrados = re.findall(
        r"(?<![\w./\-])([\w./\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|yml|yaml))\b",
        str(objetivo or ""),
        re.IGNORECASE,
    )
    return list(dict.fromkeys(item.replace("\\", "/") for item in encontrados))


def _simbolo_explicito_objetivo(objetivo):
    match = re.search(
        r"\b(?:fun[cç][aã]o|s[ií]mbolo)\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)",
        str(objetivo or ""),
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _proxima_leitura_explicita(objetivo, estado):
    """Escolhe um arquivo literal ainda nao tentado para sair de um falso bloqueio."""
    arquivos = _arquivos_explicitos_objetivo(objetivo)
    tentados = set()
    for acao in estado.actions:
        if acao.get("tool") not in ("read_file", "read_range"):
            continue
        argumentos = acao.get("arguments") or {}
        caminho = argumentos.get("caminho_relativo")
        if caminho:
            tentados.add(str(caminho).replace("\\", "/"))
    for arquivo in arquivos:
        if arquivo not in tentados:
            return {"tool": "read_file", "arguments": {"caminho_relativo": arquivo}}
    return None


def _completar_argumentos_obvios(tool, arguments, objetivo):
    """Preenche apenas dados literais inequívocos presentes no objetivo."""
    argumentos = dict(arguments or {}) if isinstance(arguments, dict) else arguments
    if not isinstance(argumentos, dict):
        return argumentos
    arquivos = _arquivos_explicitos_objetivo(objetivo)
    if tool in ("read_file", "read_range", "read_metadata", "find_symbol"):
        if not argumentos.get("caminho_relativo") and len(arquivos) == 1:
            argumentos["caminho_relativo"] = arquivos[0]
    if tool == "find_symbol" and not argumentos.get("simbolo"):
        simbolo = _simbolo_explicito_objetivo(objetivo)
        if simbolo:
            argumentos["simbolo"] = simbolo
    if tool == "search_code" and not argumentos.get("pergunta"):
        argumentos["pergunta"] = str(objetivo or "").strip()
    return argumentos


def _acao_recuperacao_deterministica(objetivo, estado):
    """Escolhe uma leitura obvia depois de uma decisao de tool invalida.

    Modelos locais pequenos as vezes erram o nome da tool ou esquecem um
    argumento tres vezes seguidas. Quando arquivo/simbolo estao literais no
    pedido, o sistema nao precisa transferir esse erro ao usuario.
    """
    arquivos = _arquivos_explicitos_objetivo(objetivo)
    simbolo = _simbolo_explicito_objetivo(objetivo)
    if len(arquivos) == 1 and simbolo:
        decisao = {
            "tool": "find_symbol",
            "arguments": {"caminho_relativo": arquivos[0], "simbolo": simbolo},
        }
        if not estado.chamada_repetida(decisao["tool"], decisao["arguments"]):
            return decisao
    for arquivo in arquivos:
        decisao = {"tool": "read_file", "arguments": {"caminho_relativo": arquivo}}
        if not estado.chamada_repetida(decisao["tool"], decisao["arguments"]):
            return decisao
    return None


def _deve_recuperar_sem_llm(estado):
    if (estado.goal_state or {}).get("replan_reason") == "file_changed":
        return True
    if not estado.actions:
        return False
    ultima = estado.actions[-1]
    return (
        not ultima.get("action_number")
        and ultima.get("error_code") in {"TOOL_NOT_FOUND", "INVALID_ARGUMENT"}
    )


def _preparar_recuperacao_stale(estado, arguments, resultado_tool):
    """Transforma STALE_PATCH em releitura/reconfirmacao, nao em beco sem saida."""
    arquivo = (arguments or {}).get("caminho_relativo")
    if arquivo:
        estado.marcar_evidencias_stale(arquivo)
    estado.goal_state["status"] = "in_progress"
    detalhe = (resultado_tool or {}).get("detail")
    if isinstance(detalhe, dict):
        detalhe = detalhe.get("detail") or detalhe.get("message") or str(detalhe)
    mensagem = (
        f"STALE_PATCH recuperavel em {arquivo or 'arquivo'}: {detalhe or 'hash divergente'}. "
        "Releia a faixa, refaca o dry-run e solicite nova confirmacao; nao reutilize a confirmacao antiga."
    )
    estado.observar("stale_patch_recovery", {
        "status": "success", "ok": True, "executed": False,
        "changed": False, "error_code": None, "detail": mensagem,
    })
    return mensagem


def executar_agente(objetivo, config, entendimento=None, projeto=None, retomar=None,
                    retornar_detalhes=False, modo=None, task_id=None,
                    checkpoint=None, resposta_usuario=None):
    """
    Loop principal do Agente minimo (Atualizacao 4; tools de verdade
    conectadas na Atualizacao 5; persistencia geral na Atualizacao 49).

    Junta o que as atualizacoes anteriores prepararam:
      - decidir_passo (Atualizacao 1) decide o proximo passo, com retry
        curto quando a LLM nao devolve JSON valido.
      - AgentState (Atualizacao 2) guarda cada observacao ja resumida
        (nunca resultado cru); montar_prompt_agente manda so as ultimas
        3-4 no proximo prompt.
      - AgentState.chamada_repetida/registrar_chamada (Atualizacao 3)
        barra a mesma (tool, argumentos) de rodar duas vezes na mesma
        tarefa.
      - engine/agent_tools.py:TOOLS/executar_tool (Atualizacao 5) executa
        a tool de verdade -- list_tree, search_code, find_symbol,
        read_range/read_file, read_metadata, test_patch_dry_run, run_tests,
        apply_patch. Desde a Atualizacao 40 o prompt recebe o catalogo
        gerado desse mesmo registro e os argumentos sao validados antes do
        gate/execucao.

    Nesta atualizacao: o loop roda no maximo config["agent"]["max_steps"]
    passos; toda tool marcada como permission="WRITE" no registro de
    ferramentas (engine/agent_tools.py -- hoje so apply_patch) para o
    loop em "needs_user" pedindo confirmacao explicita ANTES de
    executar, se config["agent"]["require_confirmation_for_write"]
    estiver ligado -- nao executa a tool nesse mesmo passo. Cada passo
    grava uma linha em context/agent_trace.jsonl.

    Parametros:
      objetivo: a tarefa em texto livre (o "OBJETIVO" que entra no prompt
                via montar_prompt_agente). Ao retomar, e' o mesmo
                objetivo salvo no checkpoint SQLite (ou no JSON legado) --
                quem chama _retomar_agente_pendente nao precisa pedir o
                objetivo novamente.
      config: config.json carregado (usa config["agent"]).
      entendimento: memory/entendimento.json carregado, opcional -- passa
                    adiante para montar_prompt_agente (bloco de
                    metadados do projeto no prompt) E para as tools
                    (read_metadata le direto daqui, sem funcao propria).
      projeto: memory/projeto.json carregado, opcional -- as tools que
               tocam o codigo real no disco (read_file, find_symbol,
               test_patch_dry_run, run_tests, apply_patch) usam
               projeto["caminho_origem"] pra achar o projeto. Sem isso,
               essas tools devolvem {"erro": "nenhum projeto indexado..."}
               em vez de travar o loop.
      retomar: continuacao SQLite/legada com
               {"estado", "step_atual", "tool_pendente", ...}. Pode
               representar confirmacao, resposta livre ou recuperacao
               interna idempotente. None para tarefa nova.
               Quando presente: reidrata o AgentState via
               AgentState.from_dict(retomar["estado"], config=config),
               executa retomar["tool_pendente"] (ja confirmada -- pula a
               checagem de WRITE/chamada_repetida pra ela), e so ENTAO
               continua o loop normal a partir de retomar["step_atual"].

    Devolve (status, texto, estado_pendente):
      status: "success" | "failed" | "needs_user" | "max_steps"
      texto:  resposta final (success/needs_user) ou um resumo curto do
              motivo (failed/max_steps).
      estado_pendente: continuacao serializavel em todo status
              "needs_user", com objetivo, estado, task_id, orçamento e ação
              pendente real ou marcador de resposta livre.
    """
    # Copia apenas o envelope para anexar limites efemeros sem contaminar a
    # configuracao global compartilhada pelo Flask/Worker.
    config = dict(config or {})
    cfg_agente = config.get("agent", {})
    if not isinstance(config.get("_runtime_agent_budget"), dict):
        deadline_segundos = max(1, int(cfg_agente.get("task_deadline_seconds", 300)))
        config["_runtime_agent_budget"] = {
            "started_monotonic": time.monotonic(),
            "deadline_monotonic": time.monotonic() + deadline_segundos,
            "max_llm_calls": max(1, int(cfg_agente.get("max_llm_calls", 12))),
            "max_generated_tokens": max(
                1, int(cfg_agente.get("max_total_generated_tokens", 12000)),
            ),
            "llm_calls": 0,
            "generated_tokens": 0,
        }
    max_steps = cfg_agente.get("max_steps", 8)
    exigir_confirmacao_write = cfg_agente.get("require_confirmation_for_write", True)
    exigir_confirmacao_exec = cfg_agente.get("require_confirmation_for_exec", False)
    max_erros_consecutivos = cfg_agente.get("max_erros_consecutivos", 3)  # Atualizacao 11
    exigir_testes_apos_escrita = cfg_agente.get("exigir_run_tests_apos_escrita", True)  # Atualizacao 10
    max_sem_progresso = cfg_agente.get("max_no_progress_decisions", 3)
    modos_habilitados = cfg_agente.get("enabled_modes", ["analyze", "suggest"])
    rollout_mode = cfg_agente.get("rollout_mode", "full")
    somente_leitura = rollout_mode != "full"
    modo = modo or classificar_modo_projeto(objetivo)
    if not (projeto or {}).get("caminho_origem"):
        modo = "chat"
    edit_habilitado = "edit" in modos_habilitados and not somente_leitura
    task_type = classificar_tarefa_agente(objetivo, projeto=projeto, modo=modo)
    task_id = str(task_id or (retomar or {}).get("task_id") or uuid.uuid4().hex)

    step = 0
    estado = None

    def _checkpoint(payload):
        if checkpoint is not None:
            checkpoint(dict(payload))

    def _retorno_agente(status, texto, estado_pendente, detalhes, retornar_detalhes_local):
        """Fecha qualquer saida com continuacao e auditoria estruturada."""
        detalhes = dict(detalhes or {})
        estado_serializado = estado.to_dict() if estado is not None else {}
        acoes = list(estado.actions) if estado is not None else []
        evidencias = list(estado.evidence) if estado is not None else []
        codigos_gate = {
            "success": "goal_satisfied",
            "needs_user": "user_input_required",
            "max_steps": "budget_exhausted",
            "failed": "agent_failed",
        }
        completion_gate = {
            "code": codigos_gate.get(status, status),
            "passed": status == "success",
            "requires_user": status == "needs_user",
        }
        bloqueios_sistema = set((estado.goal_state or {}).get("blockers") or [])
        fallback_explicito = detalhes.get("fallback_cause")
        if "rollout_read_only" in bloqueios_sistema:
            completion_gate["code"] = "read_only_write_blocked"
            fallback_cause = "rollout_read_only_write_blocked"
        else:
            fallback_cause = (
                fallback_explicito
                if isinstance(fallback_explicito, str) and fallback_explicito.strip()
                else None if status in ("success", "needs_user") else status
            )
        acoes_read = [
            item for item in acoes
            if item.get("action_number")
            and TOOLS.get(item.get("tool"), {}).get("permission") == "READ"
        ]
        detalhes.update({
            "task_id": task_id,
            "response": texto,
            "tools_called": [
                item.get("tool") for item in acoes
                if item.get("tool") and item.get("action_number")
            ],
            "completion_gate": completion_gate,
            "fallback_cause": fallback_cause,
            "read_status": (
                "read" if any(item.get("estado") == "fresh" for item in evidencias)
                else "read" if any(item.get("ok") is True for item in acoes_read)
                else "read_failed" if acoes_read
                else "not_read"
            ),
        })
        detalhes.setdefault(
            "evidence_ids", [item.get("id") for item in evidencias if item.get("id")],
        )

        if status == "needs_user":
            if estado_pendente is None:
                estado_pendente = {
                    "objetivo": objetivo,
                    "step_atual": estado.acoes_executadas,
                    "estado": estado_serializado,
                    "tool_pendente": {
                        "tool": "__user_response__",
                        "arguments": {},
                        "permission": "READ",
                        "idempotent": True,
                    },
                    "pergunta_ao_usuario": texto,
                    "continuation_kind": "user_input",
                }
            else:
                estado_pendente = dict(estado_pendente)
                estado_pendente.setdefault("estado", estado_serializado)
                estado_pendente.setdefault("continuation_kind", "confirmation")
            if (
                isinstance(retomar, dict)
                and str(retomar.get("task_id") or "") == task_id
            ):
                for chave in (
                    "id", "tipo_pendencia", "criado_em", "expira_em", "projeto_hash",
                ):
                    if retomar.get(chave) is not None:
                        estado_pendente.setdefault(chave, retomar[chave])
            estado_pendente.update({
                "task_id": task_id,
                "modo": modo,
                "task_type": task_type,
                "orcamento_restante": max(0, int(max_steps) - int(estado.acoes_executadas)),
            })
            acao_pendente = dict(estado_pendente.get("tool_pendente") or {})
            nome_pendente = acao_pendente.get("tool")
            if nome_pendente and nome_pendente != "__user_response__":
                permissao = TOOLS.get(nome_pendente, {}).get("permission")
                acao_pendente.setdefault("permission", permissao)
                acao_pendente.setdefault("idempotent", permissao == "READ")
        else:
            acao_pendente = None

        if status in ("success", "needs_user"):
            job_progress.publicar(
                config, "finalizing",
                "Preparando a resposta final" if status == "success" else "Aguardando uma resposta do usuario",
                partial_text=texto[-16000:] if isinstance(texto, str) else None,
                step=estado.acoes_executadas if estado is not None else None,
            )
        elif status in ("failed", "max_steps"):
            job_progress.publicar(
                config, "agent_issue", texto,
                step=estado.acoes_executadas if estado is not None else None,
            )

        status_persistido = {
            "success": "completed",
            "needs_user": "waiting_user",
            "max_steps": "blocked",
            "failed": "failed",
        }.get(status, "failed")
        _checkpoint({
            "task_id": task_id,
            "status": status_persistido,
            "estado": estado_serializado,
            "continuacao": estado_pendente,
            "acao_pendente": acao_pendente,
            "orcamento_restante": max(0, int(max_steps) - int(estado.acoes_executadas)),
            "pergunta": texto if status == "needs_user" else None,
            "resultado": detalhes if status != "needs_user" else None,
            "causa_fallback": detalhes.get("fallback_cause"),
            "evento": {
                "tipo": "agent_return",
                "agent_status": status,
                "completion_gate": completion_gate["code"],
            },
        })
        return _retorno_agente_base(
            status, texto, estado_pendente, detalhes, retornar_detalhes_local,
        )

    def _checkpoint_acao(acao_pendente, tipo):
        continuacao_acao = acao_pendente or {
            "tool": "__resume__",
            "arguments": {},
            "permission": "READ",
            "idempotent": True,
        }
        continuacao = {
            "task_id": task_id,
            "objetivo": objetivo,
            "modo": modo,
            "task_type": task_type,
            "step_atual": estado.acoes_executadas,
            "estado": estado.to_dict(),
            "tool_pendente": continuacao_acao,
            "continuation_kind": "internal_resume",
        }
        if isinstance(retomar, dict):
            for chave in (
                "id", "tipo_pendencia", "criado_em", "expira_em",
                "projeto_hash", "pergunta_ao_usuario",
            ):
                if retomar.get(chave) is not None:
                    continuacao[chave] = retomar[chave]
        _checkpoint({
            "task_id": task_id,
            "status": "running",
            "estado": estado.to_dict(),
            "continuacao": continuacao,
            "acao_pendente": acao_pendente,
            "orcamento_restante": max(0, int(max_steps) - int(estado.acoes_executadas)),
            "pergunta": None,
            "evento": {"tipo": tipo, "tool": (acao_pendente or {}).get("tool")},
        })

    if retomar:
        estado = AgentState.from_dict(retomar.get("estado"), config=config)
        estado.definir_objetivo(objetivo, task_type, modo=modo)
        if not somente_leitura:
            estado.goal_state["blockers"] = [
                item for item in estado.goal_state.get("blockers", [])
                if item != "rollout_read_only"
            ]
        task_type = estado.goal_state.get("task_type", task_type)
        modo = estado.goal_state.get("mode", modo)
        step = estado.acoes_executadas
        _continuar_trace()

        tool_pendente = retomar.get("tool_pendente") or {}
        tool_confirmada = tool_pendente.get("tool")
        arguments_confirmados = tool_pendente.get("arguments", {}) or {}
        if tool_confirmada == "__user_response__":
            arguments_confirmados = {
                "resposta": str(
                    resposta_usuario
                    if resposta_usuario is not None
                    else arguments_confirmados.get("resposta") or ""
                )
            }

        permissao_confirmada = (
            "READ" if tool_confirmada in ("__user_response__", "__resume__")
            else TOOLS.get(tool_confirmada, {}).get("permission")
        )
        if somente_leitura and permissao_confirmada != "READ":
            transicao_valida = False
            motivo_transicao = (
                "O rollout read_only permite apenas leitura; a acao pendente nao foi executada."
            )
            bloqueios = estado.goal_state.setdefault("blockers", [])
            if "rollout_read_only" not in bloqueios:
                bloqueios.append("rollout_read_only")
        else:
            transicao_valida, motivo_transicao = estado.validar_transicao(
                tool_confirmada, permissao_confirmada,
                edit_habilitado=edit_habilitado,
            )
        if not transicao_valida:
            estado.goal_state["status"] = "blocked"
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "retomada_rejeitada_por_modo",
                "tool": tool_confirmada,
                "motivo": motivo_transicao,
            })
            return _retorno_agente(
                "needs_user", motivo_transicao, None,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                retornar_detalhes,
            )

        acao_checkpoint = {
            "tool": tool_confirmada,
            "arguments": arguments_confirmados,
            "permission": permissao_confirmada,
            "idempotent": permissao_confirmada == "READ",
        }
        _checkpoint_acao(acao_checkpoint, "action_started")
        write_recuperada = False
        if tool_confirmada in ("__user_response__", "__resume__"):
            resultado_tool = {
                "status": "success", "ok": True, "executed": False,
                "changed": False, "error_code": None,
                "detail": (
                    {"resposta_usuario": arguments_confirmados["resposta"]}
                    if tool_confirmada == "__user_response__"
                    else {"retomada_interna": True}
                ),
            }
            if tool_confirmada not in ("__user_response__", "__resume__"):
                _publicar_tool(config, tool_confirmada, step=step)
            if tool_confirmada == "__user_response__":
                bloqueios = estado.goal_state.setdefault("blockers", [])
                pergunta_anterior = retomar.get("pergunta_ao_usuario")
                estado.goal_state["blockers"] = [
                    item for item in bloqueios if item != pergunta_anterior
                ]
                estado.goal_state["status"] = "in_progress"
        elif permissao_confirmada == "WRITE":
            situacao_write, detalhe_write = _inspecionar_write_pendente(
                arguments_confirmados, projeto,
            )
            if situacao_write == "already_applied":
                write_recuperada = True
                resultado_tool = {
                    "status": "success", "ok": True, "executed": False,
                    "changed": False, "error_code": "ALREADY_APPLIED_RECOVERED",
                    "detail": {
                        "outcome": "recovered_already_applied",
                        "file_hash_antes": arguments_confirmados.get("file_hash_esperado"),
                        "range_hash_antes": arguments_confirmados.get("range_hash_esperado"),
                        "file_hash_depois": detalhe_write.get("file_hash_atual"),
                        "linha_fim_final": detalhe_write.get("linha_fim_final"),
                        "rollback_snapshot": None,
                    },
                }
            elif situacao_write == "stale":
                resultado_tool = {
                    "status": "failed", "ok": False, "executed": False,
                    "changed": False, "error_code": "STALE_PATCH",
                    "detail": detalhe_write,
                }
            else:
                resultado_tool = executar_tool(
                    tool_confirmada, arguments_confirmados,
                    {"config": config, "entendimento": entendimento, "projeto": projeto},
                )
        else:
            resultado_tool = executar_tool(
                tool_confirmada, arguments_confirmados,
                {"config": config, "entendimento": entendimento, "projeto": projeto},
            )
        if tool_confirmada not in ("__user_response__", "__resume__"):
            _publicar_tool(config, tool_confirmada, step=step, concluida=True)
        acao_realmente_executada = bool(
            isinstance(resultado_tool, dict) and resultado_tool.get("executed") is True
        ) or write_recuperada
        if acao_realmente_executada:
            estado.registrar_chamada(tool_confirmada, arguments_confirmados)
        estado.registrar_resultado_tool(resultado_tool)  # Atualizacao 11
        acao = estado.registrar_acao(
            tool_confirmada, arguments_confirmados, resultado_tool,
            contar_execucao=(
                tool_confirmada not in ("__user_response__", "__resume__")
                and acao_realmente_executada
            ),
        )
        step = estado.acoes_executadas
        if tool_confirmada == "run_tests":
            estado.registrar_testes(resultado_tool)
        if (
            TOOLS.get(tool_confirmada, {}).get("permission") == "WRITE"
            and isinstance(resultado_tool, dict)
            and (resultado_tool.get("changed") is True or write_recuperada)
        ):
            estado.registrar_escrita()  # Atualizacoes 10/21
            estado.registrar_edicao_aplicada(arguments_confirmados, resultado_tool)
            if write_recuperada:
                estado.edit_state["status"] = "applied_pending_tests"
                estado.edit_state["recovered_already_applied"] = True
            estado.marcar_evidencias_stale(arguments_confirmados.get("caminho_relativo"))
        resultado_verificacao = (
            _resultado_pos_testes(estado, resultado_tool, projeto)
            if tool_confirmada == "run_tests" else None
        )
        entrada_observada = estado.observar(tool_confirmada, resultado_tool)
        _registrar_trace_estado(estado, {
            "step": step,
            "tipo": "tool_call_confirmada",
            "tool": tool_confirmada,
            "arguments": arguments_confirmados,
            "resumo": entrada_observada["resumo"],
        })
        _checkpoint_acao(None, "action_completed")
        if resultado_tool.get("error_code") == "SYMBOL_NOT_FOUND":
            detalhe_simbolo = resultado_tool.get("detail")
            if not isinstance(detalhe_simbolo, str):
                detalhe_simbolo = str(detalhe_simbolo or "simbolo nao encontrado")
            estado.marcar_concluido()
            return _retorno_agente(
                "success", detalhe_simbolo, None,
                {"task_type": task_type, "mode": modo,
                 "goal_state": estado.goal_state,
                 "negative_evidence": {
                     "tool": tool_confirmada,
                     "arguments": arguments_confirmados,
                     "error_code": "SYMBOL_NOT_FOUND",
                 }},
                retornar_detalhes,
            )
        if resultado_tool.get("error_code") == "STALE_PATCH":
            mensagem_stale = _preparar_recuperacao_stale(
                estado, arguments_confirmados, resultado_tool,
            )
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "stale_patch_recuperado",
                "tool": tool_confirmada,
                "mensagem": mensagem_stale,
            })
        if resultado_verificacao is not None:
            status_verificacao, texto_verificacao = resultado_verificacao
            return _retorno_agente(
                status_verificacao, texto_verificacao, None,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state,
                 "edit_state": _edit_state_publico(estado)},
                retornar_detalhes,
            )
        if estado.erros_consecutivos >= max_erros_consecutivos:  # Atualizacao 11
            pergunta = (
                f"O agente encontrou {estado.erros_consecutivos} erro(s) de ferramenta seguido(s) "
                "(a ultima logo apos retomar uma escrita confirmada) e parou para evitar insistir "
                "num caminho que nao esta funcionando."
            )
            _registrar_trace_estado(estado, {"step": step, "tipo": "circuit_breaker", "erros_consecutivos": estado.erros_consecutivos})
            return _retorno_agente(
                "needs_user", pergunta, None,
                {"task_type": task_type, "goal_state": estado.goal_state},
                retornar_detalhes,
            )
    else:
        estado = AgentState(config=config)
        estado.definir_objetivo(objetivo, task_type, modo=modo)
        _iniciar_trace(config)

    # Atualizacao 45: depois da ultima acao permitida ainda existe uma rodada
    # para a LLM devolver ``final``/``needs_user``. O gate abaixo impede uma
    # ferramenta adicional; formato invalido/final recusado usam a guarda
    # separada de decisoes sem progresso.
    while True:
        limite_runtime = _limite_runtime(config)
        if limite_runtime:
            runtime = config.get("_runtime_agent_budget", {})
            _registrar_trace_estado(estado, {
                "step": estado.acoes_executadas + 1,
                "tipo": "runtime_budget_exhausted",
                "reason": limite_runtime,
                "llm_calls": runtime.get("llm_calls", 0),
                "generated_tokens": runtime.get("generated_tokens", 0),
            })
            return _retorno_agente(
                "max_steps",
                "O agente encerrou porque atingiu o limite global de tempo/chamadas da tarefa.",
                None,
                {
                    "task_type": task_type, "mode": modo,
                    "goal_state": estado.goal_state,
                    "runtime_limit": limite_runtime,
                    "llm_calls": runtime.get("llm_calls", 0),
                    "generated_tokens": runtime.get("generated_tokens", 0),
                },
                retornar_detalhes,
            )
        step = estado.acoes_executadas + 1
        job_progress.publicar(
            config, "agent_decision", "Decidindo o proximo passo do agente",
            profile="agent", step=step,
        )
        invalidadas = _atualizar_frescor_evidencias(estado, projeto)
        if invalidadas:
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "evidencias_stale_por_hash",
                "evidence_ids": invalidadas,
            })
        decisao_forcada = _acao_obrigatoria_goal_state(estado)
        tipo_decisao_forcada = "transicao_obrigatoria"
        if decisao_forcada is None and _deve_recuperar_sem_llm(estado):
            decisao_forcada = _acao_recuperacao_deterministica(objetivo, estado)
            tipo_decisao_forcada = "recuperacao_deterministica"

        if decisao_forcada is not None:
            resultado_passo = {
                "decisao": decisao_forcada,
                "falhou": False,
                "tentativas": 0,
                "resposta_bruta": "",
            }
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": tipo_decisao_forcada,
                "tool": decisao_forcada.get("tool"),
                "arguments": decisao_forcada.get("arguments", {}),
            })
        else:
            prompt = montar_prompt_agente(
                objetivo, observacoes=estado.observacoes, entendimento=entendimento,
                fatos_importantes=estado.fatos_importantes,
                catalogo_tools=gerar_catalogo_tools(TOOLS, config=config),
                goal_state=estado.goal_state,
                evidencias=estado.evidence,
                actions=estado.actions,
                edit_state=estado.edit_state,
                config=config,
                system_prompt=PROMPT_AGENTE,
            )
            resultado_passo = decidir_passo(prompt, config)
        decisao = resultado_passo["decisao"]

        if resultado_passo.get("budget_exhausted"):
            runtime = config.get("_runtime_agent_budget", {})
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "runtime_budget_exhausted",
                "reason": resultado_passo["budget_exhausted"],
                "llm_calls": runtime.get("llm_calls", 0),
                "generated_tokens": runtime.get("generated_tokens", 0),
            })
            return _retorno_agente(
                "max_steps",
                "O agente encerrou porque atingiu o limite global de tempo/chamadas da tarefa.",
                None,
                {
                    "task_type": task_type, "mode": modo,
                    "goal_state": estado.goal_state,
                    "runtime_limit": resultado_passo["budget_exhausted"],
                    "llm_calls": runtime.get("llm_calls", 0),
                    "generated_tokens": runtime.get("generated_tokens", 0),
                },
                retornar_detalhes,
            )

        if resultado_passo["falhou"]:
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "parse_falhou",
                "tentativas": resultado_passo["tentativas"],
                "resposta_bruta": (resultado_passo["resposta_bruta"] or "")[:300],
            })
            return _retorno_agente(
                "failed",
                "O agente nao conseguiu decidir o proximo passo (formato invalido apos as tentativas configuradas).",
                None,
                {
                    "task_type": task_type,
                    "mode": modo,
                    "goal_state": estado.goal_state,
                    "failure_code": "AGENT_INVALID_DECISION_FORMAT",
                    "fallback_cause": "invalid_agent_json",
                },
                retornar_detalhes,
            )

        # Atualizacao 12: fato_importante e' opcional e pode vir junto de
        # qualquer uma das tres decisoes (tool_call, final ou needs_user)
        # -- registra antes de decidir o que fazer com a decisao em si.
        estado.registrar_fato(decisao.get("important_fact", decisao.get("fato_importante")))
        replanejado, erro_replanejamento = estado.aplicar_replanejamento(
            decisao.get("goal_update")
        )
        if not replanejado:
            estado.observar("goal_update", {
                "status": "failed", "ok": False, "executed": False,
                "changed": False, "error_code": "INVALID_GOAL_TRANSITION",
                "detail": erro_replanejamento,
            })
            if _sem_progresso(
                estado, max_sem_progresso, step,
                "replanejamento_rejeitado", erro_replanejamento,
            ):
                return _retorno_agente(
                    "needs_user",
                    "O agente nao conseguiu produzir uma transicao valida do plano e parou para nao divagar.",
                    None,
                    {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                    retornar_detalhes,
                )
            continue

        if "final" in decisao:
            # Atualizacao 10: nao aceita "final" so' porque a LLM disse
            # que terminou -- se a tarefa escreveu no projeto (tool
            # WRITE) e 'run_tests' ainda nao passou depois dessa
            # escrita, devolve um passo extra em vez de confiar na
            # palavra da LLM.
            if (
                exigir_testes_apos_escrita
                and estado.houve_escrita
                and not estado.testes_ok_apos_escrita
                and estado.edit_state.get("status") == "applied_without_suite"
                and not estado.edit_state.get("post_write_evidence_id")
            ):
                estado.observar("final", {
                    "status": "failed", "ok": False, "executed": False,
                    "changed": False, "error_code": "POST_WRITE_READ_REQUIRED",
                    "detail": (
                        "A suite nao esta disponivel (executed=false). Releia a faixa final "
                        "com read_range antes de informar o estado nao verificado."
                    ),
                })
                if _sem_progresso(
                    estado, max_sem_progresso, step,
                    "final_recusado_sem_releitura", "falta evidencia pos-escrita",
                ):
                    return _retorno_agente(
                        "needs_user", "A alteracao foi aplicada sem suite, mas faltou releitura final.",
                        None,
                        {"task_type": task_type, "mode": modo,
                         "goal_state": estado.goal_state, "edit_state": _edit_state_publico(estado)},
                        retornar_detalhes,
                    )
                continue
            if (
                exigir_testes_apos_escrita
                and estado.houve_escrita
                and not estado.testes_ok_apos_escrita
                and estado.edit_state.get("status") == "applied_without_suite"
                and estado.edit_state.get("post_write_evidence_id")
            ):
                estado.goal_state["status"] = "blocked"
                return _retorno_agente(
                    "needs_user",
                    "Alteracao aplicada e relida, mas nenhuma suite estava disponivel. "
                    "Nenhum teste foi executado (executed=false); a mudanca permanece sem verificacao de suite.",
                    None,
                    {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state,
                     "edit_state": _edit_state_publico(estado)},
                    retornar_detalhes,
                )
            if exigir_testes_apos_escrita and estado.houve_escrita and not estado.testes_ok_apos_escrita:
                estado.observar_final_sem_verificacao()
                if _sem_progresso(
                    estado, max_sem_progresso, step,
                    "final_recusado_sem_verificacao",
                    "escrita sem run_tests executado com sucesso",
                ):
                    return _retorno_agente(
                        "needs_user",
                        "A conclusao foi recusada repetidamente porque a escrita ainda nao foi verificada.",
                        None,
                        {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                        retornar_detalhes,
                    )
                continue
            conclusao, erro_conclusao = _normalizar_conclusao(decisao, estado, task_type)
            if erro_conclusao:
                estado.observar_final_sem_grounding(erro_conclusao)
                if _sem_progresso(
                    estado, max_sem_progresso, step,
                    "final_recusado_contrato", erro_conclusao,
                ):
                    return _retorno_agente(
                        "needs_user", "O agente repetiu uma conclusao invalida e foi pausado.",
                        None,
                        {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                        retornar_detalhes,
                    )
                continue
            evidencias_usadas = []
            if task_type in ("project_read", "project_write"):
                valido, motivo, evidencias_usadas = _validar_conclusao_projeto(
                    conclusao, estado, projeto,
                )
                if not valido:
                    estado.observar_final_sem_grounding(motivo)
                    atingiu_limite = _sem_progresso(
                        estado, max_sem_progresso, step,
                        "final_recusado_sem_grounding", motivo,
                    )
                    if atingiu_limite:
                        return _retorno_agente(
                            "needs_user", "A conclusao continuou sem evidencia valida e foi pausada.",
                            None,
                            {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                            retornar_detalhes,
                        )
                    continue
                conclusao["verificacao_sistema"] = motivo
                verificacao_semantica = verify_conclusion(
                    conclusao.get("resposta"),
                    evidencias_usadas,
                    config.get("agent", {}).get("semantic_grounding", {}),
                    claim_annotations=conclusao.get("claim_annotations"),
                )
                conclusao["semantic_grounding"] = verificacao_semantica
                if not verificacao_semantica.get("ok", False):
                    resumo_semantico = verificacao_semantica.get("summary") or (
                        "a conclusao contem afirmacoes objetivas sem suporte nas evidencias"
                    )
                    feedback_semantico = format_grounding_feedback(verificacao_semantica)
                    estado.observar_final_sem_grounding(feedback_semantico)
                    if _sem_progresso(
                        estado, max_sem_progresso, step,
                        "final_recusado_semantica", resumo_semantico,
                    ):
                        resposta_original = conclusao.get("resposta") or ""
                        resposta_segura = build_safe_grounded_answer(
                            resposta_original,
                            verificacao_semantica,
                            evidencias_usadas,
                        )
                        verificacao_fallback = verify_conclusion(
                            resposta_segura,
                            evidencias_usadas,
                            config.get("agent", {}).get("semantic_grounding", {}),
                            claim_annotations=conclusao.get("claim_annotations"),
                        )
                        if not verificacao_fallback.get("ok", False):
                            return _retorno_agente(
                                "failed",
                                "O agente nao conseguiu produzir uma conclusao grounded "
                                "mesmo apos reduzir automaticamente a resposta.",
                                None,
                                {
                                    "task_type": task_type,
                                    "mode": modo,
                                    "goal_state": estado.goal_state,
                                    "failure_code": "SEMANTIC_GROUNDING_FAILED",
                                    "fallback_cause": "semantic_grounding_failed",
                                    "semantic_grounding": verificacao_semantica,
                                    "semantic_grounding_fallback": verificacao_fallback,
                                },
                                retornar_detalhes,
                            )
                        conclusao["resposta"] = resposta_segura
                        conclusao["semantic_grounding_original"] = verificacao_semantica
                        conclusao["semantic_grounding"] = verificacao_fallback
                        conclusao["grounding_fallback_applied"] = True
                        conclusao.setdefault("limitacoes", []).append(
                            "A resposta foi reduzida automaticamente para remover "
                            "afirmacoes sem suporte verificavel."
                        )
                    else:
                        continue
                if task_type == "project_write":
                    edit_valido, motivo_edit = estado.validar_conclusao_edicao(
                        conclusao.get("evidence_ids") or [],
                    )
                    if not edit_valido:
                        estado.observar_final_sem_grounding(motivo_edit)
                        if _sem_progresso(
                            estado, max_sem_progresso, step,
                            "final_recusado_ciclo_edicao", motivo_edit,
                        ):
                            return _retorno_agente(
                                "needs_user", motivo_edit, None,
                                {"task_type": task_type, "mode": modo,
                                 "goal_state": estado.goal_state,
                                 "edit_state": _edit_state_publico(estado)},
                                retornar_detalhes,
                            )
                        continue
                    conclusao["verificacao_sistema"] = motivo_edit

            estado.marcar_concluido()
            detalhes = {
                "task_type": task_type,
                "mode": modo,
                "goal_state": estado.goal_state,
                "evidence_ids": conclusao.get("evidence_ids", []),
                "evidencias_usadas": [
                    {
                        "id": item.get("id"),
                        "arquivo": item.get("arquivo"),
                        "linha_inicio": item.get("linha_inicio"),
                        "linha_fim": item.get("linha_fim"),
                        "total_linhas_arquivo": item.get("total_linhas_arquivo"),
                        "truncado": item.get("truncado"),
                        "leitura_completa": item.get("leitura_completa"),
                        "content_hash": item.get("content_hash"),
                        "file_hash": item.get("file_hash"),
                        "estado": item.get("estado"),
                    }
                    for item in evidencias_usadas
                ],
                "verificacao": conclusao.get("verificacao"),
                "verificacao_sistema": conclusao.get("verificacao_sistema"),
                "semantic_grounding": conclusao.get("semantic_grounding"),
                "semantic_grounding_original": conclusao.get("semantic_grounding_original"),
                "grounding_fallback_applied": bool(conclusao.get("grounding_fallback_applied")),
                "fallback_cause": (
                    "semantic_grounding_safe_fallback"
                    if conclusao.get("grounding_fallback_applied") else None
                ),
                "limitacoes": conclusao.get("limitacoes", []),
                "edit_state": _edit_state_publico(estado),
            }
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "final",
                "resposta": conclusao["resposta"],
                "evidence_ids": conclusao.get("evidence_ids", []),
                "verificacao_sistema": conclusao.get("verificacao_sistema"),
            })
            return _retorno_agente(
                "success", conclusao["resposta"], None, detalhes,
                retornar_detalhes,
            )

        if "needs_user" in decisao and _needs_user_antes_de_leitura(estado, task_type):
            pergunta_prematura = str(decisao.get("needs_user") or "").strip()
            detalhe_prematuro = (
                "needs_user recusado: ainda nao houve tentativa de leitura do projeto. "
                "Use as tools READ disponiveis; em analise geral, comece por list_tree "
                "e depois leia codigo relevante antes de pedir informacao ao usuario."
            )
            estado.observar("needs_user", {
                "status": "failed", "ok": False, "executed": False,
                "changed": False, "error_code": "PREMATURE_NEEDS_USER",
                "detail": detalhe_prematuro,
            })
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "needs_user_recusado_sem_leitura",
                "pergunta": pergunta_prematura,
            })
            leitura_explicita = _proxima_leitura_explicita(objetivo, estado)
            if _analise_geral_ainda_precisa_list_tree(estado):
                # O plano ja declarou esta primeira acao. Executa pelo fluxo
                # normal abaixo, com schema, gate, trace e checkpoint iguais
                # aos de uma decisao da LLM.
                decisao = {"tool": "list_tree", "arguments": {}}
            elif leitura_explicita is not None and estado.decisoes_sem_progresso >= 1:
                # Na primeira fuga, o modelo ainda recebe a correcao e pode
                # escolher read_range. Se insistir, o sistema faz a leitura
                # literal obvia em vez de pausar sem sequer abrir o arquivo.
                decisao = leitura_explicita
            else:
                if _sem_progresso(
                    estado, max_sem_progresso, step,
                    "needs_user_recusado_sem_leitura", detalhe_prematuro,
                ):
                    return _retorno_agente(
                        "needs_user",
                        "O agente nao conseguiu iniciar uma leitura valida do projeto e foi pausado.",
                        None,
                        {"task_type": task_type, "mode": modo,
                         "goal_state": estado.goal_state},
                        retornar_detalhes,
                    )
                continue

        if "needs_user" in decisao:
            estado.goal_state["status"] = "blocked"
            bloqueios = estado.goal_state.setdefault("blockers", [])
            if decisao["needs_user"] not in bloqueios:
                bloqueios.append(decisao["needs_user"])
            _registrar_trace_estado(estado, {"step": step, "tipo": "needs_user", "pergunta": decisao["needs_user"]})
            return _retorno_agente(
                "needs_user", decisao["needs_user"], None,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                retornar_detalhes,
            )

        # tool_call
        tool = decisao.get("tool")
        arguments_brutos = _completar_argumentos_obvios(
            tool, decisao.get("arguments", {}), objetivo,
        )
        arguments_brutos = estado.completar_argumentos_patch(tool, arguments_brutos)
        arguments, erro_argumentos = validar_chamada_tool(
            tool, arguments_brutos, registro=TOOLS,
        )
        if erro_argumentos is not None:
            estado.registrar_resultado_tool(erro_argumentos)
            estado.registrar_acao(tool or "tool_invalida", arguments_brutos, erro_argumentos)
            entrada_observada = estado.observar(tool or "tool_invalida", erro_argumentos)
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "tool_call_invalida",
                "tool": tool,
                "arguments": arguments_brutos,
                "error_code": erro_argumentos.get("error_code"),
                "resumo": entrada_observada["resumo"],
            })
            if estado.erros_consecutivos >= max_erros_consecutivos:
                pergunta = (
                    f"O agente encontrou {estado.erros_consecutivos} erro(s) de ferramenta seguido(s) "
                    "e parou para evitar insistir num caminho que nao esta funcionando."
                )
                return _retorno_agente(
                    "needs_user", pergunta, None,
                    {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                    retornar_detalhes,
                )
            if _sem_progresso(
                estado, max_sem_progresso, step,
                "tool_call_invalida_repetida", erro_argumentos.get("detail"),
            ):
                return _retorno_agente(
                    "needs_user", "O agente repetiu decisoes sem executar uma acao valida e foi pausado.",
                    None,
                    {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                    retornar_detalhes,
                )
            continue

        permissao = TOOLS.get(tool, {}).get("permission")
        if somente_leitura and permissao != "READ":
            transicao_valida = False
            motivo_transicao = (
                "O rollout read_only permite apenas ferramentas READ; WRITE/EXEC foi bloqueada antes da execucao."
            )
            bloqueios = estado.goal_state.setdefault("blockers", [])
            if "rollout_read_only" not in bloqueios:
                bloqueios.append("rollout_read_only")
        else:
            transicao_valida, motivo_transicao = estado.validar_transicao(
                tool, permissao, edit_habilitado=edit_habilitado,
            )
        if not transicao_valida:
            resultado_transicao = {
                "status": "failed", "ok": False, "executed": False,
                "changed": False, "error_code": "INVALID_GOAL_TRANSITION",
                "detail": motivo_transicao,
            }
            estado.registrar_resultado_tool(resultado_transicao)
            estado.registrar_acao(tool, arguments, resultado_transicao)
            estado.observar(tool, resultado_transicao)
            if _sem_progresso(
                estado, max_sem_progresso, step,
                "transicao_rejeitada", motivo_transicao,
            ):
                return _retorno_agente(
                    "needs_user", motivo_transicao, None,
                    {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                    retornar_detalhes,
                )
            continue
        if tool == "apply_patch" and isinstance(TOOLS.get(tool, {}).get("input_schema"), dict):
            precondicoes_ok, motivo_precondicao = estado.validar_precondicoes_patch(arguments)
            if not precondicoes_ok:
                resultado_precondicao = {
                    "status": "failed", "ok": False, "executed": False,
                    "changed": False, "error_code": "PATCH_PRECONDITION_FAILED",
                    "detail": motivo_precondicao,
                }
                estado.registrar_resultado_tool(resultado_precondicao)
                estado.registrar_acao(tool, arguments, resultado_precondicao)
                estado.observar(tool, resultado_precondicao)
                if _sem_progresso(
                    estado, max_sem_progresso, step,
                    "patch_sem_leitura_ou_dry_run", motivo_precondicao,
                ):
                    return _retorno_agente(
                        "needs_user", motivo_precondicao, None,
                        {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                        retornar_detalhes,
                    )
                continue
        if estado.acoes_executadas >= max_steps:
            estado.goal_state["status"] = "blocked"
            _registrar_trace_estado(estado, {
                "step": estado.acoes_executadas,
                "tipo": "max_steps",
                "tool_recusada": tool,
            })
            return _retorno_agente(
                "max_steps",
                f"O agente atingiu o limite de {max_steps} acao(oes) reais sem concluir a tarefa.",
                None,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                retornar_detalhes,
            )
        precisa_confirmacao = (
            (permissao == "WRITE" and exigir_confirmacao_write)
            or (permissao == "EXEC" and exigir_confirmacao_exec)
        )
        if precisa_confirmacao:
            if tool == "apply_patch":
                pergunta = (
                    "Proposta pronta para confirmacao: "
                    + _resumo_confirmacao_patch(arguments, entendimento)
                    + " Confirma a aplicacao atomica e a verificacao?"
                )
            else:
                acao = "uma escrita no projeto" if permissao == "WRITE" else "codigo de teste no sandbox"
                pergunta = (
                    f"A ferramenta '{tool}' executa {acao} (argumentos: {arguments}). "
                    "Confirma a execucao?"
                )
            estado.goal_state["status"] = "blocked"
            _registrar_trace_estado(estado, {
                "step": step, "tipo": f"needs_user_{permissao.lower()}",
                "tool": tool, "arguments": arguments,
            })
            estado_pendente = {
                "objetivo": objetivo,
                "step_atual": estado.acoes_executadas,
                "estado": estado.to_dict(),
                "tool_pendente": {"tool": tool, "arguments": arguments},
                "pergunta_ao_usuario": pergunta,
            }
            return _retorno_agente(
                "needs_user", pergunta, estado_pendente,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                retornar_detalhes,
            )

        if estado.chamada_repetida(tool, arguments):
            estado.observar_chamada_repetida(tool)
            if _sem_progresso(
                estado, max_sem_progresso, step,
                "chamada_repetida", f"{tool} com os mesmos argumentos",
            ):
                return _retorno_agente(
                    "needs_user", "O agente repetiu a mesma acao sem progresso e foi pausado.",
                    None,
                    {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                    retornar_detalhes,
                )
            continue

        limite_runtime = _limite_runtime(config)
        if limite_runtime:
            runtime = config.get("_runtime_agent_budget", {})
            _registrar_trace_estado(estado, {
                "step": step, "tipo": "runtime_budget_before_tool",
                "reason": limite_runtime, "tool": tool,
            })
            return _retorno_agente(
                "max_steps",
                "O agente encerrou antes da próxima ferramenta porque o prazo global acabou.",
                None,
                {
                    "task_type": task_type, "mode": modo,
                    "goal_state": estado.goal_state,
                    "runtime_limit": limite_runtime,
                    "llm_calls": runtime.get("llm_calls", 0),
                    "generated_tokens": runtime.get("generated_tokens", 0),
                },
                retornar_detalhes,
            )

        _publicar_tool(config, tool, step=step)
        _checkpoint_acao({
            "tool": tool,
            "arguments": arguments,
            "permission": permissao,
            "idempotent": permissao == "READ",
        }, "action_started")
        inicio_tool = time.monotonic()
        try:
            resultado_tool = executar_tool(
                tool, arguments, {"config": config, "entendimento": entendimento, "projeto": projeto}
            )
        except Exception as erro_tool:
            telemetry.record(
                "tool", tool or "unknown", "exception",
                (time.monotonic() - inicio_tool) * 1000,
                task_id=(config.get("_runtime_agent_budget") or {}).get("task_id"),
                job_id=(config.get("_runtime_agent_budget") or {}).get("source_job_id"),
                metadata={"exception": type(erro_tool).__name__, "detail": str(erro_tool)[:500]},
            )
            raise
        telemetry.record(
            "tool", tool or "unknown",
            "ok" if isinstance(resultado_tool, dict) and resultado_tool.get("ok") else "failed",
            (time.monotonic() - inicio_tool) * 1000,
            task_id=(config.get("_runtime_agent_budget") or {}).get("task_id"),
            job_id=(config.get("_runtime_agent_budget") or {}).get("source_job_id"),
            metadata={
                "permission": permissao,
                "error_code": resultado_tool.get("error_code") if isinstance(resultado_tool, dict) else None,
                "changed": resultado_tool.get("changed") if isinstance(resultado_tool, dict) else None,
            },
        )
        _publicar_tool(config, tool, step=step, concluida=True)
        estado.registrar_chamada(tool, arguments)
        estado.registrar_resultado_tool(resultado_tool)  # Atualizacao 11
        estado.registrar_acao(tool, arguments, resultado_tool, contar_execucao=True)
        step = estado.acoes_executadas
        if tool == "run_tests":
            estado.registrar_testes(resultado_tool)  # Atualizacao 10
        if (
            permissao == "WRITE"
            and isinstance(resultado_tool, dict)
            and resultado_tool.get("changed") is True
        ):
            estado.registrar_escrita()  # Atualizacao 21
            estado.registrar_edicao_aplicada(arguments, resultado_tool)
            estado.marcar_evidencias_stale(arguments.get("caminho_relativo"))
        resultado_verificacao = (
            _resultado_pos_testes(estado, resultado_tool, projeto)
            if tool == "run_tests" else None
        )
        entrada_observada = estado.observar(tool, resultado_tool)
        _registrar_trace_estado(estado, {
            "step": step,
            "tipo": "tool_call",
            "tool": tool,
            "arguments": arguments,
            "resumo": entrada_observada["resumo"],
        })
        _checkpoint_acao(None, "action_completed")

        ciclo = estado.registrar_fingerprint_ciclo(
            tool,
            resultado_tool,
            min_repeticoes=cfg_agente.get("cycle_min_repetitions", 3),
        )
        if resultado_tool.get("ok") is True and ciclo.get("detectado"):
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "ciclo_de_estado_detectado",
                "tool": tool,
                "periodo": ciclo.get("periodo"),
                "fingerprint": ciclo.get("fingerprint"),
            })
            return _retorno_agente(
                "needs_user",
                "O agente voltou ao mesmo estado observavel em um ciclo curto e foi pausado antes de gastar mais chamadas.",
                None,
                {
                    "task_type": task_type,
                    "mode": modo,
                    "goal_state": estado.goal_state,
                    "cycle_period": ciclo.get("periodo"),
                },
                retornar_detalhes,
            )

        if resultado_tool.get("error_code") == "SYMBOL_NOT_FOUND":
            detalhe_simbolo = resultado_tool.get("detail")
            if not isinstance(detalhe_simbolo, str):
                detalhe_simbolo = str(detalhe_simbolo or "simbolo nao encontrado")
            estado.marcar_concluido()
            return _retorno_agente(
                "success", detalhe_simbolo, None,
                {"task_type": task_type, "mode": modo,
                 "goal_state": estado.goal_state,
                 "negative_evidence": {
                     "tool": tool,
                     "arguments": arguments,
                     "error_code": "SYMBOL_NOT_FOUND",
                 }},
                retornar_detalhes,
            )
        if resultado_tool.get("error_code") == "STALE_PATCH":
            mensagem_stale = _preparar_recuperacao_stale(
                estado, arguments, resultado_tool,
            )
            _registrar_trace_estado(estado, {
                "step": step,
                "tipo": "stale_patch_recuperado",
                "tool": tool,
                "mensagem": mensagem_stale,
            })
            continue
        if resultado_verificacao is not None:
            status_verificacao, texto_verificacao = resultado_verificacao
            return _retorno_agente(
                status_verificacao, texto_verificacao, None,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state,
                 "edit_state": _edit_state_publico(estado)},
                retornar_detalhes,
            )

        if estado.erros_consecutivos >= max_erros_consecutivos:  # Atualizacao 11
            pergunta = (
                f"O agente encontrou {estado.erros_consecutivos} erro(s) de ferramenta seguido(s) "
                "e parou para evitar insistir num caminho que nao esta funcionando."
            )
            _registrar_trace_estado(estado, {"step": step, "tipo": "circuit_breaker", "erros_consecutivos": estado.erros_consecutivos})
            return _retorno_agente(
                "needs_user", pergunta, None,
                {"task_type": task_type, "mode": modo, "goal_state": estado.goal_state},
                retornar_detalhes,
            )
