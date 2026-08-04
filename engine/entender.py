#!/usr/bin/env python3
"""
entender.py
-----------
Atualizacao 3 -- Modelo Interno do Projeto.

Gera memory/entendimento.json["arquivos"]: um objeto POR ARQUIVO (nao mais
so por componente/pasta), preenchido pela LLM lendo o arquivo INTEIRO uma
unica vez, no formato:

    {
      "engine/engine.py": {
        "tipo": "orquestrador",
        "responsabilidade": "orquestrar fluxo principal",
        "entrada": ["mensagem", "estado"],
        "saida": ["resposta"],
        "depende_de": ["retrieval/buscar.py", "llm/executar.py", "verify/validar.py"],
        "funcoes_principais": ["processar"],
        "pontos_criticos": ["controle do pipeline", "alto acoplamento"],
        "hash": "a1b2c3d4e5f6..."
      }
    }

So chama a LLM para arquivos NOVOS ou cujo hash mudou desde a ultima
execucao (o mesmo hash sha256 curto ja calculado por ingest.py em
estrutura.json) -- um arquivo sem mudanca reaproveita a entrada anterior
sem gastar uma chamada de LLM. Isso e o que torna viavel rodar isto a
cada ingest, mesmo em projetos grandes: so o delta e analisado.

Chamado por ingest.py, depois que estrutura.json ja foi montado (precisa
do hash de cada arquivo). Nao decide nada sobre retrieval/resposta -- isso
e Atualizacao 4 (dar dicas reais), fora do escopo deste modulo.
"""
import json
import os
import re
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.compiler import montar_prompt_entendedor
from engine.seguranca import _resolver_caminho_seguro
from llm.executar import ErroLLM, executar_entendedor

_RE_JSON_BLOCO = re.compile(r"\{.*\}", re.DOTALL)

CAMPOS_LISTA = ("entrada", "saida", "depende_de", "funcoes_principais", "pontos_criticos")


def _parse_resposta_entendedor(texto):
    """
    Extrai o JSON do retrato estrutural devolvido pela LLM. Se a LLM nao
    devolver JSON valido (comum em modelos pequenos em Q4), devolve None --
    quem chama decide o fallback (normalmente: manter a entrada anterior,
    se existir, em vez de gravar algo inventado/vazio por cima de um
    entendimento bom que ja existia).
    """
    match = _RE_JSON_BLOCO.search(texto or "")
    if not match:
        return None
    try:
        dados = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, dict):
        return None

    resultado = {
        "tipo": dados.get("tipo") or "desconhecido",
        "responsabilidade": dados.get("responsabilidade") or "",
    }
    for campo in CAMPOS_LISTA:
        valor = dados.get(campo)
        resultado[campo] = valor if isinstance(valor, list) else []
    return resultado


def gerar_entendimento_arquivos(estrutura, caminho_projeto, config=None, entendimento_existente=None, log=None):
    """
    estrutura: dict "arquivo_relativo -> {hash, linhas, funcoes_classes, ...}"
               (saida de ingest.py / memory/estrutura.json)
    caminho_projeto: pasta raiz absoluta do projeto sendo indexado
    config: config.json carregado (usa config['llm'] e config['entendimento'])
    entendimento_existente: memory/entendimento.json ja carregado (para
               reaproveitar entradas cujo hash nao mudou)
    log: funcao de log (default: print); passe uma no-op para rodar em silencio

    Devolve dict "arquivo_relativo -> entendimento" pronto para virar
    entendimento_json["arquivos"].
    """
    config = config or {}
    log = log or print
    entendimento_existente = entendimento_existente or {}
    arquivos_existentes = entendimento_existente.get("arquivos", {})

    cfg_ent = config.get("entendimento", {})
    ativado = cfg_ent.get("gerar_via_llm", True)
    max_chars = cfg_ent.get("max_chars_por_arquivo", 20000)

    arquivos_novos = {}
    gerados = 0
    reaproveitados = 0
    falhas = 0

    for caminho_rel, info in estrutura.items():
        hash_atual = info.get("hash")
        anterior = arquivos_existentes.get(caminho_rel)

        # hash igual ao da ultima execucao -> nada mudou neste arquivo,
        # reaproveita o entendimento anterior sem gastar chamada de LLM
        if anterior and hash_atual and anterior.get("hash") == hash_atual:
            arquivos_novos[caminho_rel] = anterior
            reaproveitados += 1
            continue

        if not ativado:
            # LLM desligada em config.json (entendimento.gerar_via_llm=false):
            # preserva o que ja existia (se existia) em vez de apagar
            if anterior:
                arquivos_novos[caminho_rel] = anterior
            continue

        caminho_abs = _resolver_caminho_seguro(caminho_projeto, caminho_rel)
        if caminho_abs is None or not os.path.isfile(caminho_abs):
            falhas += 1
            log(f"[entender] Leitura segura rejeitou {caminho_rel}")
            if anterior:
                arquivos_novos[caminho_rel] = anterior
            continue
        try:
            with open(caminho_abs, "r", encoding="utf-8", errors="ignore") as f:
                conteudo = f.read()
        except OSError as e:
            falhas += 1
            log(f"[entender] Nao foi possivel ler {caminho_rel}: {e}")
            if anterior:
                arquivos_novos[caminho_rel] = anterior
            continue

        prompt_usuario = montar_prompt_entendedor(caminho_rel, conteudo, max_chars=max_chars)
        try:
            resposta_bruta = executar_entendedor(prompt_usuario, config)
        except ErroLLM as erro:
            falhas += 1
            log(
                f"[entender] Falha ao gerar entendimento de {caminho_rel} ({erro}); "
                "mantendo entrada anterior (se houver)."
            )
            if anterior:
                arquivos_novos[caminho_rel] = anterior
            continue
        entrada = _parse_resposta_entendedor(resposta_bruta)

        if entrada is None:
            falhas += 1
            log(
                f"[entender] Falha ao gerar entendimento de {caminho_rel} "
                "(resposta sem JSON valido); mantendo entrada anterior (se houver)."
            )
            if anterior:
                arquivos_novos[caminho_rel] = anterior
            continue

        entrada["hash"] = hash_atual
        arquivos_novos[caminho_rel] = entrada
        gerados += 1

    log(
        f"[entender] {gerados} arquivo(s) analisado(s) pela LLM, "
        f"{reaproveitados} reaproveitado(s) (hash sem mudanca), {falhas} falha(s)."
    )
    return arquivos_novos
