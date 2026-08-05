#!/usr/bin/env python3
"""Roteador leve da Eyle 2.7.4.

Existem somente dois caminhos publicos: ``chat`` e ``agente``. O modo interno
do agente (analyze/suggest/edit) e derivado da intencao do pedido.
"""
import difflib
import re

PALAVRAS_ENGENHARIA = {
    "edite", "editar", "edita", "crie", "criar", "cria", "implemente",
    "implementar", "implementa", "corrija", "corrigir", "corrige",
    "refatore", "refatorar", "refatora", "adicione", "adicionar",
    "adiciona", "remova", "remover", "remove", "delete", "deletar",
    "apague", "apagar", "gere", "gerar", "gera", "escreva", "escrever",
    "reescreva", "reescrever", "atualize", "atualizar", "atualiza",
    "modifique", "modificar", "modifica", "conserte", "consertar",
    "resolva esse bug", "resolver esse bug", "faça um patch", "patch",
    "commit", "pull request", "faça essa mudança", "aplique essa mudança",
}

PALAVRAS_DICAS = {
    "dica", "dicas", "sugestao", "sugestões", "sugestoes",
    "sugira", "sugerir", "sugere", "conselho", "conselhos",
    "recomendacao", "recomendação", "recomendacoes", "recomendações",
    "recomende", "recomendar", "recomenda", "o que voce sugere",
    "o que você sugere", "o que voce recomenda", "o que você recomenda",
    "de uma dica", "dê uma dica", "de dicas", "dê dicas", "me da uma dica",
    "me dá uma dica", "alguma sugestao", "alguma sugestão",
}

PALAVRAS_CONSULTA = {
    "onde está", "onde fica", "onde esta", "localizar", "localize",
    "encontrar", "encontre", "qual arquivo", "quais arquivos",
    "como funciona", "o que faz", "pra que serve", "para que serve",
    "estrutura do projeto", "quais funções", "quais funcoes", "liste",
    "listar", "mostre", "mostrar", "explique o projeto",
    "explique esse projeto", "resumo do projeto", "analise esse projeto",
    "analisar esse projeto", "o que esse projeto faz",
    "analyze the project", "analyse the project", "review the project",
    "inspect the project", "audit the project", "explain the project",
    "project overview", "project structure", "what does this project do",
}

# Pedidos informais tipo "da uma olhada no projeto", "confere o codigo",
# "analisa o projeto pra mim" nao batem em nenhuma frase fixa de
# PALAVRAS_CONSULTA (que sao mais especificas) nem mencionam um
# arquivo/simbolo concreto -- sem essa checagem caiam no chat, que nao tem
# acesso a memoria do projeto e so pode responder pedindo pro usuario
# descrever tudo de novo. Em vez de listar frase por frase (frágil, sempre
# vai faltar uma variacao), detecta por TOKEN: um verbo de inspecao +
# um substantivo que se refere ao projeto, em qualquer ordem/conjugacao.
VERBOS_INSPECAO = {
    "olhar", "olha", "olhe", "olhada",
    "ver", "veja", "vê", "ve",
    "confira", "confere", "conferir",
    "verifica", "verifique", "verificar",
    "cheque", "checa", "checar",
    "revisa", "revise", "revisar",
    "examina", "examine", "examinar",
    "analisa", "analise", "analisar",
    "resume", "resuma", "resumir", "resumo",
    "inspeciona", "inspecione", "inspecionar",
    "avalia", "avalie", "avaliar",
    "analyze", "analyse", "review", "inspect", "examine", "check",
    "summarize", "evaluate", "audit",
}

SUBSTANTIVOS_PROJETO = {
    "projeto", "codigo", "código", "repositorio", "repositório", "repo",
    "aplicacao", "aplicação", "app", "sistema",
    "project", "code", "repository", "repo", "application", "system",
}

# Perguntas tipo "o que TEM/FAZ/TA no projeto" nao usam nenhum verbo de
# VERBOS_INSPECAO (nao tem "olha"/"confere"/etc), mas ainda sao pedido de
# panorama geral, nao conversa livre.
PALAVRAS_CONTEUDO = {
    "tem", "ta", "tá", "esta", "está", "faz", "existe", "contem", "contém", "ha", "há",
}

