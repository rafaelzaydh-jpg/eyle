#!/usr/bin/env python3
"""
agent_state.py
--------------
Estado do Agente minimo da Eyle -- Atualizacao 2 (observacoes resumidas
e historico limitado, problema 2 do plano v2:
"o contexto que cada passo recebe cresce mais rapido do que o orcamento
de tokens permite").

Escopo original (Atualizacao 2): guardar o RESULTADO de cada tool ja
resumido (nunca o resultado cru) em AgentState.observacoes, e deixar
compiler.py:montar_prompt_agente fatiar so as ultimas 3-4 entradas na
hora de montar o proximo prompt. Isso segue a mesma disciplina que
retrieval/buscar.py e engine/dicas.py ja aplicam no resto da Eyle: nunca
mandar tudo, mandar o que cabe e importa agora.

Atualizacao 3 (ordem de preferencia de ferramentas + guarda de chamada
repetida): a ordem de preferencia em si e' so texto, vive em
PROMPT_AGENTE (llm/executar.py). O que entra aqui e' a guarda de
repeticao -- self.assinaturas_chamadas guarda (tool, argumentos
canonicos) de cada chamada ja EXECUTADA nesta tarefa. Quem roda o loop
principal do Agente chama chamada_repetida(tool, arguments) ANTES de
executar uma tool: se True, chama observar_chamada_repetida(tool) em
vez de rodar a tool de novo; se False, executa normalmente e so entao
chama registrar_chamada(tool, arguments) (nunca antes de rodar, e nunca
quando a chamada foi barrada por ja ser repetida).

Fora de escopo aqui (proxima atualizacao, ja mapeada no plano):
- max_steps, status do loop principal e context/agent_trace.jsonl
  (Atualizacao 4)

Fase 3 (Atualizacao_Agente.md): to_dict()/from_dict() nesta classe --
serializam so o que ja existe aqui (observacoes resumidas e assinaturas
de chamada), sem reescrever a classe. Quem persiste/reidrata o estado
entre turnos e' engine/agent.py (executar_agente com o parametro
`retomar`) e engine/engine.py (checkpoint SQLite/JSON legado), no mesmo
padrao ja usado por context/proposta_pendente.json.

Atualizacao 10 (verificador de conclusao objetivo): duas flags novas,
houve_escrita e testes_ok_apos_escrita. engine/agent.py e' quem as
atualiza (e' quem sabe a permission de cada tool); esta classe so
guarda o estado e serializa. O loop principal usa as duas pra recusar
{"final": ...} quando a tarefa escreveu no projeto (tool WRITE) mas
'run_tests' ainda nao rodou com sucesso depois da ultima escrita.

Atualizacao 11 (circuit breaker de erro consecutivo): erros_consecutivos
conta falhas de tool (resultado com chave "erro") em sequencia,
independente de qual tool -- zera assim que uma tool roda sem erro.
engine/agent.py compara isso contra config["agent"]["max_erros_consecutivos"]
a cada passo.

Atualizacao 12 (fatos_importantes): lista separada de observacoes,
alimentada pela chave opcional "fato_importante" que a LLM pode incluir
em qualquer decisao (tool_call, final ou needs_user). Ao contrario de
observacoes, NUNCA e' cortada por max_entradas em
compiler.py:montar_prompt_agente -- sempre entra inteira no prompt (com
teto max_fatos_importantes, FIFO, pra nao crescer sem limite numa tarefa
muito longa).

Atualizacao 21 (contrato de tools): resultados passam a ter sempre
`status`, `ok`, `executed`, `changed`, `error_code`, `detail`. O estado
usa somente esse contrato para decidir falha, teste executado e escrita
real; formatos antigos continuam aceitos apenas ao reidratar mocks/estado
legado, sem serem produzidos pelas tools atuais.

Atualizacao 42 (Context Engine): o estado passa a separar `goal_state`,
`evidence`, `actions` e `recent_observations`. Leituras de codigo reais
guardam conteudo completo, faixa e hash por evidence_id; observacoes seguem
resumidas porque sao apenas feedback operacional. O alias `observacoes`
preserva compatibilidade com o loop anterior.

Atualizacao 43 (grounding): evidencias podem virar `stale`; escrita ou hash
alterado libera a mesma faixa para releitura. O sistema, nao a LLM, decide se
os IDs/faixas/hashes satisfazem a conclusao.

Esta classe nao executa o loop: acumula os quatro blocos de estado, fatos e
guardas para `engine/agent.py` montar o proximo passo e validar a conclusao.
"""
import hashlib
import json
import re

from engine.text_hash import hash_faixa, normalizar_quebras


