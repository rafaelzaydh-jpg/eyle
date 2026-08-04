#!/usr/bin/env python3
"""
roteador.py
-----------
Classificador heuristico (0 chamadas de LLM) que decide o caminho de alto
nivel antes de retrieval/LLM. Desde a Atualizacao 44, quando o Agente esta
ativo, existem dois caminhos publicos:

    "chat"       -> conversa geral sem projeto
    "agente"     -> qualquer pedido reconhecido como relativo ao projeto

Dentro de "agente", ``classificar_modo_projeto`` escolhe analyze, suggest ou
edit. Os tipos historicos abaixo permanecem somente como fallback compativel
quando o Agente estiver desligado ou enquanto edit aguarda a Atualizacao 46:

    "chat"       -> conversa geral, nao precisa do projeto (Executor direto)
    "consulta"   -> pergunta especifica sobre o projeto, precisa buscar
                    (Retrieval -> Executor, sem Analista, sem retry)
    "dicas"      -> pede sugestao/opiniao sobre o projeto (Atualizacao 4):
                    usa o Modelo Interno (entendimento.json['arquivos']) pra
                    escolher componentes candidatos, le o codigo real deles
                    e so entao sugere -- nao aplica nenhuma mudanca
    "visao_geral"-> pedido generico tipo "da uma olhada no projeto"/"confere
                    o codigo" -- sem termo especifico pra buscar, entao NAO
                    usa retrieval; monta o panorama direto de
                    estrutura.json/entendimento.json (Executor direto)
    "engenharia" -> pede mudanca real (editar/criar/corrigir codigo),
                    precisa do pipeline completo (Retrieval -> Analista ->
                    Executor -> Verify, com retry)

Isso resolve o gargalo principal descrito no plano de otimizacao: hoje
TODA mensagem roda o pipeline pesado, mesmo um "oi" ou "quanto e 2+2".

Atualizacao 5 acrescenta um mecanismo separado (nao um tipo de pipeline):
detectar_resposta_proposta(...), usado so quando ja existe uma proposta de
patch pendente -- decide se a mensagem atual e uma confirmacao ('sim') ou
cancelamento ('nao') dela, ANTES de classificar_pergunta(...) rodar.
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
}

SUBSTANTIVOS_PROJETO = {
    "projeto", "codigo", "código", "repositorio", "repositório", "repo",
    "aplicacao", "aplicação", "app", "sistema",
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
    r"(?:\s+(?:do|desse|deste)\s+projeto)?(?:\s+pra\s+mim)?[.!?]*$",
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

    Isso NAO e uma pergunta especifica (nao ha palavra-chave pra buscar no
    codigo): a palavra 'olhada'/'projeto' nao aparece no conteudo indexado,
    entao rodar retrieval/buscar.py com o texto literal da pergunta so
    devolveria ruido. Por isso vira seu proprio tipo ('visao_geral'), que
    o engine trata SEM retrieval -- monta o panorama direto de
    estrutura.json/entendimento.json."""
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


# Atualizacao Agente / Fase 2 -- vocabulario que indica tarefa MULTI-PASSO
# (algo que precisa de investigacao/execucao encadeada -- ler, editar,
# rodar teste, ajustar de novo -- em vez de uma mudanca pontual de texto
# unica que o pipeline 'engenharia' de sempre ja resolve bem). So importa
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


