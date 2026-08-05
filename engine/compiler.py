#!/usr/bin/env python3
"""
compiler.py
-----------
Monta os dois prompts usados pelas duas personalidades da LLM
(Analista e Executor) a partir da mesma memoria. Nao decide nada e
nao chama a LLM -- so formata texto dentro do orcamento de contexto.
Isso e o "Compilador de Contexto" do plano.

    montar_prompt_analista(...)  -> pede pro Analista decidir o que ler
    montar_prompt_executor(...)  -> monta o contexto final pro Executor
"""
import json

from engine.context_engine import (
    calcular_orcamento_evidencias,
    selecionar_evidencias,
)


def _compactar_catalogo_tools(catalogo_tools):
    """Projecao pequena do schema real, sem perder nome/tipo/limite/saida."""
    compacto = []
    for tool in catalogo_tools or []:
        schema = tool.get("input_schema") or {}
        obrigatorios = set(schema.get("required") or [])
        argumentos = {}
        for nome, contrato in (schema.get("properties") or {}).items():
            tipo = contrato.get("type", "any")
            if "minimum" in contrato:
                tipo += f">={contrato['minimum']}"
            if nome in obrigatorios:
                tipo += "!"
            argumentos[nome] = tipo
        saida = str(tool.get("output_schema") or "")
        for prefixo in ("Standard envelope; ", "Envelope padrao; "):
            if saida.startswith(prefixo):
                saida = saida[len(prefixo):]
                break
        compacto.append({
            "name": tool.get("name"),
            "description": tool.get("description"),
            "permission": tool.get("permission"),
            "arguments": argumentos,
            "limits": tool.get("limits") or {},
            "returns": saida,
        })
    return compacto


_MODEL_STATE_TEXT = {
    "resposta_direta": "direct_response",
    "sem_ferramentas_de_projeto": "no_project_tools",
    "Responder ao usuario": "Reply to the user",
    "codigo_fresco_lido": "fresh_code_read",
    "sugestoes_grounded": "grounded_suggestions",
    "sem_escrita": "no_write",
    "somente_leitura": "read_only",
    "uma_acao_por_decisao": "one_action_per_decision",
    "Ler o codigo fresco do alvo": "Read fresh code for the target",
    "Propor melhorias com evidencias": "Propose improvements grounded in evidence",
    "Localizar o componente relevante": "Locate the relevant component",
    "Ler o codigo fresco necessario": "Read the required fresh code",
    "codigo_fresco_relevante": "fresh_relevant_code",
    "confirmacao_explicita": "explicit_confirmation",
    "mudanca_verificada": "verified_change",
    "escrita_confirmada": "confirmed_write",
    "Localizar e ler o alvo": "Locate and read the target",
    "Preparar a mudanca": "Prepare the change",
    "Confirmar e aplicar": "Confirm and apply",
    "Testar e reler o resultado": "Test and re-read the result",
    "evidencia_pos_escrita": "post_write_evidence",
    "resposta_grounded": "grounded_answer",
    "Responder com evidencias": "Answer with evidence",
    "Mapear o projeto com list_tree": "Map the project with list_tree",
}


def _valor_para_modelo(valor):
    """Translate only deterministic state-machine text shown to the model.

    The original user objective and arbitrary user-provided blocker text remain
    untouched, so no translation layer can distort the request.
    """
    if isinstance(valor, dict):
        return {chave: _valor_para_modelo(item) for chave, item in valor.items()}
    if isinstance(valor, list):
        return [_valor_para_modelo(item) for item in valor]
    if isinstance(valor, str):
        return _MODEL_STATE_TEXT.get(valor, valor)
    return valor


def bloco_entendimento(entendimento):
    """
    Monta o bloco 'RESUMO DO PROJETO' a partir de memory/entendimento.json
    (o que cada componente FAZ), separado da estrutura fisica (o que EXISTE)
    e das evidencias (COMO se sabe disso). Devolve lista de linhas de texto.
    """
    partes = ["RESUMO DO PROJETO:"]

    componentes = (entendimento or {}).get("componentes", {})
    algum_com_funcao = False
    for nome, item in componentes.items():
        if item.get("funcao"):
            partes.append(f"- {nome}: {item['funcao']}")
            algum_com_funcao = True

    if not algum_com_funcao:
        return []

    return partes


