#!/usr/bin/env python3
"""
queue.py
--------
Fila persistente de eventos entre o Flask (web/routes.py) e o Worker
(engine/worker.py).

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

from engine.process_utils import pid_ativo


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "context", "fila.sqlite3")

_evento_disponivel = threading.Event()
_schema_lock = threading.Lock()
_schemas_prontos = set()
_STATUS = ("pending", "processing", "completed", "failed", "cancelled")
_AGENT_STATUS = ("running", "waiting_user", "completed", "blocked", "failed")
_NAO_INFORMADO = object()


def _parse_utc(valor):
    if not valor:
        return None
    try:
        instante = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # Bancos antigos ou editados manualmente podem conter timestamps sem
    # timezone. Trate-os como UTC em vez de derrubar /status na subtracao.
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def _idade_segundos(valor, agora=None):
    dt = _parse_utc(valor)
    if dt is None:
        return None
    agora = agora or datetime.now(timezone.utc)
    return max(0.0, (agora - dt).total_seconds())


def _pid_ativo(pid):
    """Compatibilidade interna para o probe central e seguro de PID."""
    return pid_ativo(pid)


def _agora_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _serializar(valor):
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"), default=str)


def _inicializar_schema(conexao, caminho_banco):
    """Cria/migra o schema uma vez por arquivo, nao em toda conexao."""
    with _schema_lock:
        if caminho_banco in _schemas_prontos:
            return
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
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
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs(status, id)"
        )
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_tasks (
                task_id TEXT PRIMARY KEY,
                objetivo TEXT NOT NULL,
                modo TEXT NOT NULL,
                status TEXT NOT NULL,
                projeto_hash TEXT,
                source_job_id INTEGER,
                estado TEXT,
                continuacao TEXT,
                acao_pendente TEXT,
                orcamento_restante INTEGER,
                pergunta TEXT,
                resultado TEXT,
                causa_fallback TEXT,
                auditoria TEXT NOT NULL DEFAULT '[]',
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL,
                expira_em TEXT,
                concluido_em TEXT
            )
            """
        )
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_updated "
            "ON agent_tasks(status, atualizado_em)"
        )
        conexao.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_heartbeat (
                worker_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                job_id INTEGER,
                atualizado_em TEXT NOT NULL,
                detalhe TEXT,
                pid INTEGER
            )
            """
        )
        colunas_jobs = {row[1] for row in conexao.execute("PRAGMA table_info(jobs)")}
        if "worker_id" not in colunas_jobs:
            conexao.execute("ALTER TABLE jobs ADD COLUMN worker_id TEXT")
        if "progresso" not in colunas_jobs:
            conexao.execute("ALTER TABLE jobs ADD COLUMN progresso TEXT")
        if "progresso_seq" not in colunas_jobs:
            conexao.execute(
                "ALTER TABLE jobs ADD COLUMN progresso_seq INTEGER NOT NULL DEFAULT 0"
            )
        if "cancel_requested" not in colunas_jobs:
            conexao.execute(
                "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
            )
        if "cancel_reason" not in colunas_jobs:
            conexao.execute("ALTER TABLE jobs ADD COLUMN cancel_reason TEXT")
        colunas_hb = {row[1] for row in conexao.execute("PRAGMA table_info(worker_heartbeat)")}
        if "pid" not in colunas_hb:
            conexao.execute("ALTER TABLE worker_heartbeat ADD COLUMN pid INTEGER")
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
    _inicializar_schema(conexao, caminho_banco)
    return conexao


@contextmanager
def _abrir_conexao():
    conexao = _conectar()
    try:
        yield conexao
    finally:
        conexao.close()


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


def obter_heartbeat(worker_id=None):
    with _abrir_conexao() as conexao:
        if worker_id is None:
            linhas = conexao.execute(
                "SELECT * FROM worker_heartbeat ORDER BY atualizado_em DESC"
            ).fetchall()
            return [dict(linha) for linha in linhas]
        linha = conexao.execute(
            "SELECT * FROM worker_heartbeat WHERE worker_id = ?", (str(worker_id),),
        ).fetchone()
    return dict(linha) if linha is not None else None


def adicionar(evento):
    """Persiste um evento e devolve o ID numerico do job criado."""
    if not isinstance(evento, dict):
        raise TypeError("evento precisa ser um dict")
    tipo = str(evento.get("tipo") or "").strip()
    if not tipo:
        raise ValueError("evento precisa informar 'tipo'")

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


def concluir(job_id, resultado=None, resumo_trabalho=None, duracao_segundos=None):
    """Marca um job reservado como concluido e persiste seu resultado.

    ``resumo_trabalho`` e um relato operacional publico e estruturado. Ele fica
    junto do progresso do job, separado do resultado completo do Engine.
    """
    motivo_cancelamento = cancelamento_solicitado(job_id)
    if motivo_cancelamento:
        marcar_cancelado(job_id, motivo_cancelamento)
        return False

    campos = {}
    if isinstance(resumo_trabalho, dict):
        campos["work_summary"] = resumo_trabalho
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


def falhar(job_id, erro, resultado=None, resumo_trabalho=None, duracao_segundos=None):
    """Marca um job reservado como falho e conserva erro + resultado seguro.

    ``resultado`` e opcional para manter compatibilidade com excecoes do Worker.
    Quando o Engine devolve um estado estruturado ``status=failed`` sem levantar,
    ele e persistido aqui para que a API consiga explicar a falha sem transformar
    diagnostico de transporte em fala do assistente no historico.
    """
    motivo_cancelamento = cancelamento_solicitado(job_id)
    if motivo_cancelamento:
        marcar_cancelado(job_id, motivo_cancelamento, resultado=resultado)
        return False

    detalhe = f"{type(erro).__name__}: {erro}" if isinstance(erro, BaseException) else str(erro)
    campos = {"error": detalhe[:500]}
    if isinstance(resumo_trabalho, dict):
        campos["work_summary"] = resumo_trabalho
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
            morto = linha["pid"] is not None and not _pid_ativo(linha["pid"])
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
        pid_alive = _pid_ativo(heartbeat.get("pid")) if heartbeat.get("pid") else None
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
    with _abrir_conexao() as conexao:
        tarefas_agente = {status: 0 for status in _AGENT_STATUS}
        for linha in conexao.execute(
            "SELECT status, COUNT(*) AS total FROM agent_tasks GROUP BY status"
        ):
            tarefas_agente[linha["status"]] = int(linha["total"])
    tarefas_agente["total"] = sum(tarefas_agente.values())
    contagens["agent_tasks"] = tarefas_agente
    return contagens


# ---------------------------------------------------------------------------
# Atualizacao 49 -- ciclo persistente das tarefas do Agente
# ---------------------------------------------------------------------------

def _desserializar_json(valor, default=None):
    if valor is None:
        return default
    try:
        return json.loads(valor)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _tarefa_agente_publica(linha):
    if linha is None:
        return None
    dados = dict(linha)
    for campo in ("estado", "continuacao", "acao_pendente", "resultado", "auditoria"):
        dados[campo] = _desserializar_json(
            dados.get(campo), [] if campo == "auditoria" else None,
        )
    return dados


def criar_tarefa_agente(objetivo, modo, projeto_hash=None, task_id=None, source_job_id=None):
    """Cria uma tarefa duravel ou devolve a tarefa idempotente ja existente."""
    task_id = str(task_id or uuid.uuid4().hex)
    objetivo = str(objetivo or "")
    modo = str(modo or "analyze")
    agora = _agora_utc()
    evento = {
        "em": agora,
        "tipo": "task_created",
        "status": "running",
    }
    with _abrir_conexao() as conexao:
        conexao.execute(
            """
            INSERT OR IGNORE INTO agent_tasks (
                task_id, objetivo, modo, status, projeto_hash, source_job_id,
                auditoria, criado_em, atualizado_em
            ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                task_id, objetivo, modo, projeto_hash,
                int(source_job_id) if source_job_id is not None else None,
                _serializar([evento]), agora, agora,
            ),
        )
        linha = conexao.execute(
            "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,),
        ).fetchone()
    return _tarefa_agente_publica(linha)


