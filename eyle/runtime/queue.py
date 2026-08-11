#!/usr/bin/env python3
"""
queue.py
--------
Fila persistente de eventos entre o Flask (web/routes.py) e o Worker
(eyle/runtime/worker.py).

Os jobs vivem em ``context/fila.sqlite3``. Um reinicio do processo nao
apaga eventos pendentes, e cada job conserva status, tentativas, resultado
ou erro. SQLite tambem permite que Flask e Worker rodem em processos
separados sem manter duas filas de memoria independentes.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from eyle.runtime.process import pid_ativo


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "context", "fila.sqlite3")

_evento_disponivel = threading.Event()
_schema_lock = threading.Lock()
_schemas_prontos = set()
_STATUS = ("pending", "processing", "completed", "failed", "cancelled")


def _parse_utc(valor):
    if not valor:
        return None
    try:
        instante = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if instante.tzinfo is None:
        return None
    return instante.astimezone(timezone.utc)


def _idade_segundos(valor, agora=None):
    dt = _parse_utc(valor)
    if dt is None:
        return None
    agora = agora or datetime.now(timezone.utc)
    return max(0.0, (agora - dt).total_seconds())


def _agora_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _serializar(valor):
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"), default=str)


QUEUE_SCHEMA_VERSION = "5.7.5"

_EXPECTED_TABLE_COLUMNS = {
    "jobs": [
        "id", "tipo", "payload", "status", "tentativas", "criado_em", "atualizado_em",
        "iniciado_em", "concluido_em", "resultado", "erro", "worker_id", "progresso",
        "progresso_seq", "cancel_requested", "cancel_reason",
    ],
    "runtime_meta": ["chave", "valor"],
    "worker_heartbeat": ["worker_id", "status", "job_id", "atualizado_em", "detalhe", "pid"],
}


def _table_columns(conexao, table):
    return [str(row[1]) for row in conexao.execute(f"PRAGMA table_info({table})")]


def _validate_schema(conexao):
    for table, expected in _EXPECTED_TABLE_COLUMNS.items():
        observed = _table_columns(conexao, table)
        if observed != expected:
            raise RuntimeError(
                f"QUEUE_SCHEMA_INCOMPATIBLE:{table}:expected={','.join(expected)}:observed={','.join(observed)}"
            )
    row = conexao.execute(
        "SELECT valor FROM runtime_meta WHERE chave = 'schema_version'"
    ).fetchone()
    if row is None or str(row[0]) != QUEUE_SCHEMA_VERSION:
        observed = "missing" if row is None else str(row[0])
        raise RuntimeError(f"QUEUE_SCHEMA_INCOMPATIBLE:version:{observed}")


def _inicializar_schema(conexao, caminho_banco, *, new_database):
    """Create exactly the Rev5.7.5 queue schema or reject the existing database."""
    with _schema_lock:
        if caminho_banco in _schemas_prontos:
            return
        if new_database:
            conexao.execute(
                """
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tentativas INTEGER NOT NULL DEFAULT 0,
                    criado_em TEXT NOT NULL,
                    atualizado_em TEXT NOT NULL,
                    iniciado_em TEXT,
                    concluido_em TEXT,
                    resultado TEXT,
                    erro TEXT,
                    worker_id TEXT,
                    progresso TEXT,
                    progresso_seq INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_reason TEXT
                )
                """
            )
            conexao.execute("CREATE INDEX idx_jobs_status_id ON jobs(status, id)")
            conexao.execute(
                """
                CREATE TABLE runtime_meta (
                    chave TEXT PRIMARY KEY,
                    valor TEXT NOT NULL
                )
                """
            )
            conexao.executemany(
                "INSERT INTO runtime_meta (chave, valor) VALUES (?, ?)",
                [
                    ("queue_instance_id", uuid.uuid4().hex),
                    ("schema_version", QUEUE_SCHEMA_VERSION),
                ],
            )
            conexao.execute(
                """
                CREATE TABLE worker_heartbeat (
                    worker_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    job_id INTEGER,
                    atualizado_em TEXT NOT NULL,
                    detalhe TEXT,
                    pid INTEGER
                )
                """
            )
        _validate_schema(conexao)
        _schemas_prontos.add(caminho_banco)


def _conectar():
    diretorio = os.path.dirname(DB_PATH)
    if diretorio:
        os.makedirs(diretorio, exist_ok=True)
    caminho_banco = os.path.abspath(DB_PATH)
    existia = os.path.exists(caminho_banco)
    if not existia:
        with _schema_lock:
            _schemas_prontos.discard(caminho_banco)
    conexao = sqlite3.connect(DB_PATH, timeout=5.0, isolation_level=None)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA busy_timeout = 5000")
    conexao.execute("PRAGMA journal_mode = WAL")
    conexao.execute("PRAGMA synchronous = NORMAL")
    _inicializar_schema(conexao, caminho_banco, new_database=not existia)
    return conexao


@contextmanager
def _abrir_conexao():
    conexao = _conectar()
    try:
        yield conexao
    finally:
        conexao.close()


def database_instance_id():
    """Identidade persistente do arquivo SQLite atual.

    Muda quando o banco e recriado, impedindo o navegador de associar um job
    numerico novo a um resumo antigo guardado em sessionStorage.
    """
    with _abrir_conexao() as conexao:
        linha = conexao.execute(
            "SELECT valor FROM runtime_meta WHERE chave = 'queue_instance_id'"
        ).fetchone()
    return str(linha["valor"]) if linha is not None else ""


def registrar_heartbeat(worker_id, status="idle", job_id=None, detalhe=None, pid=None):
    """Persiste sinal de vida do worker para health checks/watchdogs."""
    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        conexao.execute(
            """
            INSERT INTO worker_heartbeat (worker_id, status, job_id, atualizado_em, detalhe, pid)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                status = excluded.status,
                job_id = excluded.job_id,
                atualizado_em = excluded.atualizado_em,
                detalhe = excluded.detalhe,
                pid = excluded.pid
            """,
            (
                str(worker_id), str(status),
                int(job_id) if job_id is not None else None,
                agora, None if detalhe is None else str(detalhe)[:1000],
                int(pid if pid is not None else os.getpid()),
            ),
        )
    return agora



def adicionar(evento):
    """Persiste um evento e devolve o ID numerico do job criado."""
    if not isinstance(evento, dict):
        raise TypeError("evento precisa ser um dict")
    tipo = str(evento.get("type") or "").strip()
    if not tipo:
        raise ValueError("evento precisa informar 'type'")

    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO jobs (tipo, payload, status, criado_em, atualizado_em)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (tipo, _serializar(evento), agora, agora),
        )
        job_id = cursor.lastrowid
    _evento_disponivel.set()
    return job_id


def _reservar_proximo(max_invalid_jobs=100, worker_id=None):
    """Reserva atomicamente o job pendente mais antigo."""
    conexao = _conectar()
    invalidos = 0
    ciclos = 0
    # ``BEGIN IMMEDIATE`` ja serializa escritores, mas um banco alterado por
    # outro processo/versao nao deve transformar um conflito de ``rowcount``
    # em spin infinito. O teto e maior que o numero de payloads invalidos
    # permitido para conservar o comportamento normal da fila.
    max_ciclos = max(4, max(1, int(max_invalid_jobs or 1)) * 2 + 2)
    try:
        while ciclos < max_ciclos:
            ciclos += 1
            conexao.execute("BEGIN IMMEDIATE")
            linha = conexao.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if linha is None:
                conexao.commit()
                return None

            agora = _agora_utc()
            try:
                evento = json.loads(linha["payload"])
                if not isinstance(evento, dict):
                    raise ValueError("payload nao e um objeto JSON")
            except (TypeError, ValueError, json.JSONDecodeError) as erro:
                conexao.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', erro = ?, atualizado_em = ?, concluido_em = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (f"payload invalido: {erro}", agora, agora, linha["id"]),
                )
                conexao.commit()
                invalidos += 1
                if invalidos >= max(1, int(max_invalid_jobs or 1)):
                    return None
                continue

            atualizado = conexao.execute(
                """
                UPDATE jobs
                SET status = 'processing', tentativas = tentativas + 1,
                    iniciado_em = ?, atualizado_em = ?, erro = NULL, worker_id = ?,
                    progresso = ?, progresso_seq = progresso_seq + 1,
                    cancel_requested = 0, cancel_reason = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (
                    agora, agora, None if worker_id is None else str(worker_id),
                    _serializar({
                        "phase": "starting",
                        "message": "Preparando a tarefa",
                        "updated_at": agora,
                    }),
                    linha["id"],
                ),
            )
            if atualizado.rowcount != 1:
                conexao.rollback()
                continue
            conexao.commit()
            evento["_job_id"] = linha["id"]
            evento["_job_tentativa"] = linha["tentativas"] + 1
            return evento
        return None
    finally:
        conexao.close()