def montar_prompt_entendedor(caminho_relativo, conteudo, max_chars=20000):
    """
    Monta o prompt que pede pra LLM ler um arquivo INTEIRO (uma unica vez,
    na ingestao) e devolver o retrato estrutural dele para o Modelo Interno
    do Projeto. Usado por engine/entender.py.

    Diferente de montar_prompt_analista/montar_prompt_executor (que rodam a
    CADA pergunta, dentro do orcamento de contexto da conversa), este prompt
    roda uma vez por ARQUIVO, na ingestao -- so de novo se o hash do arquivo
    mudar. max_chars e um limite proprio (arquivo inteiro, nao trechos),
    truncando arquivos muito grandes em vez de estourar o contexto da LLM.
    """
    truncado = False
    if len(conteudo) > max_chars:
        conteudo = conteudo[:max_chars]
        truncado = True

    partes = [
        f"ARQUIVO: {caminho_relativo}",
        "",
        "CONTEUDO DO ARQUIVO:",
        conteudo,
    ]
    if truncado:
        partes.append(f"\n[arquivo truncado em {max_chars} caracteres para caber no contexto -- analise o que foi mostrado]")

    partes.append(
        "\nResponda APENAS com um JSON, sem texto antes ou depois, no formato exato:\n"
        '{"tipo": "...", "responsabilidade": "...", "entrada": [...], "saida": [...], '
        '"depende_de": [...], "funcoes_principais": [...], "pontos_criticos": [...]}\n'
        "Regras:\n"
        "- \"tipo\": categoria curta do arquivo (ex: \"orquestrador\", \"utilitario\", \"rota_web\", \"modelo_de_dados\", \"configuracao\").\n"
        "- \"responsabilidade\": uma frase objetiva do que este arquivo faz.\n"
        "- \"entrada\": o que este arquivo recebe (parametros, payloads, arquivos lidos) -- lista curta.\n"
        "- \"saida\": o que este arquivo produz/retorna/escreve -- lista curta.\n"
        "- \"depende_de\": outros arquivos/modulos que este arquivo importa ou usa diretamente, com base nos imports reais do codigo.\n"
        "- \"funcoes_principais\": nomes das funcoes/classes mais importantes definidas aqui.\n"
        "- \"pontos_criticos\": riscos operacionais (ex: \"controla o pipeline principal\") E questoes arquiteturais "
        "(ex: \"alto acoplamento\", \"sem tratamento de erro\") -- pode ser lista vazia.\n"
        "Nao invente nada que nao esteja no conteudo do arquivo mostrado acima."
    )
    return "\n".join(partes)