def atualizar_tarefa_agente(
    task_id, *, status=None, estado=_NAO_INFORMADO,
    continuacao=_NAO_INFORMADO, acao_pendente=_NAO_INFORMADO,
    orcamento_restante=_NAO_INFORMADO, pergunta=_NAO_INFORMADO,
    resultado=_NAO_INFORMADO, causa_fallback=_NAO_INFORMADO,
    expira_em=_NAO_INFORMADO, evento=None,
):
    """Atualiza snapshot e auditoria na mesma transacao SQLite."""
    if status is not None and status not in _AGENT_STATUS:
        raise ValueError(f"status de tarefa do agente invalido: {status}")
    task_id = str(task_id)
    agora = _agora_utc()
    with _abrir_conexao() as conexao:
        conexao.execute("BEGIN IMMEDIATE")
        linha = conexao.execute(
            "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,),
        ).fetchone()
        if linha is None:
            conexao.rollback()
            return False

        campos = ["atualizado_em = ?"]
        valores = [agora]
        atualizacoes = {
            "estado": estado,
            "continuacao": continuacao,
            "acao_pendente": acao_pendente,
            "orcamento_restante": orcamento_restante,
            "pergunta": pergunta,
            "resultado": resultado,
            "causa_fallback": causa_fallback,
            "expira_em": expira_em,
        }
        if status is not None:
            campos.append("status = ?")
            valores.append(status)
            if status in ("completed", "blocked", "failed"):
                campos.append("concluido_em = ?")
                valores.append(agora)
            else:
                campos.append("concluido_em = NULL")
        for nome, valor in atualizacoes.items():
            if valor is _NAO_INFORMADO:
                continue
            campos.append(f"{nome} = ?")
            if nome in ("estado", "continuacao", "acao_pendente", "resultado"):
                valores.append(None if valor is None else _serializar(valor))
            else:
                valores.append(valor)

        auditoria = _desserializar_json(linha["auditoria"], [])
        if evento is not None:
            entrada = dict(evento) if isinstance(evento, dict) else {"tipo": str(evento)}
            entrada.setdefault("em", agora)
            if status is not None:
                entrada.setdefault("status", status)
            auditoria.append(entrada)
            campos.append("auditoria = ?")
            valores.append(_serializar(auditoria))

        valores.append(task_id)
        conexao.execute(
            f"UPDATE agent_tasks SET {', '.join(campos)} WHERE task_id = ?",
            valores,
        )
        conexao.commit()
    return True