def proximo(timeout=1.0, max_invalid_jobs=100, worker_id=None):
    """Espera ate ``timeout`` por um job e o marca como ``processing``."""
    timeout = max(0.0, float(timeout or 0.0))
    limite = time.monotonic() + timeout
    while True:
        evento = _reservar_proximo(
            max_invalid_jobs=max_invalid_jobs, worker_id=worker_id,
        )
        if evento is not None:
            return evento

        restante = limite - time.monotonic()
        if restante <= 0:
            _evento_disponivel.clear()
            return None
        _evento_disponivel.wait(timeout=min(0.1, restante))
        _evento_disponivel.clear()


def atualizar_progresso(job_id, progresso=None, **campos):
    """Publica progresso seguro e incremental de um job em processamento.

    O navegador recebe apenas este objeto resumido; prompts, observacoes internas
    e respostas estruturadas brutas nunca sao gravados aqui. Atualizacoes podem
    vir do processo filho isolado porque o estado vive no mesmo SQLite da fila.
    """
    job_id = int(job_id)
    patch = {}
    if isinstance(progresso, dict):
        patch.update(progresso)
    patch.update(campos)
    if not patch:
        return False

    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        conexao.execute("BEGIN IMMEDIATE")
        linha = conexao.execute(
            "SELECT progresso, status, cancel_requested FROM jobs WHERE id = ?", (job_id,),
        ).fetchone()
        if (
            linha is None
            or linha["status"] not in ("pending", "processing")
            or int(linha["cancel_requested"] or 0)
        ):
            conexao.rollback()
            return False
        atual = {}
        if linha["progresso"]:
            try:
                carregado = json.loads(linha["progresso"])
                if isinstance(carregado, dict):
                    atual = carregado
            except (TypeError, ValueError, json.JSONDecodeError):
                atual = {}
        atual.update(patch)
        atual["updated_at"] = agora
        cursor = conexao.execute(
            """
            UPDATE jobs
            SET progresso = ?, progresso_seq = progresso_seq + 1, atualizado_em = ?
            WHERE id = ? AND status IN ('pending', 'processing')
              AND cancel_requested = 0
            """,
            (_serializar(atual), agora, job_id),
        )
        conexao.commit()
        return cursor.rowcount == 1