def montar_prompt_visao_geral(pergunta, projeto=None, estrutura=None, entendimento=None,
                               decisoes=None, token_budget=1500, chars_per_token=4,
                               codigos_reais=None):
    """Monta uma visao geral estrutural ou uma analise completa de projeto pequeno.

    Quando ``codigos_reais`` esta presente, o Executor recebe o conteudo fresco
    dos arquivos e deve analisar comportamento, fluxo, riscos e melhorias. Para
    projetos maiores, conserva o panorama estrutural usado como fallback seguro.
    """
    partes = []
    if projeto:
        partes.append(
            f"IDENTIFICACAO DO PROJETO: {projeto.get('projeto')} "
            f"({projeto.get('arquivos')} arquivos, {projeto.get('tokens_estimados_totais')} tokens totais indexados)"
        )

    if entendimento:
        bloco = bloco_entendimento(entendimento)
        if bloco:
            partes.append("")
            partes.extend(bloco)

    if decisoes:
        partes.append("\nDECISOES ARQUITETURAIS CONHECIDAS:")
        for d in decisoes[:10]:
            partes.append(f"- {d.get('decisao')} (motivo: {d.get('motivo')})")

    partes.append(f"\nOBJETIVO: {pergunta}\n")

    orcamento_chars = token_budget * chars_per_token
    usado = sum(len(p) for p in partes)

    partes.append("MAPA DE ARQUIVOS DO PROJETO (arquivo (linhas): simbolos conhecidos):")
    if estrutura:
        omitidos = 0
        for arquivo, info in estrutura.items():
            info = info if isinstance(info, dict) else {}
            simbolos = info.get("funcoes_classes", [])
            linha = f"- {arquivo} ({info.get('linhas', '?')} linhas)"
            if simbolos:
                mostrados = simbolos[:12]
                linha += ": " + ", ".join(mostrados)
                if len(simbolos) > len(mostrados):
                    linha += f", +{len(simbolos) - len(mostrados)}"
            if usado + len(linha) > orcamento_chars:
                omitidos += 1
                continue
            partes.append(linha)
            usado += len(linha)
        if omitidos:
            partes.append(f"(+ {omitidos} arquivo(s) omitido(s) por orcamento de contexto)")
    else:
        partes.append("(projeto ainda nao foi indexado -- rode 'python main.py ingest <pasta>')")

    codigos_reais = codigos_reais or {}
    codigos_lidos = [
        (arquivo, info) for arquivo, info in codigos_reais.items()
        if isinstance(info, dict) and isinstance(info.get("conteudo"), str)
    ]
    if codigos_lidos:
        partes.append(
            "\nCODIGO REAL FRESCO DO PROJETO PEQUENO "
            "(analise o conteudo abaixo; nao diga que ele esta indisponivel):"
        )
        for arquivo, info in codigos_lidos:
            partes.append(f"\n--- {arquivo} ---")
            linhas = info["conteudo"].splitlines()
            partes.append("\n".join(
                f"{numero:>6} | {linha}"
                for numero, linha in enumerate(linhas, start=1)
            ))
            if info.get("truncado"):
                partes.append(
                    "[arquivo truncado pelo limite de contexto; analise o trecho mostrado "
                    "e declare essa limitacao]"
                )
        partes.append(
            "\nENTREGA OBRIGATORIA: explique o que o projeto faz, descreva o fluxo real, "
            "aponte problemas/riscos encontrados no codigo e sugira correcoes praticas. "
            "Nao reduza a resposta a contagem de arquivos ou linhas."
        )
    else:
        partes.append(
            "\nOBSERVACAO: isto e um panorama ESTRUTURAL (nomes de arquivo/funcao e contagem de "
            "linhas), sem o conteudo linha a linha de cada trecho. Descreva o projeto em nivel de "
            "arquivo/funcao. Nao invente numeros de linha especificos que nao foram dados aqui -- "
            "se precisar citar uma faixa exata, diga que precisa consultar o arquivo especifico "
            "primeiro."
        )
    return "\n".join(partes)