# No painel da Eyle, pedidos curtissimos como "faça a análise" normalmente
# se referem ao projeto indexado. Antes eles caiam em chat e a LLM respondia
# que nao tinha contexto, mesmo com o projeto carregado.
_RE_ANALISE_CURTA = re.compile(
    r"^(?:por\s+favor\s+)?(?:faça|faca|faz|faça-me|faca-me)?\s*"
    r"(?:(?:a|uma)\s+)?(?:análise|analise|avaliação|avaliacao)"
    r"(?:\s+(?:do|desse|deste)\s+projeto)?(?:\s+pra\s+mim)?[.!?]*$"
    r"|^(?:please\s+)?(?:analyze|analyse|review|inspect|audit|evaluate)"
    r"(?:\s+(?:the|this|a))?\s*(?:project|codebase|repository|repo)?[.!?]*$",
    re.IGNORECASE,
)


_RE_PALAVRA = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _normalizar(texto):
    return texto.strip().lower()


def _distancia_edicao_max1(a, b):
    """True se a e b tem distancia de Levenshtein <= 1 (aceita 1 letra a
    mais/a menos/trocada). Usado so' contra listas curtas e controladas
    (verbos/substantivos de inspecao), entao o risco de falso positivo e
    baixo -- e resolve erros de digitacao comuns tipo 'verfique' (falta
    uma letra) que um match exato nunca pegaria."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        novo = [i] + [0] * lb
        for j in range(1, lb + 1):
            custo = 0 if a[i - 1] == b[j - 1] else 1
            novo[j] = min(dp[j] + 1, novo[j - 1] + 1, dp[j - 1] + custo)
        dp = novo
    return dp[lb] <= 1


def _bate_com_tolerancia(tokens, palavras, tamanho_minimo=4):
    """Igual a 'tokens & palavras', mas tambem aceita erro de digitacao de
    1 letra em palavras com tamanho_minimo+ caracteres (evita falso
    positivo em palavras curtas, onde 1 letra de diferenca muda o
    sentido)."""
    if tokens & palavras:
        return True
    for tok in tokens:
        if len(tok) < tamanho_minimo:
            continue
        for palavra in palavras:
            if len(palavra) < tamanho_minimo:
                continue
            if _distancia_edicao_max1(tok, palavra):
                return True
    return False


def _contem_frase(texto_norm, frases):
    """Match por FRASE INTEIRA com fronteira de palavra, nao substring cru.

    Bug corrigido: 'frase in texto_norm' batia dentro de qualquer palavra que
    contivesse a frase como substring -- ex: 'cria' (PALAVRAS_ENGENHARIA)
    batia em 'criatividade', 'app' bateria em 'aplicativo' por acidente, etc.
    re.search com \\b nas duas pontas resolve isso mantendo frases de mais de
    uma palavra funcionando igual (\\b tambem vale nos espacos internos)."""
    for frase in frases:
        padrao = r"\b" + re.escape(frase) + r"\b"
        if re.search(padrao, texto_norm):
            return True
    return False


def _menciona_arquivo_ou_simbolo(texto_norm, estrutura, entendimento):
    """Match rapido (sem BM25) contra nomes de arquivo/simbolo ja conhecidos."""
    if not estrutura:
        return False
    tokens = set(_RE_PALAVRA.findall(texto_norm))
    if not tokens:
        return False
    for caminho, info in estrutura.items():
        base = caminho.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if base and base in tokens:
            return True
        for simbolo in info.get("funcoes_classes", []):
            if simbolo.lower() in tokens:
                return True
    return False


def _pede_inspecao_projeto(texto_norm):
    """Verbo de inspecao + substantivo de projeto, em qualquer ordem
    (ex: 'da uma olhada no projeto', 'confere o codigo', 'analisa o
    projeto pra mim') -- generaliza melhor que listar frase por frase.

    Isso nao e uma pergunta especifica. O roteador apenas reconhece que o
    pedido exige contexto do projeto e envia tudo ao unico tipo publico
    ``agente``; o proprio agente decide listar, buscar ou ler arquivos."""
    tokens = set(_RE_PALAVRA.findall(texto_norm))
    if not tokens:
        return False
    tem_substantivo = _bate_com_tolerancia(tokens, SUBSTANTIVOS_PROJETO)
    if not tem_substantivo:
        return False
    if _bate_com_tolerancia(tokens, VERBOS_INSPECAO):
        return True
    # sem verbo de inspecao explicito (nem com tolerancia a erro de
    # digitacao), mas ainda bate o padrao "o que tem/faz/ta/existe no
    # projeto" -- tambem e' pedido de panorama geral, nao conversa livre.
    return bool(tokens & PALAVRAS_CONTEUDO)


def pede_auditoria_projeto(texto):
    """True para pedidos gerais que exigem cobertura minima do projeto.

    Consultas com arquivo literal continuam ``project_read``. O novo tipo
    ``project_audit`` e reservado a pedidos de panorama/analise do projeto
    inteiro, incluindo a forma curta "faça a analise" quando existe projeto.
    """
    texto_norm = _normalizar(str(texto or ""))
    if re.search(
        r"[\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|yml|yaml)\b",
        texto_norm, re.IGNORECASE,
    ):
        return False
    return bool(
        _RE_ANALISE_CURTA.match(texto_norm)
        or _pede_inspecao_projeto(texto_norm)
        or _contem_frase(texto_norm, {
            "explique o projeto", "explique esse projeto", "resumo do projeto",
            "analise esse projeto", "analisar esse projeto",
            "o que esse projeto faz", "estrutura do projeto",
            "analyze the project", "analyse the project", "review the project",
            "inspect the project", "audit the project", "summarize the project",
            "explain the project", "project overview", "project structure",
            "what does this project do",
        })
    )


# Atualizacao Agente / Fase 2 -- vocabulario que indica tarefa MULTI-PASSO
# (algo que precisa de investigacao/execucao encadeada -- ler, editar,
# rodar teste, ajustar de novo -- em vez de uma mudanca pontual de texto
# pontual. So importa
# quando combinado com PALAVRAS_ENGENHARIA (ver classificar_pergunta) --
# sozinho, "e depois" nao significa nada.
PALAVRAS_MULTIPASSO = {
    "e depois", "e entao", "e então", "e em seguida", "e so depois",
    "e só depois", "ate passar", "até passar", "ate os testes passarem",
    "até os testes passarem", "e roda os testes", "e rode os testes",
    "e roda o teste", "e testa", "e teste", "e depois roda",
    "e depois testa", "e verifica se passa", "passo a passo",
    "varios passos", "vários passos", "e confirma que passou",
}


def _pede_tarefa_multipasso(texto_norm):
    """True se a mensagem indica uma tarefa encadeada (mais de uma acao em
    sequencia), nao so uma mudanca pontual -- ver PALAVRAS_MULTIPASSO."""
    return _contem_frase(texto_norm, PALAVRAS_MULTIPASSO)


def _pede_analise_curta(texto_norm):
    return bool(_RE_ANALISE_CURTA.fullmatch((texto_norm or "").strip()))


def classificar_modo_projeto(pergunta):
    """Classifica a intencao interna do Agente unificado (Atualizacao 44).

    O roteador de alto nivel decide apenas entre conversa livre e Agente.
    Esta funcao conserva a diferenca operacional que antes vivia em quatro
    pipelines: leitura/explicacao (``analyze``), sugestao sem escrita
    (``suggest``) e mudanca real (``edit``).
    """
    texto_norm = _normalizar(pergunta or "")
    if _contem_frase(texto_norm, PALAVRAS_ENGENHARIA):
        return "edit"
    if _contem_frase(texto_norm, PALAVRAS_DICAS) or re.search(
        r"\b(melhorar|melhoria|melhorias)\b", texto_norm,
    ):
        return "suggest"
    return "analyze"


def classificar_pergunta(pergunta, estrutura=None, entendimento=None, agent_habilitado=True):
    """Decide somente entre conversa livre e a unica Eyle agente.

    A flag ``agent_habilitado`` permanece na assinatura por compatibilidade de
    API, mas 2.7.4 nao possui pipeline alternativo para tarefas de projeto.
    """
    texto_norm = _normalizar(pergunta or "")

    if _pede_analise_curta(texto_norm):
        return "agente", "pedido curto de analise encaminhado a Eyle"
    if _contem_frase(texto_norm, PALAVRAS_ENGENHARIA):
        return "agente", "pedido de criacao ou edicao encaminhado a Eyle"
    if _contem_frase(texto_norm, PALAVRAS_DICAS):
        return "agente", "pedido de sugestao sobre o projeto encaminhado a Eyle"
    if _contem_frase(texto_norm, PALAVRAS_CONSULTA):
        return "agente", "pedido de leitura ou explicacao encaminhado a Eyle"
    if _menciona_arquivo_ou_simbolo(texto_norm, estrutura or {}, entendimento or {}):
        return "agente", "arquivo ou simbolo conhecido encaminhado a Eyle"
    if _pede_inspecao_projeto(texto_norm):
        return "agente", "pedido de inspecao encaminhado a Eyle"

    tokens = set(_RE_PALAVRA.findall(texto_norm))
    if tokens and _bate_com_tolerancia(tokens, SUBSTANTIVOS_PROJETO):
        return "agente", "mensagem menciona o projeto"
    return "chat", "mensagem nao depende do projeto"


# ---------------------------------------------------------------------------
# Atualizacao 5 -- resposta a uma proposta de patch pendente
# ---------------------------------------------------------------------------
# So faz sentido chamar isto quando ja existe uma proposta pendente
# (context/proposta_pendente.json) -- engine/engine.py checa isso ANTES de
# rotear a mensagem normalmente, pra "sim" nao virar uma pergunta de chat
# aleatoria quando na verdade e a resposta a "aplico essa mudanca?".

PALAVRAS_CONFIRMACAO_PATCH = {
    "sim", "aplica", "aplicar", "aplique", "confirmo", "confirma", "confirmar",
    "pode aplicar", "manda ver", "ok aplica", "ok, aplica", "beleza aplica",
    "aplica sim",
}
# "isso" e "exato" foram removidas -- sao palavras genericas do dia a dia
# ("quanto custa isso", "isso mesmo que eu disse antes") e causavam falso
# positivo de confirmacao em mensagens sem nenhuma intencao de aplicar nada,
# mesma classe de bug que o "para" solto causava no cancelamento.

PALAVRAS_CANCELAMENTO_PATCH = {
    "nao", "não", "cancela", "cancelar", "cancele", "esquece", "deixa pra la",
    "deixa quieto", "deixa la", "pera", "espera", "nao aplica",
    "não aplica", "ainda nao", "ainda não", "nao ainda", "melhor nao",
    "melhor não", "para com isso", "para tudo", "pare",
}


def detectar_resposta_proposta(pergunta):
    """
    So chamado quando ja existe uma proposta de patch pendente (Atualizacao
    5). Decide se a mensagem atual e uma confirmacao ('sim'/'aplica') ou um
    cancelamento ('nao'/'cancela') dela.

    Bug corrigido: as listas continham palavras genericas demais do
    portugues do dia a dia -- a preposicao solta "para" (cancelamento) e
    "pode"/"vai"/"faz"/"isso"/"exato" (confirmacao). Qualquer mensagem
    contendo essas palavras por acaso, mesmo confirmando explicitamente
    algo diferente ("aplica isso para o arquivo principal"), virava um
    cancelamento silencioso da proposta pendente. As listas agora so tem
    frases com intencao clara de confirmar/cancelar.

    Cancelamento continua sendo checado PRIMEIRO, de proposito: frases
    como "nao aplica"/"ainda nao" contem a palavra "aplica" (confirmacao)
    dentro delas, entao teriam prioridade errada se a ordem fosse
    invertida -- isso e' esperado e nao e' a mesma coisa que o bug
    original (que vinha de palavras soltas de alta frequencia sem nenhuma
    relacao com a proposta, nao de uma frase de cancelamento conter uma
    palavra de confirmacao por construcao).

    Devolve 'aplicar' | 'cancelar' | None (None = a mensagem nao parece
    resposta a proposta; engine/engine.py segue o fluxo normal de
    roteamento e MANTEM a proposta pendente -- ela so e descartada com uma
    confirmacao/cancelamento explicito, ou substituida por uma proposta nova).
    """
    texto_norm = _normalizar(pergunta)
    if _contem_frase(texto_norm, PALAVRAS_CANCELAMENTO_PATCH):
        return "cancelar"
    if _contem_frase(texto_norm, PALAVRAS_CONFIRMACAO_PATCH):
        return "aplicar"
    return None