def cancelamento_solicitado(job_id):
    """Devolve o motivo quando um job recebeu pedido de cancelamento."""
    with _abrir_conexao() as conexao:
        linha = conexao.execute(
            "SELECT status, cancel_requested, cancel_reason FROM jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    if linha is None:
        return None
    if linha["status"] == "cancelled" or int(linha["cancel_requested"] or 0):
        return str(linha["cancel_reason"] or "cancelado pelo usuario")
    return None


def cancelar_job(job_id, motivo="mensagem removida pelo usuario"):
    """Cancela job pendente ou sinaliza interrupcao do job em processamento."""
    job_id = int(job_id)
    motivo = str(motivo or "cancelado pelo usuario")[:500]
    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        conexao.execute("BEGIN IMMEDIATE")
        linha = conexao.execute(
            "SELECT status, progresso FROM jobs WHERE id = ?", (job_id,),
        ).fetchone()
        if linha is None:
            conexao.rollback()
            return {"job_id": job_id, "status": "missing", "changed": False}

        status = linha["status"]
        if status not in ("pending", "processing"):
            conexao.rollback()
            return {"job_id": job_id, "status": status, "changed": False}

        progresso = {}
        if linha["progresso"]:
            try:
                carregado = json.loads(linha["progresso"])
                if isinstance(carregado, dict):
                    progresso = carregado
            except (TypeError, ValueError, json.JSONDecodeError):
                progresso = {}
        progresso.update({
            "phase": "cancelling" if status == "processing" else "cancelled",
            "message": "Cancelando a tarefa" if status == "processing" else "Tarefa cancelada",
            "updated_at": agora,
        })

        if status == "pending":
            conexao.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', cancel_requested = 1, cancel_reason = ?,
                    progresso = ?, progresso_seq = progresso_seq + 1,
                    atualizado_em = ?, concluido_em = ?
                WHERE id = ? AND status = 'pending'
                """,
                (motivo, _serializar(progresso), agora, agora, job_id),
            )
            novo_status = "cancelled"
        else:
            conexao.execute(
                """
                UPDATE jobs
                SET cancel_requested = 1, cancel_reason = ?, progresso = ?,
                    progresso_seq = progresso_seq + 1, atualizado_em = ?
                WHERE id = ? AND status = 'processing'
                """,
                (motivo, _serializar(progresso), agora, job_id),
            )
            novo_status = "processing"
        conexao.commit()
    _evento_disponivel.set()
    return {"job_id": job_id, "status": novo_status, "changed": True}


def marcar_cancelado(job_id, motivo="cancelado pelo usuario", resultado=None):
    """Fecha definitivamente um job cujo processo foi interrompido."""
    job_id = int(job_id)
    motivo = str(motivo or "cancelado pelo usuario")[:500]
    agora = _agora_utc()
    serializado = None if resultado is None else _serializar(resultado)
    with _abrir_conexao() as conexao:
        linha = conexao.execute(
            "SELECT progresso FROM jobs WHERE id = ?", (job_id,),
        ).fetchone()
        if linha is None:
            return False
        progresso = {}
        if linha["progresso"]:
            try:
                carregado = json.loads(linha["progresso"])
                if isinstance(carregado, dict):
                    progresso = carregado
            except (TypeError, ValueError, json.JSONDecodeError):
                progresso = {}
        progresso.update({
            "phase": "cancelled",
            "message": "Tarefa cancelada",
            "updated_at": agora,
        })
        cursor = conexao.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', cancel_requested = 1, cancel_reason = ?,
                resultado = ?, erro = NULL, progresso = ?,
                progresso_seq = progresso_seq + 1,
                atualizado_em = ?, concluido_em = ?
            WHERE id = ? AND status IN ('pending', 'processing')
            """,
            (motivo, serializado, _serializar(progresso), agora, agora, job_id),
        )
        return cursor.rowcount == 1