def montar_prompt_dicas(pergunta, candidatos, codigos, projeto=None, entendimento=None):
    """
    Atualizacao 4 -- monta o prompt do Sugestor: pergunta + Modelo Interno
    (por que cada candidato foi escolhido: match direto ou dependencia) +
    CODIGO REAL (arquivo inteiro, nao chunk) de cada componente candidato.

    candidatos: saida de engine/dicas.py:selecionar_componentes_candidatos
                (lista de {"arquivo", "score", "motivo"})
    codigos: saida de engine/dicas.py:ler_codigo_real
             (dict "arquivo" -> {"conteudo", "truncado"})
    """
    partes = []
    if projeto:
        partes.append(
            f"IDENTIFICACAO DO PROJETO: {projeto.get('projeto')} "
            f"({projeto.get('arquivos')} arquivos, {projeto.get('tokens_estimados_totais')} tokens totais indexados)"
        )

    if entendimento:
        bloco = bloco_entendimento(entendimento)
        if bloco:
            partes.append("")
            partes.extend(bloco)

    partes.append(f"\nOBJETIVO: {pergunta}\n")

    arquivos_info = (entendimento or {}).get("arquivos", {})
    partes.append(
        "COMPONENTES CANDIDATOS (escolhidos pelo Modelo Interno do Projeto -- "
        "tipo/responsabilidade/funcoes_principais/pontos_criticos, nao busca por palavra-chave):"
    )
    if not candidatos:
        partes.append("(nenhum componente candidato encontrado no Modelo Interno para esta pergunta)")

    for c in candidatos:
        arquivo = c["arquivo"]
        info = arquivos_info.get(arquivo, {})
        cabecalho = f"\n--- {arquivo} ({c['motivo']}) ---"
        partes.append(cabecalho)
        if info.get("tipo") or info.get("responsabilidade"):
            partes.append(f"tipo: {info.get('tipo', '?')} | responsabilidade: {info.get('responsabilidade', '?')}")
        if info.get("depende_de"):
            partes.append(f"depende_de: {', '.join(info['depende_de'])}")
        if info.get("pontos_criticos"):
            partes.append(f"pontos_criticos: {', '.join(info['pontos_criticos'])}")

        codigo = codigos.get(arquivo)
        if codigo and not codigo.get("erro"):
            partes.append("CODIGO REAL:")
            partes.append(codigo["conteudo"])
            if codigo["truncado"]:
                partes.append("[arquivo truncado para caber no contexto -- analise o que foi mostrado]")
        elif codigo and codigo.get("erro"):
            partes.append(f"(codigo nao lido por seguranca: {codigo['erro']})")
        else:
            partes.append("(codigo nao pode ser lido do disco -- arquivo pode ter sido removido desde o ultimo ingest)")

    partes.append(
        "\nResponda com sugestoes objetivas e fundamentadas SOMENTE no codigo real mostrado "
        "acima. Para cada sugestao, cite o arquivo (e a linha, se identificavel no codigo "
        "mostrado). Nao proponha nada que exija ver um arquivo que nao esta nos COMPONENTES "
        "CANDIDATOS -- se a sugestao depender de algo que nao esta aqui, diga isso em vez de "
        "supor. Voce esta SUGERINDO, nao aplicando nada -- nao gere um patch nem diga que a "
        "mudanca ja foi feita."
    )
    return "\n".join(partes)


def montar_prompt_engenheiro(pergunta, arquivo, simbolo, alvo, entendimento=None,
                              decisoes=None, impacto=None):
    """
    Atualizacao 5 -- monta o prompt do Engenheiro: pede o CODIGO NOVO
    COMPLETO de um simbolo (funcao/classe) especifico, ja localizado por
    linha_inicio/linha_fim no arquivo real (engine/codar.py:localizar_simbolo).
    Pede um recorte completo, nao um diff -- mais facil de aplicar
    (substituicao direta de linhas) e mais facil de um modelo pequeno
    gerar corretamente.

    alvo: saida de engine/codar.py:localizar_simbolo
          ({"linha_inicio", "linha_fim", "codigo_original", ...})
    impacto: saida de engine/codar.py:calcular_impacto
             (lista de {"arquivo", "responsabilidade"})
    """
    partes = []
    if entendimento:
        bloco = bloco_entendimento(entendimento)
        if bloco:
            partes.extend(bloco)
            partes.append("")

    info = (entendimento or {}).get("arquivos", {}).get(arquivo, {})
    if info:
        partes.append(f"MODELO INTERNO DE '{arquivo}':")
        if info.get("tipo") or info.get("responsabilidade"):
            partes.append(f"tipo: {info.get('tipo', '?')} | responsabilidade: {info.get('responsabilidade', '?')}")
        if info.get("pontos_criticos"):
            partes.append(f"pontos_criticos: {', '.join(info['pontos_criticos'])}")
        partes.append("")

    if impacto:
        partes.append("QUEM DEPENDE DESTE ARQUIVO (cuidado ao mudar assinatura/comportamento existente):")
        for i in impacto:
            partes.append(f"- {i['arquivo']}: {i.get('responsabilidade', '')}")
        partes.append("")

    if decisoes:
        partes.append("DECISOES ARQUITETURAIS CONHECIDAS:")
        for d in decisoes[:10]:
            partes.append(f"- {d.get('decisao')} (motivo: {d.get('motivo')})")
        partes.append("")

    partes.append(f"OBJETIVO: {pergunta}\n")
    partes.append(f"ARQUIVO ALVO: {arquivo}")
    partes.append(f"SIMBOLO ALVO: {simbolo} (linhas {alvo['linha_inicio']}-{alvo['linha_fim']} no arquivo atual)")
    partes.append("\nCODIGO REAL ATUAL DESTE SIMBOLO (lido agora, direto do arquivo):")
    partes.append(alvo["codigo_original"])

    partes.append(
        "\nResponda APENAS com um JSON, sem texto antes ou depois, no formato exato:\n"
        '{"resumo": "...", "codigo_novo": "...", "riscos": [...]}\n'
        "Regras:\n"
        "- \"codigo_novo\": o RECORTE COMPLETO E FINAL que deve substituir o codigo atual mostrado acima "
        "(a funcao/classe inteira, pronta para gravar no lugar -- NAO um diff, NAO \"...\" indicando partes "
        "omitidas). Preserve a indentacao/assinatura salvo se a mudanca pedida for justamente nisso.\n"
        "- \"resumo\": uma frase objetiva do que muda e por que.\n"
        "- \"riscos\": riscos que voce percebe NESTA mudanca especifica (pode ser lista vazia).\n"
        "Use APENAS o codigo real mostrado acima e o OBJETIVO pedido -- nao invente funcoes, campos ou "
        "comportamento que nao existem no restante do arquivo."
    )
    return "\n".join(partes)