def classificar_pergunta(pergunta, estrutura=None, entendimento=None, agent_habilitado=False):
    """
    Devolve ('chat' | 'agente', motivo) quando ``agent_habilitado=True``.
    Com a flag desligada, preserva os tipos legados consulta/dicas/
    visao_geral/engenharia como fallback compativel.
    Heuristico e conservador: na duvida entre consulta/engenharia, prefere
    engenharia (pipeline completo) para nao arriscar aplicar mudanca sem
    o Analista/Verify. Na duvida entre chat/consulta, prefere consulta
    quando ha match de arquivo/simbolo conhecido. 'dicas' fica entre
    engenharia e consulta: pede opiniao/sugestao (nao aplica nada), mas
    precisa ler codigo real (mais do que uma consulta simples) -- se a
    mensagem tambem pede uma mudanca explicita (PALAVRAS_ENGENHARIA), essa
    checagem roda primeiro e ganha.

    agent_habilitado espelha config.json['agent']['enabled']. Na configuracao
    2.4 fica True com apenas analyze/suggest em enabled_modes; edit entra no
    mesmo ponto de entrada e usa fallback interno ate a Atualizacao 46.
    """
    texto_norm = _normalizar(pergunta)

    if _pede_analise_curta(texto_norm):
        if agent_habilitado:
            return (
                "agente",
                "pedido curto de analise encaminhado ao Agente Eyle no modo analyze",
            )
        return "visao_geral", "pedido curto de analise geral do projeto"

    if _contem_frase(texto_norm, PALAVRAS_ENGENHARIA):
        if agent_habilitado:
            return (
                "agente",
                "pedido sobre projeto encaminhado ao Agente Eyle no modo edit",
            )
        return "engenharia", "mensagem pede uma mudanca no codigo/projeto"

    if _contem_frase(texto_norm, PALAVRAS_DICAS):
        if agent_habilitado:
            return (
                "agente",
                "pedido sobre projeto encaminhado ao Agente Eyle no modo suggest",
            )
        return "dicas", "mensagem pede sugestao/opiniao sobre o projeto"

    if _contem_frase(texto_norm, PALAVRAS_CONSULTA):
        if agent_habilitado:
            return (
                "agente",
                "pedido sobre projeto encaminhado ao Agente Eyle no modo analyze",
            )
        return "consulta", "mensagem pergunta sobre o projeto (leitura/explicacao)"

    if _menciona_arquivo_ou_simbolo(texto_norm, estrutura, entendimento):
        if agent_habilitado:
            return (
                "agente",
                "arquivo/simbolo conhecido encaminhado ao Agente Eyle no modo analyze",
            )
        return "consulta", "mensagem menciona um arquivo/simbolo conhecido do projeto"

    if _pede_inspecao_projeto(texto_norm):
        if agent_habilitado:
            return (
                "agente",
                "analise geral do projeto encaminhada ao Agente Eyle no modo analyze",
            )
        return "visao_geral", "mensagem pede uma olhada/analise geral do projeto (sem termo especifico pra buscar)"

    # Atualizacao 13: rede de seguranca. Nenhuma categoria acima bateu, mas
    # a mensagem menciona um substantivo de projeto (ex: "como melhorar o
    # PROJETO", "3 caminhos para o CODIGO") -- sem verbo de inspecao
    # reconhecido, entao nao virou "visao_geral" acima. Cair em "chat" aqui
    # seria responder sem NENHUM contexto do projeto com a mesma confianca
    # de uma resposta grounded -- foi exatamente isso que gerou a
    # auto-descricao inventada do projeto (ver caso real documentado em
    # Atual_Versao.md). Preferir "visao_geral" custa uma leitura de
    # estrutura.json/entendimento.json a mais; "chat" errado aqui custa uma
    # resposta fabricada que o usuario nao tem como distinguir de uma
    # resposta real.
    tokens = set(_RE_PALAVRA.findall(texto_norm))
    if tokens and _bate_com_tolerancia(tokens, SUBSTANTIVOS_PROJETO):
        if agent_habilitado:
            return (
                "agente",
                "mencao ao projeto encaminhada ao Agente Eyle no modo analyze",
            )
        return (
            "visao_geral",
            "mensagem nao bateu em nenhuma categoria especifica, mas menciona o projeto -- "
            "prefere dar contexto a arriscar resposta sem grounding (rede de seguranca)",
        )

    return "chat", "mensagem nao parece precisar do contexto do projeto"


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
