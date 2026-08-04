#!/usr/bin/env python3
"""
cache.py
--------
Cache de resposta por hash do prompt COMPLETO (modelo + prompt de sistema +
prompt de usuario ja montado pelo compiler). Evita chamar a LLM de novo
quando exatamente a mesma coisa ja foi perguntada nas mesmas condicoes.

A chave inclui o prompt inteiro, nao so o texto que o usuario digitou --
isso e o que torna o cache seguro: se o retrieval trouxe trechos
diferentes, se o historico de conversa mudou, ou se o Analista decidiu
ler outra coisa, o prompt final muda e a chave muda junto. O cache so
acerta quando a pergunta E o contexto em volta dela sao identicos a uma
chamada anterior -- nunca devolve uma resposta calculada com um contexto
diferente do atual.

Fica em context/cache_llm.json (mesma pasta de atual.json/ultima_resposta.txt,
por ser um artefato derivado, nao memoria "de verdade" do projeto).
"""
import hashlib
import json
import os
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_THIS_DIR)
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from engine.memoria_lock import lock_para  # noqa: E402
from engine.persistencia import salvar_json_atomico  # noqa: E402
from engine.retencao import podar_cache  # noqa: E402

NOME_ARQUIVO = "cache_llm.json"


def _caminho(base_dir):
    return os.path.join(base_dir, "context", NOME_ARQUIVO)


def _chave(backend_fingerprint, prompt_sistema, prompt_usuario):
    bruto = "\x1f".join([
        backend_fingerprint, prompt_sistema, prompt_usuario,
    ]).encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()


def _carregar(base_dir):
    caminho = _caminho(base_dir)
    if not os.path.exists(caminho):
        return {"version": "2.0", "entradas": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
            if not isinstance(dados, dict):
                return {"version": "2.0", "entradas": {}}
            if not isinstance(dados.get("entradas"), dict):
                dados["entradas"] = {}
            dados["version"] = "2.0"
            return dados
    except (json.JSONDecodeError, OSError, TypeError):
        return {"version": "2.0", "entradas": {}}


def _salvar(base_dir, dados):
    caminho = _caminho(base_dir)
    salvar_json_atomico(caminho, dados)


def obter(
    base_dir, backend_fingerprint, prompt_sistema, prompt_usuario,
    max_entradas=500, max_age_days=30,
):
    """Devolve a resposta em cache para este prompt exato, ou None se nao houver.

    Bug 2 do plano de correcao: tambem faz ler+modificar (hits/ultimo_uso)
    +gravar, o mesmo padrao vulneravel a corrida entre a thread do Flask e
    a do Worker que o resto da memoria tinha -- protegido pelo mesmo lock
    por caminho de arquivo."""
    with lock_para(_caminho(base_dir)):
        dados = _carregar(base_dir)
        antes = len(dados["entradas"])
        podar_cache(dados["entradas"], max_entradas, max_age_days)
        if len(dados["entradas"]) != antes:
            _salvar(base_dir, dados)
        chave = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
        entrada = dados["entradas"].get(chave)
        if entrada is None:
            return None
        entrada["hits"] = entrada.get("hits", 0) + 1
        entrada["ultimo_uso"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _salvar(base_dir, dados)
        return entrada["resposta"]


def definir(
    base_dir, backend_fingerprint, prompt_sistema, prompt_usuario, resposta,
    max_entradas=500, max_age_days=30,
):
    """Grava a resposta no cache. Se passar de max_entradas, descarta as usadas
    ha mais tempo (LRU simples por 'ultimo_uso'). Mesmo lock de obter() --
    e' o mesmo arquivo (context/cache_llm.json)."""
    with lock_para(_caminho(base_dir)):
        dados = _carregar(base_dir)
        entradas = dados["entradas"]
        chave = _chave(backend_fingerprint, prompt_sistema, prompt_usuario)
        agora = time.strftime("%Y-%m-%dT%H:%M:%S")
        entradas[chave] = {
            "resposta": resposta,
            "criado_em": agora,
            "ultimo_uso": agora,
            "hits": 0,
        }
        podar_cache(entradas, max_entradas, max_age_days)
        dados["version"] = "2.0"
        _salvar(base_dir, dados)