def montar_texto_proposta(proposta):
    """
    Atualizacao 5 -- formata a proposta (Proposta + Impacto + Patch + Teste)
    num texto legivel pro usuario, terminando com o pedido de confirmacao
    explicita (se o teste passou) ou explicando por que nao pode ser
    aplicada como esta (se o teste falhou).

    proposta: dict montado em engine/engine.py:_tentar_gerar_proposta
    """
    p = proposta
    partes = [
        "PROPOSTA DE MUDANCA",
        f"Arquivo: {p['arquivo']}",
        f"Simbolo: {p['simbolo']} (linhas {p['linha_inicio']}-{p['linha_fim']})",
        f"Resumo: {p['resumo'] or '(sem resumo)'}",
    ]
    if p.get("riscos"):
        partes.append(f"Riscos apontados pelo Engenheiro: {', '.join(p['riscos'])}")

    partes.append(f"\n--- CODIGO ATUAL ({p['arquivo']}:{p['linha_inicio']}-{p['linha_fim']}) ---")
    partes.append(p["codigo_original"])
    partes.append("\n+++ CODIGO PROPOSTO +++")
    partes.append(p["codigo_novo"])

    partes.append("\nIMPACTO (arquivos que dependem deste, via depende_de invertido no Modelo Interno):")
    if p.get("impacto"):
        for i in p["impacto"]:
            partes.append(f"- {i['arquivo']}: {i.get('responsabilidade') or '(sem responsabilidade registrada)'}")
    else:
        partes.append("- nenhum arquivo do Modelo Interno declara depender deste (ou isso ainda nao foi mapeado)")

    teste = p.get("teste") or {}
    status_teste = "OK" if teste.get("ok") else "FALHOU"
    partes.append("\nTESTE (numa copia temporaria -- o arquivo real NAO foi tocado):")
    partes.append(f"[{status_teste}] {teste.get('detalhe', '(sem detalhe)')}")

    if teste.get("ok"):
        partes.append(
            "\nQuer que eu aplique essa mudanca de verdade no arquivo? Responda \"sim\" ou \"aplica\" para "
            "aplicar, ou mande qualquer outra mensagem para cancelar."
        )
    else:
        partes.append(
            "\nO teste na copia temporaria NAO passou, entao esta proposta nao fica pendente de confirmacao "
            "-- nao vou aplicar isto como esta. Peca a mudanca de novo ou ajuste manualmente."
        )

    return "\n".join(partes)


