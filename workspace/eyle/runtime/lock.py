#!/usr/bin/env python3
"""
memoria_lock.py
----------------
A interface web, o Worker e o agente podem persistir estado ao mesmo tempo.
Este modulo serializa atualizacoes no mesmo arquivo para evitar lost update --
ler o JSON inteiro, modificar em memoria, gravar o JSON inteiro de volta
-- sem nenhuma trava. Isso e' seguro enquanto so uma coisa escreve por
vez, mas main.py serve sobe o Flask (thread da requisicao) e o Worker
(eyle/runtime/worker.py, thread permanente) NO MESMO PROCESSO, e os dois podem
chamar registrar_mensagem/salvar_evidencias/registrar_historico ao mesmo
tempo -- exatamente o modo normal de operacao do agente persistente, nao
um caso raro. Sem trava, duas escritas concorrentes podem se perder uma
a outra (lost update) ou gerar o mesmo id duas vezes.

Como tudo roda no mesmo processo (threads, nao processos separados), um
threading.Lock() por caminho de arquivo resolve -- nao precisamos de
lock de sistema de arquivos (flock) pra esse caso de uso. Cada caminho
tem seu proprio lock (conversa.json e agent_pendente.json podem ser escritos
em paralelo sem se atrapalhar; so o MESMO arquivo precisa ser
serializado).

Uso:
    from eyle.runtime.lock import lock_para

    def registrar_algo(...):
        caminho = os.path.join(MEMORY_DIR, "conversa.json")
        with lock_para(caminho):
            dados = carregar(caminho)
            ...
            salvar(caminho, dados)
"""
import os
import threading
from collections import defaultdict

_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def lock_para(caminho):
    """Devolve sempre o MESMO threading.Lock() para o mesmo caminho
    (normalizado, absoluto), mesmo que seja passado com barras/casing
    ligeiramente diferentes entre chamadas."""
    chave = os.path.normcase(os.path.normpath(os.path.abspath(caminho)))
    with _locks_guard:
        return _locks[chave]
