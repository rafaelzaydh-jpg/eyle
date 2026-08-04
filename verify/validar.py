#!/usr/bin/env python3
"""
validar.py
----------
"Sem verificacao vira so RAG. Com verificacao vira agente."

Este modulo confere se a resposta da LLM:
  1. So cita arquivos que realmente existem em estrutura.json
  2. Nao inventa inicio/fim de faixa fora do tamanho real do arquivo
  3. Fundamenta as citacoes nos arquivos enviados ao modelo nesta rodada

E entao registra a interacao em memory/historico.json, com
versao/data/hash -- porque memoria sem historico vira memoria falsa.
"""
import argparse
import json
import os
import re
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.memoria_lock import lock_para  # noqa: E402
from engine.persistencia import salvar_json_atomico  # noqa: E402
from engine.retencao import limitar_lista  # noqa: E402
from engine.config_schema import carregar_config_validada  # noqa: E402

RE_ARQUIVO = re.compile(r"([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]{1,5})(?::(\d+)(?:-(\d+))?)?")
EXTENSOES_VERIFICAVEIS = re.compile(
    r"\.(py|js|ts|jsx|tsx|java|go|rb|php|c|cpp|h|cs|rs|md|json|sql)$"
)


def carregar_estrutura(memory_dir):
    caminho = os.path.join(memory_dir, "estrutura.json")
    if not os.path.exists(caminho):
        return {}
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f).get("arquivos", {})


def validar_resposta(resposta, memory_dir, arquivos_no_contexto=None):
    estrutura = carregar_estrutura(memory_dir)
    contexto_informado = arquivos_no_contexto is not None

    # indice por nome-base construido uma unica vez -- valida so o que foi
    # DE FATO citado na resposta (mencoes), sem varrer estrutura.json de
    # novo a cada citacao encontrada
    por_basename = {}
    for caminho in estrutura:
        por_basename.setdefault(caminho.rsplit("/", 1)[-1], []).append(caminho)

    def resolver_caminho(arquivo):
        if arquivo in estrutura:
            return arquivo
        candidatos = por_basename.get(arquivo, [])
        return candidatos[0] if len(candidatos) == 1 else None

    arquivos_contexto_resolvidos = set()
    for arquivo in arquivos_no_contexto or []:
        resolvido = resolver_caminho(arquivo)
        arquivos_contexto_resolvidos.add(resolvido or arquivo)

    mencoes = RE_ARQUIVO.findall(resposta)
    total = 0
    confirmadas = 0
    grounded = 0
    arquivos_citados_do_contexto = set()
    avisos = []

    for arquivo, linha_ini, linha_fim in mencoes:
        # ignora falsos positivos comuns (ex: "v1.0", "3.10", urls simples)
        if arquivo.count(".") > 3 or "/" not in arquivo and arquivo.split(".")[0].isdigit():
            continue
        # so valida coisas que parecem caminho de arquivo de codigo real
        if not EXTENSOES_VERIFICAVEIS.search(arquivo):
            continue

        total += 1
        caminho_resolvido = resolver_caminho(arquivo)
        info = estrutura.get(caminho_resolvido) if caminho_resolvido else None

        if info is None:
            candidatos = por_basename.get(arquivo, [])
            if len(candidatos) > 1:
                avisos.append(
                    f"Arquivo citado '{arquivo}' e ambiguo na memoria indexada; use o caminho completo."
                )
            else:
                avisos.append(
                    f"Arquivo citado '{arquivo}' NAO existe na memoria indexada (possivel alucinacao)."
                )
            continue

        citacao_valida = True
        total_linhas = int(info.get("linhas") or 0)

        if linha_ini:
            linha_ini_n = int(linha_ini)
            if linha_ini_n < 1 or linha_ini_n > total_linhas:
                citacao_valida = False
                avisos.append(
                    f"Linha inicial {linha_ini_n} citada em '{arquivo}' esta fora do tamanho real "
                    f"do arquivo ({total_linhas} linhas)."
                )

        if linha_fim:
            linha_fim_n = int(linha_fim)
            linha_ini_n = int(linha_ini)
            if linha_fim_n < linha_ini_n:
                citacao_valida = False
                avisos.append(
                    f"Faixa invertida citada em '{arquivo}': {linha_ini_n}-{linha_fim_n}."
                )
            elif linha_fim_n > total_linhas:
                citacao_valida = False
                avisos.append(
                    f"Linha final {linha_fim_n} citada em '{arquivo}' esta fora do tamanho real "
                    f"do arquivo ({total_linhas} linhas)."
                )

        if not citacao_valida:
            continue

        confirmadas += 1

        if contexto_informado:
            if caminho_resolvido in arquivos_contexto_resolvidos:
                grounded += 1
                arquivos_citados_do_contexto.add(caminho_resolvido)
            else:
                avisos.append(
                    f"Arquivo '{arquivo}' foi citado mas nao estava nos trechos enviados ao modelo nesta rodada."
                )

    # Atualizacao 30: as tres perguntas sao diferentes e nao podem mais
    # ser esmagadas num unico numero chamado "confianca":
    # - citation_validity: as citacoes existem e suas faixas sao validas?
    # - coverage: quantos arquivos do contexto foram efetivamente citados?
    # - grounding: quantas citacoes vieram do contexto mostrado ao modelo?
    citation_validity = None if total == 0 else round(confirmadas / total, 2)
    grounding = (
        None if total == 0 or not contexto_informado
        else round(grounded / total, 2)
    )
    coverage = (
        None if not contexto_informado or not arquivos_contexto_resolvidos
        else round(len(arquivos_citados_do_contexto) / len(arquivos_contexto_resolvidos), 2)
    )

    # Compatibilidade temporaria para consumidores antigos. Este valor nao
    # e' mais inventado a partir de status="success": vem somente das
    # citacoes verificadas. Consumidores novos devem usar as tres metricas.
    confianca = grounding if contexto_informado else citation_validity
    verificacao_aprovada = (
        None if total == 0
        else citation_validity == 1.0 and (grounding is None or grounding == 1.0)
    )

    return {
        "total_mencoes_verificadas": total,
        "confirmadas": confirmadas,
        "citacoes_grounded": grounded,
        "arquivos_contexto": len(arquivos_contexto_resolvidos),
        "arquivos_contexto_citados": len(arquivos_citados_do_contexto),
        "citation_validity": citation_validity,
        "coverage": coverage,
        "grounding": grounding,
        "verificacao_aprovada": verificacao_aprovada,
        "confianca": confianca,
        "avisos": avisos,
    }