def montar_prompt_analista(pergunta, candidatos, estrutura=None, historico_relacionado=None,
                            evidencias=None, entendimento=None, iteracao=1, respostas_anteriores=None):
    """
    candidatos: lista de trechos no mesmo formato de atual['trechos'] (saida do retrieval)
    estrutura: dict "arquivo -> {linhas, funcoes_classes, ...}" de memory/estrutura.json
    historico_relacionado: lista de decisoes antigas relacionadas (de memory/historico.json)
    evidencias: lista de entidades relevantes de memory/evidencias.json
    iteracao: numero da rodada do ciclo de investigacao (1 na primeira chamada)
    respostas_anteriores: lista de decisoes (dict) do Analista em rodadas anteriores deste ciclo
    """
    partes = []
    if entendimento:
        bloco = bloco_entendimento(entendimento)
        if bloco:
            partes.extend(bloco)
            partes.append("")

    partes.append("OBJETIVO:")
    partes.append(pergunta)

    partes.append("\nCANDIDATOS (o retrieval encontrou isto; voce decide o que realmente importa):")
    if candidatos:
        for c in candidatos:
            id_trecho = f"{c['arquivo']}:{c.get('linhas', '?')}"
            cabecalho = f"- [{id_trecho}] {c['arquivo']}"
            if c.get("simbolo"):
                cabecalho += f" ({c['simbolo']})"
            cabecalho += f" linhas {c.get('linhas', '?')}"
            partes.append(cabecalho)
    else:
        partes.append("(nenhum candidato encontrado nesta rodada)")

    if estrutura:
        arquivos_candidatos = {c["arquivo"] for c in candidatos}
        relacoes = [
            f"- {arquivo}: {', '.join(estrutura[arquivo].get('funcoes_classes', []))}"
            for arquivo in arquivos_candidatos
            if arquivo in estrutura
        ]
        if relacoes:
            partes.append("\nRELACOES ESTRUTURAIS (arquivo -> funcoes/classes conhecidas):")
            partes.extend(relacoes)

    if evidencias:
        partes.append("\nEVIDENCIAS CONHECIDAS (entidade -> onde e definida/usada/validada):")
        for e in evidencias:
            partes.append(
                f"- {e.get('entity')}: definido em {e.get('defined_in')}, "
                f"usado por {e.get('used_by')}, validado por {e.get('validated_by')}"
            )

    if historico_relacionado:
        partes.append("\nDECISOES ANTERIORES RELACIONADAS:")
        for d in historico_relacionado:
            partes.append(f"- {d.get('data', '?')}: {d.get('decisao')} (motivo: {d.get('motivo')})")

    if iteracao > 1 and respostas_anteriores:
        partes.append(f"\nESTA E A RODADA {iteracao} DO CICLO DE INVESTIGACAO.")
        partes.append(
            "Na rodada anterior voce apontou informacao faltando em 'faltando'. Um novo retrieval "
            "direcionado ja rodou e os CANDIDATOS acima ja incluem o resultado dessa busca extra. "
            "Decida agora se ja e suficiente ou se ainda falta algo."
        )

    partes.append(
        "\nResponda APENAS com um JSON, sem texto antes ou depois, no formato:\n"
        '{"ler": [...], "ignorar": [...], "faltando": [...], "riscos": [...], "motivo": "..."}\n'
        "Regras:\n"
        "- \"ler\": IDs [arquivo:linhas] da lista de candidatos que realmente importam para o objetivo.\n"
        "- \"ignorar\": IDs [arquivo:linhas] da lista de candidatos que nao importam.\n"
        "- \"faltando\": nomes de arquivos/simbolos que voce precisaria ver e NAO estao nos candidatos "
        "(deixe vazio se ja tem o suficiente para decidir -- nao invente itens so para preencher).\n"
        "- \"riscos\": riscos que voce percebe na mudanca pedida (pode ser lista vazia).\n"
        "- \"motivo\": explicacao curta e objetiva da escolha.\n"
        "Voce NUNCA gera codigo e NUNCA responde ao usuario aqui -- so decide o que importa."
    )
    return "\n".join(partes)