class GoalState:
    """Contrato pequeno e deterministico do objetivo (Atualizacao 45).

    O modelo ve e usa este estado, mas nao pode troca-lo livremente. O sistema
    cria o plano inicial, limita-o a cinco passos e so aceita replanejamento
    pelos gatilhos objetivos declarados no plano 40+.
    """

    MODOS = {"chat", "analyze", "suggest", "edit"}
    GATILHOS_REPLANEJAMENTO = {
        "tool_failure", "hypothesis_denied", "file_changed",
    }

    @staticmethod
    def _passos(descricoes):
        return [
            {"id": f"step-{indice}", "description": descricao, "status": "pending"}
            for indice, descricao in enumerate(descricoes[:5], start=1)
        ]

    @classmethod
    def criar(cls, objetivo, task_type, modo):
        objetivo_texto = str(objetivo or "")
        alvo_especifico = bool(re.search(
            r"[\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|yml|yaml)\b",
            objetivo_texto,
            re.IGNORECASE,
        ))
        modo = modo if modo in cls.MODOS else (
            "edit" if task_type == "project_write"
            else "analyze" if task_type == "project_read"
            else "chat"
        )
        if modo == "chat":
            criterios = ["resposta_direta"]
            restricoes = ["sem_ferramentas_de_projeto"]
            plano = ["Responder ao usuario"]
            evidencias_necessarias = []
        elif modo == "suggest":
            criterios = ["codigo_fresco_lido", "sugestoes_grounded", "sem_escrita"]
            restricoes = ["somente_leitura", "uma_acao_por_decisao"]
            plano = (
                ["Ler o codigo fresco do alvo", "Propor melhorias com evidencias"]
                if alvo_especifico else [
                    "Localizar o componente relevante",
                    "Ler o codigo fresco necessario",
                    "Propor melhorias com evidencias",
                ]
            )
            evidencias_necessarias = ["codigo_fresco_relevante"]
        elif modo == "edit":
            criterios = [
                "codigo_fresco_lido", "confirmacao_explicita",
                "mudanca_verificada",
            ]
            restricoes = ["uma_acao_por_decisao", "escrita_confirmada"]
            plano = [
                "Localizar e ler o alvo",
                "Preparar a mudanca",
                "Confirmar e aplicar",
                "Testar e reler o resultado",
            ]
            evidencias_necessarias = ["codigo_fresco_relevante", "evidencia_pos_escrita"]
        else:
            criterios = ["codigo_fresco_lido", "resposta_grounded"]
            restricoes = ["somente_leitura", "uma_acao_por_decisao"]
            plano = (
                ["Ler o codigo fresco do alvo", "Responder com evidencias"]
                if alvo_especifico else [
                    "Mapear o projeto com list_tree",
                    "Ler o codigo fresco necessario",
                    "Responder com evidencias",
                ]
            )
            evidencias_necessarias = ["codigo_fresco_relevante"]

        return {
            "objective": objetivo_texto,
            "mode": modo,
            "task_type": task_type,
            "success_criteria": criterios,
            "constraints": restricoes,
            "plan": cls._passos(plano),
            "current_step": 1,
            "blockers": [],
            "evidence_needed": evidencias_necessarias,
            "status": "in_progress",
            "replan_reason": None,
            "actions_executed": 0,
        }

    @classmethod
    def normalizar(cls, dados, objetivo, task_type, modo):
        """Completa estados 42-43 persistidos sem quebrar a retomada."""
        base = cls.criar(objetivo, task_type, modo)
        if not isinstance(dados, dict):
            return base
        normalizado = dict(base)
        for chave in (
            "objective", "mode", "task_type", "success_criteria", "constraints",
            "plan", "current_step", "blockers", "evidence_needed", "status",
            "replan_reason", "actions_executed",
        ):
            if chave in dados:
                normalizado[chave] = dados[chave]
        if normalizado.get("mode") not in cls.MODOS:
            normalizado["mode"] = base["mode"]
        plano = normalizado.get("plan")
        if not isinstance(plano, list) or not plano or len(plano) > 5:
            normalizado["plan"] = base["plan"]
        else:
            passos = []
            for indice, item in enumerate(plano[:5], start=1):
                if isinstance(item, str):
                    item = {"description": item}
                if not isinstance(item, dict):
                    continue
                passos.append({
                    "id": str(item.get("id") or f"step-{indice}"),
                    "description": str(item.get("description") or "Executar passo"),
                    "status": item.get("status") if item.get("status") in (
                        "pending", "in_progress", "completed", "blocked",
                    ) else "pending",
                })
            normalizado["plan"] = passos or base["plan"]
        try:
            passo = int(normalizado.get("current_step", 1))
        except (TypeError, ValueError):
            passo = 1
        normalizado["current_step"] = min(max(passo, 1), len(normalizado["plan"]))
        for chave in ("success_criteria", "constraints", "blockers", "evidence_needed"):
            if not isinstance(normalizado.get(chave), list):
                normalizado[chave] = list(base[chave])
        return normalizado

    @classmethod
    def replanejar(cls, estado, gatilho, detalhe, novo_plano=None,
                   evidencias_necessarias=None):
        if gatilho not in cls.GATILHOS_REPLANEJAMENTO:
            return False, "gatilho de replanejamento invalido"
        if evidencias_necessarias is not None and (
            not isinstance(evidencias_necessarias, list) or not all(
                isinstance(item, str) and item.strip() for item in evidencias_necessarias
            )
        ):
            return False, "evidence_needed precisa ser uma lista de textos"
        if novo_plano is not None:
            if not isinstance(novo_plano, list) or not 1 <= len(novo_plano) <= 5:
                return False, "o novo plano precisa ter entre 1 e 5 passos"
            if not all(isinstance(item, str) and item.strip() for item in novo_plano):
                return False, "cada passo do novo plano precisa ser texto nao vazio"
            estado["plan"] = cls._passos([item.strip() for item in novo_plano])
            estado["current_step"] = 1
        else:
            plano = list(estado.get("plan") or [])
            descricao = {
                "tool_failure": "Corrigir a falha da ferramenta antes de continuar",
                "file_changed": "Reler o codigo alterado antes de continuar",
                "hypothesis_denied": "Reavaliar a hipotese com a evidencia observada",
            }[gatilho]
            passo_atual = max(int(estado.get("current_step") or 1) - 1, 0)
            recuperacao = {
                "id": f"step-r{len(estado.get('blockers') or []) + 1}",
                "description": descricao,
                "status": "in_progress",
            }
            if len(plano) < 5:
                plano.insert(min(passo_atual, len(plano)), recuperacao)
            elif plano:
                plano[min(passo_atual, len(plano) - 1)] = recuperacao
            else:
                plano = [recuperacao]
            estado["plan"] = plano[:5]
            estado["current_step"] = min(passo_atual + 1, len(estado["plan"]))
        if evidencias_necessarias is not None:
            estado["evidence_needed"] = list(dict.fromkeys(evidencias_necessarias))
        detalhe = str(detalhe or gatilho)
        bloqueios = estado.setdefault("blockers", [])
        if detalhe not in bloqueios:
            bloqueios.append(detalhe)
        estado["replan_reason"] = gatilho
        estado["status"] = "in_progress"
        return True, None