def registrar_historico(
    memory_dir, pergunta, arquivos_relevantes, resultado_validacao,
    resumo_decisao=None, max_entradas=None,
):
    """Bug 2 do plano de correcao: mesmo padrao ler+modificar+gravar de
    engine/engine.py:registrar_mensagem, protegido pelo mesmo tipo de lock
    por caminho de arquivo -- registrar_historico e' chamada de dentro do
    pipeline (thread do Worker), mas o arquivo tambem pode ser escrito por
    outra chamada concorrente (ex: main.py rodando em paralelo)."""
    caminho = os.path.join(memory_dir, "historico.json")
    with lock_para(caminho):
        agora = time.strftime("%Y-%m-%dT%H:%M:%S")

        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                hist = json.load(f)
        else:
            hist = {"version": "1.0", "decisoes": []}

        metricas = {
            "citation_validity": resultado_validacao.get("citation_validity"),
            "coverage": resultado_validacao.get("coverage"),
            "grounding": resultado_validacao.get("grounding"),
        }

        hist["updated"] = agora
        hist.setdefault("decisoes", []).append({
            "data": agora,
            "pergunta": pergunta,
            "arquivos_relevantes": arquivos_relevantes,
            "decisao": resumo_decisao or "consulta respondida",
            "motivo": (
                "verify=" + ",".join(f"{nome}={valor}" for nome, valor in metricas.items())
            ),
            "verificacao": metricas,
            "avisos": resultado_validacao.get("avisos", []),
        })

        if max_entradas is None:
            config_path = os.path.join(os.path.dirname(os.path.abspath(memory_dir)), "config.json")
            if os.path.exists(config_path):
                config = carregar_config_validada(config_path)
                max_entradas = config.get("retention", {}).get(
                    "historico_max_entradas", 1000,
                )
            else:
                max_entradas = 1000
        hist["decisoes"] = limitar_lista(hist["decisoes"], max_entradas)

        salvar_json_atomico(caminho, hist)


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Valida a ultima resposta da LLM contra a memoria indexada")
    parser.add_argument("--memory-dir", default=os.path.join(base_dir, "memory"))
    parser.add_argument("--resposta-file", default=os.path.join(base_dir, "context", "ultima_resposta.txt"))
    parser.add_argument("--atual-file", default=os.path.join(base_dir, "context", "atual.json"))
    args = parser.parse_args()

    with open(args.resposta_file, "r", encoding="utf-8") as f:
        resposta = f.read()
    with open(args.atual_file, "r", encoding="utf-8") as f:
        atual = json.load(f)

    resultado = validar_resposta(resposta, args.memory_dir, atual.get("arquivos_relevantes"))
    registrar_historico(args.memory_dir, atual["pergunta"], atual.get("arquivos_relevantes", []), resultado)

    print(
        "[validar] "
        f"citation_validity={resultado['citation_validity']} "
        f"coverage={resultado['coverage']} grounding={resultado['grounding']} "
        f"({resultado['confirmadas']}/{resultado['total_mencoes_verificadas']} citacoes validas)"
    )
    for aviso in resultado["avisos"]:
        print(f"[validar][AVISO] {aviso}")


if __name__ == "__main__":
    main()