def montar_prompt_executor(atual, projeto=None, evidencias=None, entendimento=None, decisoes=None):
    """Monta o contexto final enviado ao Executor, ja reduzido pelo Analista + retrieval.
    O Executor sempre recebe MEMORIA DO PROJETO (entendimento) + RESULTADO DO RETRIEVAL,
    nunca so o retrieval bruto -- e assim que perguntas conceituais (\"o que e X\",
    \"pra que serve Y\") deixam de cair em trechos de codigo sem relacao.
    """
    partes = []
    if projeto:
        partes.append(
            f"IDENTIFICACAO DO PROJETO: {projeto.get('projeto')} "
            f"({projeto.get('arquivos')} arquivos, {projeto.get('tokens_estimados_totais')} tokens totais indexados)"
        )

    if entendimento:
        bloco = bloco_entendimento(entendimento)
        if bloco:
            partes.append("")
            partes.extend(bloco)

    if decisoes:
        partes.append("\nDECISOES ARQUITETURAIS CONHECIDAS:")
        for d in decisoes:
            partes.append(f"- {d.get('decisao')} (motivo: {d.get('motivo')})")

    partes.append(f"\nOBJETIVO: {atual['pergunta']}\n")
    partes.append(f"TRECHOS RELEVANTES ({atual['tokens_usados']} tokens selecionados):")
    for t in atual.get("trechos", []):
        cabecalho = f"\n--- {t['arquivo']} (linhas {t['linhas']}"
        if t.get("simbolo"):
            cabecalho += f", simbolo: {t['simbolo']}"
        cabecalho += f", relevancia: {t['score']}) ---"
        partes.append(cabecalho)
        partes.append(t["conteudo"])

    if evidencias:
        partes.append("\nEVIDENCIAS (relacoes ja conhecidas, nao precisa procurar de novo):")
        for e in evidencias:
            partes.append(
                f"- {e.get('entity')}: definido em {e.get('defined_in')}, usado por {e.get('used_by')}"
            )

    if atual.get("historico_relacionado"):
        partes.append("\nDECISOES ANTERIORES RELACIONADAS:")
        for d in atual["historico_relacionado"]:
            partes.append(f"- {d.get('data', '?')}: {d.get('decisao')} (motivo: {d.get('motivo')})")

    if atual.get("restricoes"):
        partes.append("\nRESTRICOES:")
        for r in atual["restricoes"]:
            partes.append(f"- {r}")

    return "\n".join(partes)



def _proxima_acao_edicao(goal_state, evidencias, actions, edit_state):
    """Resume o proximo gate deterministico do ciclo de edicao."""
    if (goal_state or {}).get("mode") != "edit":
        return None
    frescas = [item for item in (evidencias or []) if item.get("estado") == "fresh"]
    if not frescas:
        return "READ_REQUIRED: use read_range/read_file to obtain fresh code before proposing any patch."
    dry_run_ok = any(
        item.get("tool") == "test_patch_dry_run"
        and item.get("ok") is True
        and item.get("executed") is True
        for item in (actions or [])
    )
    estado_edicao = edit_state or {}
    status = estado_edicao.get("status")
    if not dry_run_ok and not status:
        return "The next action must be test_patch_dry_run for the exact range to change."
    if dry_run_ok and not status:
        return "WRITE_PENDING: the next action must be apply_patch with the same dry-run proposal; the system will request confirmation."
    if status == "applied_pending_tests":
        return "RUN_TESTS_REQUIRED: the next action must be run_tests; do not finalize before verification."
    if status == "tests_passed" and not estado_edicao.get("post_write_evidence_id"):
        return "POST_WRITE_READ_REQUIRED: the next action must be read_range on the final changed range."
    if status == "reverted":
        return "The change was reverted; explain the failure without claiming success."
    return None