def _resumir_texto(texto, max_chars):
    """
    Trunca uma string mostrando inicio e fim, pra nao perder nem o
    comeco nem o resultado final de algo longo (ex: read_file de um
    arquivo grande). Deliberadamente burro -- sem chamar LLM pra
    resumir a propria observacao (isso so aumenta custo e falha em
    cascata); o objetivo e caber no orcamento, nao um resumo bonito.
    """
    if texto is None:
        texto = ""
    texto = str(texto)
    if len(texto) <= max_chars:
        return texto

    metade = max(max_chars // 2 - 20, 40)
    inicio = texto[:metade].rstrip()
    fim = texto[-metade:].lstrip()
    omitidos = len(texto) - len(inicio) - len(fim)
    return f"{inicio}\n[... {omitidos} caracteres omitidos para caber no orcamento ...]\n{fim}"


def _resumir_resultado(tool, resultado, max_chars=500):
    """
    Resume o resultado de uma tool antes de entrar em AgentState.observacoes.
    Regra deliberadamente simples e determinística por tipo de tool
    (secao 2.2 do plano v2):

      - read_file: trunca mostrando inicio/fim (primeiras/ultimas linhas),
        nao o arquivo inteiro.
      - search_code: contagem de resultados encontrados, nao os trechos
        inteiros.
      - apply_patch: so ok/detalhe, nunca o diff completo.
      - qualquer outra tool (ainda nao prevista aqui -- lista real vem na
        Atualizacao 3 com engine/agent_tools.py): trunca a representacao
        em texto do resultado, tratando dict/list como JSON e o resto
        como string.

    Nunca chama a LLM -- e' so formatacao/corte de texto, barato e
    previsivel mesmo com um modelo pequeno/local.
    """
    envelope = isinstance(resultado, dict) and "detail" in resultado and "status" in resultado
    detalhe = resultado.get("detail") if envelope else resultado
    prefixo_erro = ""
    if envelope and resultado.get("ok") is False:
        prefixo_erro = f"[{resultado.get('error_code') or 'TOOL_FAILED'}] "

    if tool in ("read_file", "read_range"):
        if isinstance(detalhe, dict):
            conteudo = detalhe.get(
                "trecho_numerado",
                detalhe.get("conteudo", detalhe.get("resultado", detalhe)),
            )
        else:
            conteudo = detalhe
        return _resumir_texto(prefixo_erro + str(conteudo), max_chars)

    if tool == "search_code":
        if isinstance(detalhe, dict):
            ocorrencias = detalhe.get("resultados", detalhe.get("ocorrencias", []))
        elif isinstance(detalhe, list):
            ocorrencias = detalhe
        else:
            ocorrencias = None

        if ocorrencias is not None:
            total = len(ocorrencias)
            if total == 0:
                return "0 resultado(s) encontrado(s)."
            primeiros = ocorrencias[:3]
            linhas = [f"{total} resultado(s) encontrado(s), mostrando os primeiros {len(primeiros)}:"]
            for item in primeiros:
                if isinstance(item, dict):
                    cabecalho = (
                        f"- {item.get('arquivo')}:{item.get('linha_inicio')}-{item.get('linha_fim')}"
                        f" simbolo={item.get('simbolo')} score={item.get('score')}"
                        f" hash={item.get('content_hash')}"
                    )
                    linhas.append(cabecalho)
                    if item.get("trecho_numerado"):
                        linhas.append(item["trecho_numerado"])
                else:
                    linhas.append(f"- {item}")
            return _resumir_texto("\n".join(linhas), max_chars)
        return _resumir_texto(prefixo_erro + str(detalhe), max_chars)

    if tool == "list_tree" and isinstance(detalhe, dict):
        linhas = []
        for item in detalhe.get("entradas", []):
            sufixo = "/" if item.get("tipo") == "diretorio" else ""
            linhas.append(f"- {item.get('caminho')}{sufixo}")
        linhas.append(f"ignorados_por_motivo={detalhe.get('ignorados_por_motivo', {})}")
        if detalhe.get("truncado"):
            linhas.append("[arvore truncada pelo limite]")
        return _resumir_texto(prefixo_erro + "\n".join(linhas), max_chars)

    if tool == "apply_patch":
        if isinstance(resultado, dict):
            ok = resultado.get("ok")
            if isinstance(detalhe, dict):
                texto_detalhe = detalhe.get("message", detalhe.get("detalhe", detalhe))
            else:
                texto_detalhe = detalhe
            status = "ok" if ok else "falhou"
            return _resumir_texto(f"[{status}] {texto_detalhe}", max_chars)
        return _resumir_texto(resultado, max_chars)

    # tool desconhecida/generica: so trunca a representacao textual
    valor_resumo = detalhe if envelope else resultado
    if isinstance(valor_resumo, (dict, list)):
        try:
            texto = json.dumps(valor_resumo, ensure_ascii=False)
        except (TypeError, ValueError):
            texto = str(valor_resumo)
    else:
        texto = valor_resumo
    return _resumir_texto(prefixo_erro + str(texto), max_chars)


def _resumir_argumentos(arguments, max_chars=300):
    """Mantem a acao auditavel sem duplicar patches enormes no estado."""
    resumo = {}
    for chave, valor in (arguments or {}).items():
        if isinstance(valor, str) and len(valor) > max_chars:
            resumo[chave] = _resumir_texto(valor, max_chars)
        else:
            resumo[chave] = valor
    return resumo


def _fingerprint_patch(arguments):
    campos = {
        chave: (arguments or {}).get(chave)
        for chave in (
            "caminho_relativo", "linha_inicio", "linha_fim", "codigo_novo",
            "file_hash_esperado", "range_hash_esperado",
        )
    }
    bruto = json.dumps(campos, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


class AgentState:
    """
    Estado minimo do Agente para esta atualizacao: so acumula, por
    passo, um registro resumido {"tool": ..., "resumo": ...} em
    self.observacoes. Quem monta o proximo prompt (compiler.py:
    montar_prompt_agente) decide quantas observacoes recentes entram --
    esta classe nao filtra nada, so garante que cada entrada ja chega
    resumida (nunca crua) antes de ser guardada.
    """

    def __init__(self, config=None):
        cfg_agente = (config or {}).get("agent", {})
        self.max_chars_por_observacao = cfg_agente.get("max_chars_por_observacao", 500)
        self.max_fatos_importantes = cfg_agente.get("max_fatos_importantes", 10)
        # Atualizacao 42: quatro blocos com papeis distintos. O alias
        # ``observacoes`` abaixo preserva consumidores anteriores.
        self.goal_state = {}
        self.evidence = []
        self.actions = []
        self.recent_observations = []
        self._proximo_evidence_id = 1
        self._proximo_action_id = 1
        self.assinaturas_chamadas = set()  # Atualizacao 3: guarda de repeticao
        self.fatos_importantes = []  # Atualizacao 12
        self.erros_consecutivos = 0  # Atualizacao 11: circuit breaker
        self.houve_escrita = False  # Atualizacao 10: verificador de conclusao
        self.testes_ok_apos_escrita = False  # Atualizacao 10
        self.acoes_executadas = 0  # Atualizacao 45: max_steps conta acoes reais
        self.decisoes_sem_progresso = 0
        self.edit_state = {}

    @property
    def observacoes(self):
        """Alias legado para ``recent_observations``."""
        return self.recent_observations

    @observacoes.setter
    def observacoes(self, valor):
        self.recent_observations = list(valor or [])

    def definir_objetivo(self, objetivo, task_type, modo=None):
        """Cria/normaliza o GoalState executavel das Atualizacoes 44-45."""
        if not self.goal_state:
            self.goal_state = GoalState.criar(objetivo, task_type, modo)
        else:
            self.goal_state = GoalState.normalizar(
                self.goal_state, objetivo, task_type, modo,
            )
        self.acoes_executadas = max(
            int(self.acoes_executadas or 0),
            int(self.goal_state.get("actions_executed") or 0),
        )
        self.goal_state["actions_executed"] = self.acoes_executadas

    def validar_transicao(self, tool, permission, edit_habilitado=False):
        """Impede uma acao fora do modo antes de confirmação/execução."""
        modo = self.goal_state.get("mode", "chat")
        if modo in ("analyze", "suggest") and permission != "READ":
            return False, f"o modo {modo} permite somente ferramentas READ"
        if modo == "edit" and permission in ("WRITE", "EXEC") and not edit_habilitado:
            return False, "o modo edit do Agente esta desativado na configuracao"
        if not isinstance(tool, str) or not tool:
            return False, "a transicao precisa declarar uma ferramenta valida"
        plano = self.goal_state.get("plan") or []
        primeiro = plano[0].get("description", "") if plano else ""
        if (
            self.acoes_executadas == 0
            and "list_tree" in primeiro
            and tool != "list_tree"
        ):
            return False, "a analise geral precisa comecar por list_tree"
        return True, None

    def aplicar_replanejamento(self, atualizacao):
        """Aceita somente o gatilho explicito de hipotese negada vindo da LLM."""
        if atualizacao is None:
            return True, None
        if not isinstance(atualizacao, dict):
            return False, "goal_update precisa ser um objeto"
        gatilho = atualizacao.get("trigger")
        if gatilho != "hypothesis_denied":
            return False, "a LLM so pode replanejar por hypothesis_denied"
        if not self.evidencias_frescas():
            return False, "hypothesis_denied exige evidencia fresca observada"
        return GoalState.replanejar(
            self.goal_state,
            gatilho,
            atualizacao.get("detail") or "hipotese negada pela evidencia fresca",
            novo_plano=atualizacao.get("plan"),
            evidencias_necessarias=atualizacao.get("evidence_needed"),
        )

    def registrar_sem_progresso(self):
        self.decisoes_sem_progresso += 1
        return self.decisoes_sem_progresso

    def registrar_progresso(self):
        self.decisoes_sem_progresso = 0

    def _novo_evidence_id(self):
        identificador = f"ev-{self._proximo_evidence_id:04d}"
        self._proximo_evidence_id += 1
        return identificador

    def _novo_action_id(self):
        identificador = f"ac-{self._proximo_action_id:04d}"
        self._proximo_action_id += 1
        return identificador

    def _registrar_evidencia(self, source_tool, item):
        """Guarda uma leitura real completa ou reutiliza a mesma faixa/hash."""
        arquivo = item.get("arquivo")
        linha_inicio = item.get("linha_inicio")
        linha_fim = item.get("linha_fim")
        conteudo = item.get("trecho_numerado")
        conteudo_raw = item.get("conteudo")
        content_hash = item.get("content_hash")
        file_hash = item.get("file_hash")
        if not (
            isinstance(arquivo, str) and arquivo
            and isinstance(linha_inicio, int) and linha_inicio >= 1
            and isinstance(linha_fim, int) and linha_fim >= linha_inicio
            and isinstance(conteudo, str) and conteudo
            and isinstance(content_hash, str) and content_hash
        ):
            return None

        for evidencia in self.evidence:
            if (
                evidencia.get("arquivo") == arquivo
                and evidencia.get("linha_inicio") == linha_inicio
                and evidencia.get("linha_fim") == linha_fim
                and evidencia.get("content_hash") == content_hash
                and evidencia.get("file_hash") == file_hash
            ):
                evidencia["estado"] = "fresh"
                return evidencia["id"]

        evidencia = {
            "id": self._novo_evidence_id(),
            "source_tool": source_tool,
            "arquivo": arquivo,
            "linha_inicio": linha_inicio,
            "linha_fim": linha_fim,
            "conteudo": conteudo,
            "conteudo_raw": normalizar_quebras(conteudo_raw) if isinstance(conteudo_raw, str) else None,
            "content_hash": content_hash,
            "file_hash": file_hash,
            "estado": "fresh",
        }
        self.evidence.append(evidencia)
        return evidencia["id"]

    def registrar_acao(self, tool, arguments, resultado, contar_execucao=False):
        """Registra a acao compacta e extrai evidencias objetivas do envelope."""
        detalhe = resultado.get("detail") if isinstance(resultado, dict) else None
        evidence_ids = []
        if isinstance(resultado, dict) and resultado.get("ok") is True:
            if tool in ("read_range", "read_file", "find_symbol") and isinstance(detalhe, dict):
                identificador = self._registrar_evidencia(tool, detalhe)
                if identificador:
                    evidence_ids.append(identificador)
            elif tool == "search_code" and isinstance(detalhe, dict):
                for item in detalhe.get("resultados") or []:
                    if not isinstance(item, dict):
                        continue
                    identificador = self._registrar_evidencia(tool, item)
                    if identificador:
                        evidence_ids.append(identificador)

        acao = {
            "id": self._novo_action_id(),
            "tool": tool,
            "arguments": _resumir_argumentos(arguments),
            "status": resultado.get("status") if isinstance(resultado, dict) else None,
            "ok": resultado.get("ok") if isinstance(resultado, dict) else None,
            "executed": resultado.get("executed") if isinstance(resultado, dict) else None,
            "changed": resultado.get("changed") if isinstance(resultado, dict) else None,
            "error_code": resultado.get("error_code") if isinstance(resultado, dict) else None,
            "evidence_ids": evidence_ids,
        }
        if tool == "test_patch_dry_run" and acao["ok"] is True:
            acao["patch_fingerprint"] = _fingerprint_patch(arguments)
            acao["patch_spec"] = {
                chave: (arguments or {}).get(chave)
                for chave in (
                    "caminho_relativo", "linha_inicio", "linha_fim",
                    "codigo_novo", "file_hash_esperado", "range_hash_esperado",
                )
            }
        if contar_execucao:
            self.acoes_executadas += 1
            self.goal_state["actions_executed"] = self.acoes_executadas
            acao["action_number"] = self.acoes_executadas
            self.registrar_progresso()
            if evidence_ids:
                self.goal_state["evidence_needed"] = [
                    item for item in self.goal_state.get("evidence_needed", [])
                    if item != "codigo_fresco_relevante"
                ]
                if (
                    tool == "read_range"
                    and self.edit_state.get("arquivo") == (arguments or {}).get("caminho_relativo")
                    and self.edit_state.get("status") in ("tests_passed", "applied_without_suite")
                ):
                    self.edit_state["post_write_evidence_id"] = evidence_ids[-1]
                    self.goal_state["evidence_needed"] = [
                        item for item in self.goal_state.get("evidence_needed", [])
                        if item != "evidencia_pos_escrita"
                    ]
            self._avancar_plano_apos_acao(tool, resultado)
        self.actions.append(acao)
        return acao

    @staticmethod
    def _codigo_original_da_evidencia(evidencia, inicio, fim):
        raw = evidencia.get("conteudo_raw")
        if not isinstance(raw, str):
            return None
        relativo_inicio = inicio - evidencia.get("linha_inicio") + 1
        relativo_fim = fim - evidencia.get("linha_inicio") + 1
        linhas = normalizar_quebras(raw).split("\n")
        if relativo_inicio < 1 or relativo_fim < relativo_inicio or relativo_fim > len(linhas):
            return None
        return "\n".join(linhas[relativo_inicio - 1:relativo_fim])

    def _evidencia_para_patch(self, arquivo, inicio, fim):
        candidatas = [
            item for item in self.evidence
            if item.get("estado") == "fresh"
            and item.get("arquivo") == arquivo
            and isinstance(item.get("linha_inicio"), int)
            and isinstance(item.get("linha_fim"), int)
            and item.get("linha_inicio") <= inicio <= fim <= item.get("linha_fim")
            and isinstance(item.get("file_hash"), str)
        ]
        if not candidatas:
            return None
        # Prefere a menor faixa que cobre o patch: reduz ambiguidade.
        return min(
            candidatas,
            key=lambda item: item.get("linha_fim") - item.get("linha_inicio"),
        )

    def completar_argumentos_patch(self, tool, arguments):
        """Deriva hashes/original da evidencia; a LLM nao precisa copia-los.

        O modelo continua decidindo arquivo, faixa e codigo novo. Os dados de
        concorrencia (hashes) e o codigo original sao propriedade do sistema,
        evitando ``STALE_PATCH`` falso quando a leitura cobriu o arquivo inteiro
        mas o patch altera apenas algumas linhas.
        """
        argumentos = dict(arguments or {}) if isinstance(arguments, dict) else arguments
        if tool not in ("test_patch_dry_run", "apply_patch") or not isinstance(argumentos, dict):
            return argumentos
        try:
            inicio = int(argumentos.get("linha_inicio"))
            fim = int(argumentos.get("linha_fim"))
        except (TypeError, ValueError):
            return argumentos
        arquivo = argumentos.get("caminho_relativo")
        evidencia = self._evidencia_para_patch(arquivo, inicio, fim)
        if evidencia is None:
            return argumentos
        raw = evidencia.get("conteudo_raw")
        if not isinstance(raw, str):
            return argumentos
        relativo_inicio = inicio - evidencia.get("linha_inicio") + 1
        relativo_fim = fim - evidencia.get("linha_inicio") + 1
        range_hash = hash_faixa(raw, relativo_inicio, relativo_fim)
        if range_hash:
            argumentos["file_hash_esperado"] = evidencia.get("file_hash")
            argumentos["range_hash_esperado"] = range_hash
        if tool == "apply_patch":
            original = self._codigo_original_da_evidencia(evidencia, inicio, fim)
            if original is not None:
                argumentos["codigo_original_esperado"] = original
        return argumentos

    def validar_precondicoes_patch(self, arguments):
        """Exige leitura fresca cobrindo a faixa e dry-run da proposta exata."""
        arguments = arguments or {}
        arquivo = arguments.get("caminho_relativo")
        inicio = arguments.get("linha_inicio")
        fim = arguments.get("linha_fim")
        file_hash = arguments.get("file_hash_esperado")
        range_hash = arguments.get("range_hash_esperado")
        try:
            inicio = int(inicio)
            fim = int(fim)
        except (TypeError, ValueError):
            return False, "apply_patch exige faixa numerica valida"
        evidencia = self._evidencia_para_patch(arquivo, inicio, fim)
        if evidencia is not None:
            raw = evidencia.get("conteudo_raw")
            relativo_inicio = inicio - evidencia.get("linha_inicio") + 1
            relativo_fim = fim - evidencia.get("linha_inicio") + 1
            hash_derivado = (
                hash_faixa(raw, relativo_inicio, relativo_fim)
                if isinstance(raw, str) else None
            )
            if evidencia.get("file_hash") != file_hash or hash_derivado != range_hash:
                evidencia = None
        if evidencia is None:
            return False, "apply_patch exige leitura fresca cobrindo a faixa e os mesmos hashes"
        fingerprint = _fingerprint_patch(arguments)
        dry_run = next((
            item for item in reversed(self.actions)
            if item.get("tool") == "test_patch_dry_run"
            and item.get("ok") is True
            and item.get("executed") is True
            and item.get("patch_fingerprint") == fingerprint
        ), None)
        if dry_run is None:
            return False, "apply_patch exige dry-run bem-sucedido da proposta exata"
        return True, evidencia.get("id")

    def registrar_edicao_aplicada(self, arguments, resultado):
        detalhe = resultado.get("detail") if isinstance(resultado, dict) else {}
        detalhe = detalhe if isinstance(detalhe, dict) else {}
        self.edit_state = {
            "status": "applied_pending_tests",
            "arquivo": (arguments or {}).get("caminho_relativo"),
            "linha_inicio": (arguments or {}).get("linha_inicio"),
            "linha_fim_original": (arguments or {}).get("linha_fim"),
            "linha_fim_final": detalhe.get("linha_fim_final"),
            "file_hash_antes": detalhe.get("file_hash_antes"),
            "file_hash_depois": detalhe.get("file_hash_depois"),
            "range_hash_antes": detalhe.get("range_hash_antes"),
            "rollback_snapshot": detalhe.get("rollback_snapshot"),
            "test": None,
            "post_write_evidence_id": None,
        }
        necessarias = self.goal_state.setdefault("evidence_needed", [])
        for item in ("testes_reais", "evidencia_pos_escrita"):
            if item not in necessarias:
                necessarias.append(item)

    def registrar_rollback(self, resultado):
        ok = isinstance(resultado, dict) and resultado.get("ok") is True
        self.edit_state["rollback"] = resultado
        self.edit_state["status"] = "reverted" if ok else "rollback_failed"
        if ok:
            self.houve_escrita = False
            self.testes_ok_apos_escrita = False
        self.goal_state["status"] = "blocked"

    def validar_conclusao_edicao(self, evidence_ids):
        if not self.edit_state:
            return False, "nenhuma edicao confirmada foi aplicada"
        status = self.edit_state.get("status")
        if status == "applied_without_suite":
            return False, "alteracao aplicada sem suite disponivel"
        if status == "reverted":
            return False, "alteracao revertida por falha de teste"
        if status == "rollback_failed":
            return False, "teste falhou e o rollback tambem falhou"
        if status != "tests_passed":
            return False, "alteracao aplicada ainda aguarda verificacao real"
        evidence_id = self.edit_state.get("post_write_evidence_id")
        if not evidence_id or evidence_id not in (evidence_ids or []):
            return False, "sucesso de edicao exige releitura fresca da faixa final"
        evidencia = self.evidencia_por_id(evidence_id)
        if not evidencia or evidencia.get("estado") != "fresh":
            return False, "a evidencia pos-escrita nao esta fresca"
        if evidencia.get("arquivo") != self.edit_state.get("arquivo"):
            return False, "a releitura final pertence a outro arquivo"
        return True, "patch, teste executado e releitura final conferidos"

    def _avancar_plano_apos_acao(self, tool, resultado):
        """Atualiza o passo de forma deterministica; a LLM nao move o ponteiro."""
        if not isinstance(resultado, dict):
            ok = True
        else:
            ok = (
                resultado.get("ok") is not False
                and resultado.get("status") != "failed"
                and "erro" not in resultado
            )
        if not ok:
            codigo = resultado.get("error_code") if isinstance(resultado, dict) else None
            GoalState.replanejar(
                self.goal_state, "tool_failure",
                f"{tool} falhou ({codigo or 'TOOL_FAILED'})",
            )
            return

        plano = self.goal_state.get("plan") or []
        self.goal_state["status"] = "in_progress"
        if not plano:
            return
        indice = min(max(int(self.goal_state.get("current_step") or 1) - 1, 0), len(plano) - 1)
        plano[indice]["status"] = "completed"
        proximo = indice + 1
        if proximo < len(plano):
            plano[proximo]["status"] = "in_progress"
            self.goal_state["current_step"] = proximo + 1
        else:
            self.goal_state["current_step"] = len(plano)
        self.goal_state["replan_reason"] = None

    def marcar_evidencias_stale(self, caminho_relativo):
        """Invalida toda evidencia do arquivo alterado, sem apaga-la."""
        afetadas = []
        for evidencia in self.evidence:
            if evidencia.get("arquivo") == caminho_relativo:
                evidencia["estado"] = "stale"
                afetadas.append(evidencia.get("id"))
                self.liberar_releitura(evidencia)
        if caminho_relativo:
            self.assinaturas_chamadas.discard(
                self._montar_assinatura(
                    "read_file", {"caminho_relativo": caminho_relativo},
                )
            )
        if afetadas:
            necessarias = self.goal_state.setdefault("evidence_needed", [])
            if "codigo_fresco_relevante" not in necessarias:
                necessarias.append("codigo_fresco_relevante")
            GoalState.replanejar(
                self.goal_state, "file_changed",
                f"{caminho_relativo or 'arquivo'} mudou; evidencias {afetadas} ficaram stale",
            )
        return afetadas

    def marcar_concluido(self):
        self.goal_state["status"] = "completed"
        self.goal_state["evidence_needed"] = []
        for passo in self.goal_state.get("plan") or []:
            if passo.get("status") in ("pending", "in_progress"):
                passo["status"] = "completed"

    def liberar_releitura(self, evidencia):
        """Uma faixa stale pode e deve ser relida com os mesmos argumentos."""
        arguments = {
            "caminho_relativo": evidencia.get("arquivo"),
            "linha_inicio": evidencia.get("linha_inicio"),
            "linha_fim": evidencia.get("linha_fim"),
        }
        self.assinaturas_chamadas.discard(
            self._montar_assinatura("read_range", arguments)
        )

    def evidencia_por_id(self, evidence_id):
        for evidencia in self.evidence:
            if evidencia.get("id") == evidence_id:
                return evidencia
        return None

    def evidencias_frescas(self):
        return [item for item in self.evidence if item.get("estado") == "fresh"]

    def observar(self, tool, resultado):
        """
        Resume `resultado` (via _resumir_resultado, respeitando
        max_chars_por_observacao) e anexa {"tool": tool, "resumo": resumo}
        a self.observacoes. Nunca guarda o resultado cru.
        """
        resumo = _resumir_resultado(tool, resultado, max_chars=self.max_chars_por_observacao)
        entrada = {"tool": tool, "resumo": resumo}
        self.observacoes.append(entrada)
        return entrada

    # -----------------------------------------------------------------
    # Atualizacao 3 -- guarda de chamada repetida.
    #
    # O loop principal do Agente deve chamar chamada_repetida(...) ANTES
    # de executar qualquer tool. Se vier True, chama
    # observar_chamada_repetida(...) em vez de rodar a tool de novo. Se
    # vier False, executa normalmente e SO ENTAO chama
    # registrar_chamada(...) -- nunca antes de rodar (uma tentativa que
    # falhou na propria execucao nao deveria travar uma nova tentativa
    # legitima com os mesmos argumentos).
    # -----------------------------------------------------------------

    @staticmethod
    def _montar_assinatura(tool, arguments):
        """Assinatura estavel (tool, argumentos-em-JSON-canonico) usada
        para reconhecer a MESMA chamada de novo -- mesma tool, mesmos
        argumentos, independente da ordem das chaves do dict original."""
        try:
            args_serializados = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            args_serializados = str(arguments)
        return (tool, args_serializados)

    def chamada_repetida(self, tool, arguments):
        """True se essa (tool, arguments) exata ja foi EXECUTADA antes
        nesta tarefa. So consulta -- nunca registra nada sozinha."""
        return self._montar_assinatura(tool, arguments) in self.assinaturas_chamadas

    def registrar_chamada(self, tool, arguments):
        """Marca (tool, arguments) como ja executada nesta tarefa. So deve
        ser chamado depois que a tool rodou de verdade."""
        self.assinaturas_chamadas.add(self._montar_assinatura(tool, arguments))

    def observar_chamada_repetida(self, tool):
        """Anexa a observacao padrao de chamada repetida (mesmo formato de
        observar(): {"tool": ..., "resumo": ...}), sem executar a tool de
        novo nem mexer em assinaturas_chamadas -- so avisa que essa chamada
        exata ja foi feita, pra o proximo prompt do Agente (PROMPT_AGENTE,
        regra 8) direcionar o modelo a revisar as observacoes anteriores
        em vez de insistir."""
        resumo = (
            f"[chamada repetida] '{tool}' com os mesmos argumentos ja foi executada "
            "nesta tarefa -- reveja as observacoes anteriores em vez de repetir a chamada."
        )
        entrada = {"tool": tool, "resumo": resumo}
        self.observacoes.append(entrada)
        return entrada

    # -----------------------------------------------------------------
    # Atualizacao 11 -- circuit breaker de erro consecutivo.
    #
    # Guarda de repeticao (Atualizacao 3) so pega a MESMA (tool,
    # arguments) de novo -- um modelo pequeno que varia levemente o
    # argumento numa tentativa quebrada escapa dela. Este contador cobre
    # esse caso: conta qualquer erro de tool em sequencia, independente
    # de qual tool ou de quais argumentos, e zera assim que uma tool
    # roda sem erro. Quem decide o que fazer quando estoura o limite e'
    # o loop principal (engine/agent.py) -- esta classe so conta.
    # -----------------------------------------------------------------

    def registrar_resultado_tool(self, resultado):
        """Chamar depois de QUALQUER execucao de tool (WRITE ou READ),
        com o resultado cru (antes de resumir). Incrementa
        erros_consecutivos quando o resultado indica falha -- dict com
        chave "erro" (erro de EXECUCAO da tool, ex: argumento invalido --
        formato que engine/agent_tools.py:executar_tool ja usa) OU
        "ok": False (falha de NEGOCIO reportada pela propria tool, ex:
        apply_patch/run_tests/test_patch_dry_run quando o patch nao
        aplica ou os testes falham -- Atualizacao 16, corrigindo um
        buraco que a propria Atualizacao 11 tinha: uma escrita que falha
        repetidamente devolve {"ok": False}, sem chave "erro", e nao
        acionava o breaker antes desta correcao). Zera nos dois casos
        contrarios (tool rodou sem erro E sem "ok": False explicito)."""
        if isinstance(resultado, dict) and (
            resultado.get("status") == "failed"
            or resultado.get("ok") is False
            or "erro" in resultado  # compatibilidade com resultado legado
        ):
            self.erros_consecutivos += 1
        else:
            self.erros_consecutivos = 0

    # -----------------------------------------------------------------
    # Atualizacao 10 -- verificador de conclusao objetivo.
    #
    # houve_escrita/testes_ok_apos_escrita sao atualizados pelo loop
    # principal (engine/agent.py), que e' quem sabe a permission de cada
    # tool -- esta classe so guarda o estado. registrar_escrita() marca
    # que uma tool WRITE rodou (invalida qualquer verificacao anterior);
    # registrar_testes() marca se 'run_tests' passou.
    # -----------------------------------------------------------------

    def registrar_escrita(self):
        """Chamar depois que uma tool WRITE (hoje so' apply_patch)
        devolveu `changed=True`. Executar/confirmar sem alterar nao conta.
        Qualquer escrita real invalida a verificacao anterior --
        run_tests precisa rodar de novo depois dela."""
        self.houve_escrita = True
        self.testes_ok_apos_escrita = False

    def registrar_testes(self, resultado_run_tests):
        """Chamar depois que a tool 'run_tests' rodou. resultado_run_tests
        e' o envelope padrao devolvido por ela (`executed`, `ok`,
        `detail` -- Atualizacao 21).

        Atualizacao 17 (corrigindo a Atualizacao 10): testes_ok_apos_escrita
        so vira True quando 'executado' E 'ok' forem True -- nao mais so
        'ok'. Antes desta correcao, 'executado': False (testes desligados
        ou nao configurados no projeto) com 'ok': True bastava pra
        satisfazer o verificador, o que esvaziava a garantia inteira da
        Atualizacao 10: "final" podia ser aceito depois de uma escrita sem
        NENHUMA verificacao real ter rodado. Isso muda comportamento
        visivel -- projeto sem testes configurados, apos uma escrita, nao
        fecha mais sozinho em {"final": ...}; precisa de needs_user
        explicito ou esgota max_steps. E' intencional: e' exatamente o
        que "verificador objetivo" deveria significar."""
        if isinstance(resultado_run_tests, dict) and (
            resultado_run_tests.get("executed") is True
            or resultado_run_tests.get("executado") is True  # legado
        ) and resultado_run_tests.get("ok") is True:
            self.testes_ok_apos_escrita = True
            if self.edit_state:
                self.edit_state["status"] = "tests_passed"
                self.edit_state["test"] = {
                    "executed": True,
                    "ok": True,
                    "detail": resultado_run_tests.get("detail"),
                }
                self.goal_state["evidence_needed"] = [
                    item for item in self.goal_state.get("evidence_needed", [])
                    if item != "testes_reais"
                ]
        elif isinstance(resultado_run_tests, dict) and self.edit_state:
            self.edit_state["test"] = {
                "executed": resultado_run_tests.get("executed") is True,
                "ok": resultado_run_tests.get("ok") is True,
                "detail": resultado_run_tests.get("detail"),
                "error_code": resultado_run_tests.get("error_code"),
            }
            self.edit_state["status"] = (
                "tests_failed" if resultado_run_tests.get("executed") is True
                else "applied_without_suite"
            )

    def observar_final_sem_verificacao(self):
        """Anexa a observacao padrao quando um {"final": ...} e' recusado
        por causa da Atualizacao 10 (escreveu no projeto mas run_tests
        nao passou depois da ultima escrita) -- mesmo formato de
        observar()/observar_chamada_repetida(), pra o proximo prompt do
        Agente ver isso como qualquer outra observacao recente."""
        resumo = (
            "[final recusado] Voce tentou finalizar apos usar uma tool WRITE (apply_patch) "
            "sem rodar 'run_tests' com sucesso depois dela. Rode run_tests antes de responder "
            "{\"final\": ...} de novo."
        )
        entrada = {"tool": "final", "resumo": resumo}
        self.observacoes.append(entrada)
        return entrada

    def observar_final_sem_grounding(self, motivo):
        """Explica por que a conclusao de uma tarefa de projeto foi recusada."""
        resumo = f"[final recusado sem grounding] {motivo}"
        entrada = {"tool": "final", "resumo": resumo}
        self.observacoes.append(entrada)
        return entrada

    # -----------------------------------------------------------------
    # Atualizacao 12 -- fatos_importantes.
    #
    # Diferente de observacoes (resultado de tool, cortado por
    # max_entradas no prompt), fatos_importantes guarda o que a propria
    # LLM marcou como importante de lembrar -- e SEMPRE entra inteiro no
    # prompt (compiler.py:montar_prompt_agente), nunca cortado. Teto
    # max_fatos_importantes (FIFO) so' pra nao crescer sem limite numa
    # tarefa muito longa -- na pratica, um objetivo bem definido nao
    # deveria gerar mais que um punhado de fatos.
    # -----------------------------------------------------------------

    def registrar_fato(self, fato):
        """Registra um fato importante, se `fato` vier preenchido (chave
        opcional "fato_importante" na decisao da LLM -- ver
        PROMPT_AGENTE em llm/executar.py). Ignora silenciosamente valor
        vazio/None -- e' um campo opcional, nao um erro de formato."""
        fato = (fato or "").strip() if isinstance(fato, str) else ""
        if not fato:
            return
        self.fatos_importantes.append(fato)
        if len(self.fatos_importantes) > self.max_fatos_importantes:
            self.fatos_importantes = self.fatos_importantes[-self.max_fatos_importantes:]

    # -----------------------------------------------------------------
    # Fase 3 -- persistencia entre turnos (Atualizacao_Agente.md).
    #
    # So serializa o que ja existe: observacoes (ja resumidas, nunca cru)
    # e assinaturas_chamadas (cada uma e' uma tupla (tool, args_json) --
    # vira lista de 2 elementos em JSON, e volta a tupla em from_dict, pra
    # bater com o que _montar_assinatura devolve). Nao serializa
    # max_chars_por_observacao como fonte de verdade -- quem chama
    # from_dict sempre passa a `config` atual, e essa config manda; o
    # valor salvo e' so um retrato de quando a tarefa pausou.
    # -----------------------------------------------------------------

    def to_dict(self):
        """Devolve um dict serializavel em JSON com tudo que este estado
        guarda agora -- pronto para o checkpoint duravel da tarefa."""
        return {
            "goal_state": self.goal_state,
            "evidence": self.evidence,
            "actions": self.actions,
            "recent_observations": self.recent_observations,
            "assinaturas_chamadas": [list(assinatura) for assinatura in self.assinaturas_chamadas],
            "fatos_importantes": self.fatos_importantes,  # Atualizacao 12
            "erros_consecutivos": self.erros_consecutivos,  # Atualizacao 11
            "houve_escrita": self.houve_escrita,  # Atualizacao 10
            "testes_ok_apos_escrita": self.testes_ok_apos_escrita,  # Atualizacao 10
            "acoes_executadas": self.acoes_executadas,
            "decisoes_sem_progresso": self.decisoes_sem_progresso,
            "edit_state": self.edit_state,
            "proximo_evidence_id": self._proximo_evidence_id,
            "proximo_action_id": self._proximo_action_id,
        }

    @classmethod
    def from_dict(cls, dados, config=None):
        """Reconstroi um AgentState a partir do que to_dict() devolveu.
        `config` e' a config ATUAL (pode ter mudado desde que a tarefa
        pausou) -- max_chars_por_observacao vem dela, nao do dict salvo.
        Campos das Atualizacoes 10-12 usam .get(..., default) pra ler sem
        quebrar um agent_pendente.json salvo por uma versao anterior a
        elas (retrocompatibilidade -- mesmo espirito do resto da Eyle)."""
        estado = cls(config=config)
        dados = dados or {}
        estado.goal_state = dict(dados.get("goal_state") or {})
        estado.evidence = list(dados.get("evidence") or [])
        estado.actions = list(dados.get("actions") or [])
        estado.observacoes = list(
            dados.get("recent_observations") or dados.get("observacoes") or []
        )
        estado.assinaturas_chamadas = {
            tuple(assinatura) for assinatura in dados.get("assinaturas_chamadas") or []
        }
        estado.fatos_importantes = list(dados.get("fatos_importantes") or [])
        estado.erros_consecutivos = dados.get("erros_consecutivos", 0)
        estado.houve_escrita = dados.get("houve_escrita", False)
        estado.testes_ok_apos_escrita = dados.get("testes_ok_apos_escrita", False)
        estado.acoes_executadas = int(
            dados.get("acoes_executadas")
            or (estado.goal_state or {}).get("actions_executed")
            or sum(1 for item in estado.actions if item.get("action_number"))
        )
        estado.decisoes_sem_progresso = int(dados.get("decisoes_sem_progresso") or 0)
        estado.edit_state = dict(dados.get("edit_state") or {})
        estado._proximo_evidence_id = int(
            dados.get("proximo_evidence_id") or (len(estado.evidence) + 1)
        )
        estado._proximo_action_id = int(
            dados.get("proximo_action_id") or (len(estado.actions) + 1)
        )
        return estado