def _jobs_pergunta_ativos():
    with _abrir_conexao() as conexao:
        linhas = conexao.execute(
            """
            SELECT id, status, payload
            FROM jobs
            WHERE tipo = 'pergunta' AND status IN ('pending', 'processing')
            ORDER BY id
            """
        ).fetchall()
    ativos = []
    for linha in linhas:
        try:
            payload = json.loads(linha["payload"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            ativos.append({"id": int(linha["id"]), "status": linha["status"], "payload": payload})
    return ativos


def jobs_da_mensagem_ativos(mensagem_id):
    """Jobs cuja pergunta de origem e exatamente a mensagem informada."""
    mensagem_id = int(mensagem_id)
    return [
        item for item in _jobs_pergunta_ativos()
        if item["payload"].get("mensagem_id") == mensagem_id
    ]


def jobs_ativos_usando_mensagem(mensagem_id, excluir_job_ids=None):
    """Jobs que ja congelaram a mensagem em seu snapshot de contexto."""
    mensagem_id = int(mensagem_id)
    excluidos = {int(job_id) for job_id in (excluir_job_ids or [])}
    usados = []
    for item in _jobs_pergunta_ativos():
        if item["id"] in excluidos:
            continue
        snapshot = item["payload"].get("historico_snapshot") or []
        if any(
            isinstance(mensagem, dict) and mensagem.get("id") == mensagem_id
            for mensagem in snapshot
        ):
            usados.append(item)
    return usados


def cancelar_jobs_da_mensagem(mensagem_id, motivo="mensagem de origem removida"):
    resultados = []
    for item in jobs_da_mensagem_ativos(mensagem_id):
        resultados.append(cancelar_job(item["id"], motivo=motivo))
    return resultados


def concluir(job_id, resultado=None, duracao_segundos=None):
    """Marca um job reservado como concluido e persiste seu resultado."""
    motivo_cancelamento = cancelamento_solicitado(job_id)
    if motivo_cancelamento:
        marcar_cancelado(job_id, motivo_cancelamento)
        return False

    campos = {}
    if duracao_segundos is not None:
        try:
            campos["elapsed_seconds"] = round(max(0.0, float(duracao_segundos)), 2)
        except (TypeError, ValueError):
            pass
    atualizar_progresso(
        job_id, phase="completed", message="Tarefa concluida", **campos,
    )
    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        cursor = conexao.execute(
            """
            UPDATE jobs
            SET status = 'completed', resultado = ?, erro = NULL,
                atualizado_em = ?, concluido_em = ?
            WHERE id = ? AND status = 'processing' AND cancel_requested = 0
            """,
            (_serializar(resultado), agora, agora, int(job_id)),
        )
        return cursor.rowcount == 1


def falhar(job_id, erro, *, resultado, duracao_segundos=None):
    """Marca um job reservado como falho e conserva erro + resultado seguro.

    ``resultado`` e sempre explicito: ``None`` para falhas por excecao e o estado
    estruturado da AgentSession para falhas retornadas pelo fluxo normal.
    """
    motivo_cancelamento = cancelamento_solicitado(job_id)
    if motivo_cancelamento:
        marcar_cancelado(job_id, motivo_cancelamento, resultado=resultado)
        return False

    detalhe = f"{type(erro).__name__}: {erro}" if isinstance(erro, BaseException) else str(erro)
    campos = {"error": detalhe[:500]}
    if duracao_segundos is not None:
        try:
            campos["elapsed_seconds"] = round(max(0.0, float(duracao_segundos)), 2)
        except (TypeError, ValueError):
            pass
    atualizar_progresso(
        job_id, phase="failed", message="A tarefa falhou", **campos,
    )
    agora = _agora_utc()
    serializado = None if resultado is None else _serializar(resultado)
    with _abrir_conexao() as conexao:
        cursor = conexao.execute(
            """
            UPDATE jobs
            SET status = 'failed', resultado = ?, erro = ?,
                atualizado_em = ?, concluido_em = ?
            WHERE id = ? AND status = 'processing' AND cancel_requested = 0
            """,
            (serializado, detalhe, agora, agora, int(job_id)),
        )
        return cursor.rowcount == 1


def recuperar_interrompidos(stale_after_seconds=30, force=False):
    """Recoloca jobs cujo worker morreu ou parou de publicar heartbeat."""
    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        linhas = conexao.execute(
            """
            SELECT j.id, j.worker_id, j.cancel_requested, j.cancel_reason,
                   h.atualizado_em AS heartbeat_em, h.pid
            FROM jobs j
            LEFT JOIN worker_heartbeat h ON h.worker_id = j.worker_id
            WHERE j.status = 'processing'
            """
        ).fetchall()
        ids = []
        cancelados = []
        for linha in linhas:
            if int(linha["cancel_requested"] or 0):
                cancelados.append((int(linha["id"]), str(linha["cancel_reason"] or "cancelado pelo usuario")))
                continue
            stale = _idade_segundos(linha["heartbeat_em"])
            morto = linha["pid"] is not None and not pid_ativo(linha["pid"])
            sem_heartbeat_valido = not linha["worker_id"] or stale is None
            if force or sem_heartbeat_valido or morto or (
                stale >= max(0, float(stale_after_seconds))
            ):
                ids.append(int(linha["id"]))
        for job_id, motivo in cancelados:
            conexao.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', cancel_reason = ?, atualizado_em = ?,
                    concluido_em = ?, worker_id = NULL
                WHERE id = ? AND status = 'processing'
                """,
                (motivo, agora, agora, job_id),
            )
        recuperados = 0
        for job_id in ids:
            cursor = conexao.execute(
                """
                UPDATE jobs
                SET status = 'pending', atualizado_em = ?, iniciado_em = NULL,
                    worker_id = NULL,
                    erro = 'worker anterior foi interrompido; job recolocado na fila'
                WHERE id = ? AND status = 'processing'
                """,
                (agora, job_id),
            )
            recuperados += cursor.rowcount
    if recuperados:
        _evento_disponivel.set()
    return recuperados


def tamanho():
    """Quantidade de jobs ainda aguardando reserva pelo Worker."""
    with _abrir_conexao() as conexao:
        linha = conexao.execute(
            "SELECT COUNT(*) AS total FROM jobs WHERE status = 'pending'"
        ).fetchone()
        return int(linha["total"])


def obter(job_id):
    """Devolve o registro completo de um job, ou ``None`` se nao existir."""
    with _abrir_conexao() as conexao:
        linha = conexao.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
    if linha is None:
        return None
    dados = dict(linha)
    for campo in ("payload", "resultado", "progresso"):
        if dados.get(campo) is not None:
            try:
                dados[campo] = json.loads(dados[campo])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
    return dados


def estatisticas(stale_after_seconds=30, blocked_after_seconds=60):
    """Resumo persistente da fila, incluindo a ultima falha registrada."""
    contagens = {status: 0 for status in _STATUS}
    agora = datetime.now(timezone.utc)
    with _abrir_conexao() as conexao:
        for linha in conexao.execute("SELECT status, COUNT(*) AS total FROM jobs GROUP BY status"):
            contagens[linha["status"]] = int(linha["total"])
        ultima = conexao.execute(
            """
            SELECT id, tipo, erro, concluido_em
            FROM jobs WHERE status = 'failed' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        oldest_pending = conexao.execute(
            "SELECT criado_em FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        oldest_processing = conexao.execute(
            "SELECT iniciado_em FROM jobs WHERE status = 'processing' ORDER BY iniciado_em LIMIT 1"
        ).fetchone()
        heartbeats = [dict(row) for row in conexao.execute(
            "SELECT * FROM worker_heartbeat ORDER BY atualizado_em DESC"
        ).fetchall()]
    contagens["total"] = sum(contagens.get(status, 0) for status in _STATUS)
    contagens["ultima_falha"] = dict(ultima) if ultima is not None else None
    pending_age = _idade_segundos(oldest_pending["criado_em"], agora) if oldest_pending else None
    processing_age = _idade_segundos(oldest_processing["iniciado_em"], agora) if oldest_processing else None
    workers = []
    live_workers = 0
    live_idle_workers = 0
    for heartbeat in heartbeats:
        age = _idade_segundos(heartbeat.get("atualizado_em"), agora)
        stale = age is None or age >= max(1, float(stale_after_seconds))
        pid_alive = pid_ativo(heartbeat.get("pid")) if heartbeat.get("pid") else None
        live = not stale and pid_alive is not False
        live_workers += int(live)
        live_idle_workers += int(live and heartbeat.get("status") == "idle")
        workers.append({
            "worker_id": heartbeat.get("worker_id"),
            "status": heartbeat.get("status"),
            "job_id": heartbeat.get("job_id"),
            "heartbeat_age_seconds": None if age is None else round(age, 3),
            "stale": stale,
            "pid_alive": pid_alive,
            "detail": heartbeat.get("detalhe"),
        })
    contagens["oldest_pending_seconds"] = None if pending_age is None else round(pending_age, 3)
    contagens["oldest_processing_seconds"] = None if processing_age is None else round(processing_age, 3)
    contagens["workers"] = workers
    contagens["live_workers"] = live_workers
    contagens["live_idle_workers"] = live_idle_workers
    contagens["head_of_line_blocked"] = bool(
        contagens.get("pending", 0)
        and contagens.get("processing", 0)
        and processing_age is not None
        and processing_age >= max(1, float(blocked_after_seconds))
        and live_idle_workers == 0
    )
    return contagens