def obter_tarefa_agente(task_id):
    with _abrir_conexao() as conexao:
        linha = conexao.execute(
            "SELECT * FROM agent_tasks WHERE task_id = ?", (str(task_id),),
        ).fetchone()
    return _tarefa_agente_publica(linha)


def listar_tarefas_agente(status=None, limite=50):
    limite = max(1, min(int(limite), 200))
    with _abrir_conexao() as conexao:
        if status is None:
            linhas = conexao.execute(
                "SELECT * FROM agent_tasks ORDER BY atualizado_em DESC LIMIT ?",
                (limite,),
            ).fetchall()
        else:
            if status not in _AGENT_STATUS:
                raise ValueError(f"status de tarefa do agente invalido: {status}")
            linhas = conexao.execute(
                "SELECT * FROM agent_tasks WHERE status = ? "
                "ORDER BY atualizado_em DESC LIMIT ?",
                (status, limite),
            ).fetchall()
    return [_tarefa_agente_publica(linha) for linha in linhas]


def cancelar_tarefa_agente(task_id, motivo="cancelada pelo usuario"):
    """Limpa somente a acao executavel; snapshot e auditoria ficam guardados."""
    return atualizar_tarefa_agente(
        task_id,
        status="blocked",
        continuacao=None,
        acao_pendente=None,
        pergunta=None,
        causa_fallback="cancelled",
        evento={"tipo": "task_cancelled", "motivo": str(motivo)},
    )


def expirar_tarefas_agente(agora=None):
    """Expira esperas vencidas sem apagar seu historico de auditoria."""
    agora = str(agora or _agora_utc())
    with _abrir_conexao() as conexao:
        ids = [
            linha["task_id"] for linha in conexao.execute(
                "SELECT task_id FROM agent_tasks "
                "WHERE status = 'waiting_user' AND expira_em IS NOT NULL AND expira_em <= ?",
                (agora,),
            ).fetchall()
        ]
    for task_id in ids:
        atualizar_tarefa_agente(
            task_id,
            status="blocked",
            continuacao=None,
            acao_pendente=None,
            pergunta=None,
            causa_fallback="expired",
            evento={"tipo": "task_expired"},
        )
    return len(ids)


def cancelar_tarefas_agente_por_job(source_job_id, motivo="job principal cancelado"):
    """Cancela continuacoes duraveis criadas pelo job principal."""
    with _abrir_conexao() as conexao:
        linhas = conexao.execute(
            """
            SELECT task_id FROM agent_tasks
            WHERE source_job_id = ? AND status IN ('running', 'waiting_user')
            """,
            (int(source_job_id),),
        ).fetchall()
    total = 0
    for linha in linhas:
        total += int(bool(cancelar_tarefa_agente(linha["task_id"], motivo=motivo)))
    return total


def recuperar_tarefas_agente_interrompidas():
    """Classifica checkpoints deixados em ``running`` apos reinicio.

    Leituras sao retomaveis porque sao idempotentes. WRITE nunca e recolocada
    automaticamente: vira espera recuperavel e sera revalidada contra o disco
    antes de qualquer nova tentativa.
    """
    prontas = []
    protegidas = []
    for tarefa in listar_tarefas_agente(status="running", limite=200):
        acao = tarefa.get("acao_pendente") or {}
        permissao = acao.get("permission")
        if permissao == "WRITE":
            continuacao = dict(tarefa.get("continuacao") or {})
            continuacao["recovery_required"] = True
            ferramenta = dict(continuacao.get("tool_pendente") or {})
            ferramenta["recovery_required"] = True
            continuacao["tool_pendente"] = ferramenta
            atualizar_tarefa_agente(
                tarefa["task_id"],
                status="waiting_user",
                continuacao=continuacao,
                pergunta=(
                    tarefa.get("pergunta")
                    or "A Eyle reiniciou durante uma escrita. Confirme para verificar o estado final antes de continuar."
                ),
                causa_fallback="write_recovery_requires_verification",
                evento={"tipo": "write_not_requeued_after_restart"},
            )
            protegidas.append(tarefa["task_id"])
        else:
            atualizar_tarefa_agente(
                tarefa["task_id"],
                evento={"tipo": "idempotent_resume_ready"},
            )
            prontas.append(tarefa["task_id"])
    return {"idempotentes": prontas, "writes_protegidas": protegidas}