def montar_prompt_agente(pergunta, observacoes=None, entendimento=None, max_entradas=4,
                         fatos_importantes=None, catalogo_tools=None,
                         goal_state=None, evidencias=None, actions=None,
                         edit_state=None, config=None, system_prompt=""):
    """Monta um passo do Agente com contexto virtual orcado (Atualizacao 42).

    Metadados, acoes e observacoes continuam compactos. Codigo real fica em
    evidencias estruturadas persistentes e entra por relevancia ate o saldo
    dinamico da janela ``llm.context_window_tokens``. O retrieval antigo
    conserva seu proprio ``context.token_budget`` e nao participa desta conta.
    """
    cfg_contexto = (config or {}).get("context_engine", {})
    max_entradas = cfg_contexto.get("max_recent_observations", max_entradas)
    partes = []
    if entendimento:
        bloco = bloco_entendimento(entendimento)
        if bloco:
            partes.extend(bloco)
            partes.append("")

    goal_state = goal_state or {
        "objective": pergunta,
        "task_type": "chat",
        "status": "in_progress",
    }
    partes.append("ORIGINAL USER REQUEST (preserve exactly; answer in the same language):")
    partes.append(pergunta)
    partes.append("")
    partes.append("GOAL STATE (internal protocol in English):")
    partes.append(json.dumps(_valor_para_modelo(goal_state), ensure_ascii=False, separators=(",", ":")))
    proxima_acao = _proxima_acao_edicao(goal_state, evidencias, actions, edit_state)
    if proxima_acao:
        partes.append("MANDATORY NEXT EDIT ACTION:")
        partes.append(proxima_acao)
    partes.append("")

    catalogo_tools = catalogo_tools or []
    partes.append(
        "TOOL CATALOG (generated from the executable registry; "
        "use only tool names and arguments declared here):"
    )
    partes.append(json.dumps(
        _compactar_catalogo_tools(catalogo_tools),
        ensure_ascii=False,
        separators=(",", ":"),
    ))
    partes.append("")

    fatos_importantes = fatos_importantes or []
    if fatos_importantes:
        partes.append(
            "IMPORTANT FACTS (auxiliary model memory; never a substitute for tool evidence):"
        )
        for fato in fatos_importantes:
            partes.append(f"- {fato}")
        partes.append("")

    actions = actions or []
    if actions:
        recentes_acoes = actions[-max_entradas:]
        partes.append(
            f"ACTIONS (last {len(recentes_acoes)} of {len(actions)}; long results are not duplicated):"
        )
        for acao in recentes_acoes:
            partes.append(json.dumps(acao, ensure_ascii=False, separators=(",", ":")))
        partes.append("")

    observacoes = observacoes or []
    if observacoes:
        recentes = observacoes[-max_entradas:]
        omitidas = len(observacoes) - len(recentes)
        partes.append(
            f"RECENT OBSERVATIONS (last {len(recentes)} of {len(observacoes)}; "
            "full code lives in EVIDENCE, not in these summaries):"
        )
        if omitidas > 0:
            partes.append(
                f"(+ {omitidas} older observation(s) omitted; associated evidence remains preserved)"
            )
        for i, obs in enumerate(recentes, start=1):
            partes.append(f"\n{i}. tool: {obs.get('tool')}")
            partes.append(f"   summary: {obs.get('resumo')}")
    else:
        partes.append("RECENT OBSERVATIONS: (no action has been executed in this task yet)")

    evidencias = evidencias or []
    stale = [item for item in evidencias if item.get("estado") == "stale"]
    if stale:
        partes.append("")
        partes.append(
            "STALE EVIDENCE (do not use in the final answer; re-read the file): "
            + ", ".join(
                f"{item.get('id')}={item.get('arquivo')}:"
                f"{item.get('linha_inicio')}-{item.get('linha_fim')}"
                for item in stale
            )
        )

    cfg_llm = (config or {}).get("llm", {})
    partes.append("")
    partes.append(
        "CONTEXT BUDGET: window={} tokens; reserved response={}; "
        "safety margin={}.".format(
            cfg_llm.get("context_window_tokens", 2048),
            cfg_llm.get("max_tokens", 0) or 0,
            cfg_contexto.get("safety_margin_tokens", 256),
        )
    )
    partes.append(
        "SELECTED FRESH EVIDENCE (full content remains in state by evidence_id):"
    )
    prompt_sem_evidencias = "\n".join(partes)
    orcamento = calcular_orcamento_evidencias(
        config or {}, system_prompt, prompt_sem_evidencias,
    )
    selecao = selecionar_evidencias(
        evidencias,
        pergunta,
        orcamento["evidence_budget_tokens"],
        orcamento["chars_per_token"],
    )
    if selecao["selecionadas"]:
        for item in selecao["selecionadas"]:
            partes.append("")
            partes.append(item["bloco"])
    else:
        partes.append("(no fresh evidence is available or fits in this step)")

    return "\n".join(partes)
