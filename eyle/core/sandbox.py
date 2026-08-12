#!/usr/bin/env python3
"""Project command execution with explicit isolation contracts.

``run_command`` accepts only strong disposable backends (Microsandbox, Docker or Bubblewrap).
The supervised ``run_tests`` path has its own narrower allowlisted policy and may
use an explicitly configured local process backend when its network/resource
contract permits it. Neither path silently upgrades local execution into strong
isolation, and real-workspace writes remain outside this module's authority.
"""
import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import uuid

DEFAULT_OCI_IMAGE = "python:3.12-slim"

from .execution_context import current_execution
from .workspace_policy import build_protected_resource_index, is_protected_workspace_resource


class ErroSandbox(ValueError):
    """Configuracao ou comando recusado antes de iniciar qualquer processo."""


def _argv(comando):
    if isinstance(comando, str):
        try:
            partes = shlex.split(comando, posix=os.name != "nt")
        except ValueError as erro:
            raise ErroSandbox(f"comando de teste invalido: {erro}")
    elif isinstance(comando, (list, tuple)):
        partes = list(comando)
    else:
        raise ErroSandbox("comando de teste deve ser texto ou lista de argumentos")

    if not partes or any(not isinstance(item, str) or not item or "\x00" in item for item in partes):
        raise ErroSandbox("comando de teste vazio ou com argumento invalido")
    return partes


def _texto_comando(argv):
    return shlex.join(argv)


def _config_para_projeto(caminho_projeto, cfg_sandbox):
    """Combina a politica geral com override confiavel por caminho real.

    A configuracao fica fora do repositorio analisado. Assim um repositorio
    malicioso nao pode editar a propria allowlist. Overrides usam o caminho
    absoluto real como chave em ``sandbox.projetos``.
    """
    cfg_sandbox = cfg_sandbox if isinstance(cfg_sandbox, dict) else {}
    resultado = {k: v for k, v in cfg_sandbox.items() if k != "projetos"}
    projetos = cfg_sandbox.get("projetos")
    if isinstance(projetos, dict):
        especifica = projetos.get(os.path.realpath(caminho_projeto))
        if isinstance(especifica, dict):
            resultado.update(especifica)
    return resultado


def _validar_allowlist(argv, permitidos):
    if not isinstance(permitidos, list) or not permitidos:
        raise ErroSandbox("sandbox sem comandos_permitidos; execucao recusada")

    for permitido in permitidos:
        try:
            prefixo = _argv(permitido)
        except ErroSandbox:
            continue
        if argv[:len(prefixo)] == prefixo:
            return
    raise ErroSandbox(
        f"comando '{_texto_comando(argv)}' nao consta na allowlist deste projeto"
    )


def _limites(cfg):
    def inteiro(nome, padrao, minimo=1):
        valor = cfg.get(nome, padrao)
        if isinstance(valor, bool):
            raise ErroSandbox(f"sandbox.{nome} deve ser inteiro")
        try:
            valor = int(valor)
        except (TypeError, ValueError):
            raise ErroSandbox(f"sandbox.{nome} deve ser inteiro")
        if valor < minimo:
            raise ErroSandbox(f"sandbox.{nome} deve ser >= {minimo}")
        return valor

    return {
        "timeout": inteiro("timeout_segundos", 60),
        "cpu": inteiro("cpu_segundos", 60),
        "memoria_mb": inteiro("memoria_mb", 1024, 64),
        "processos": inteiro("max_processos", 128),
        "arquivos": inteiro("max_arquivos_abertos", 256, 16),
        "saida_kb": inteiro("max_saida_kb", 1024, 16),
        "arquivo_mb": inteiro("max_arquivo_mb", 64, 1),
        "arquivos_projeto": inteiro("max_arquivos_projeto", 100000),
        "tamanho_projeto_mb": inteiro("max_tamanho_projeto_mb", 2048, 1),
    }


def _copiar_projeto(caminho_projeto, limites):
    """Cria snapshot gravavel sem seguir symlinks nem copiar arquivos especiais."""
    protected_index = build_protected_resource_index(caminho_projeto)
    total_itens = 0
    total_bytes = 0
    for raiz, pastas, arquivos in os.walk(caminho_projeto, followlinks=False):
        for nome in list(pastas) + list(arquivos):
            caminho = os.path.join(raiz, nome)
            relativo = os.path.relpath(caminho, caminho_projeto).replace(os.sep, "/")
            if is_protected_workspace_resource(caminho_projeto, relativo, index=protected_index):
                continue
            try:
                info = os.lstat(caminho)
                modo = info.st_mode
                tamanho = info.st_size
            except OSError as erro:
                raise ErroSandbox(f"nao foi possivel inspecionar projeto para o sandbox: {erro}")
            total_itens += 1
            if total_itens > limites["arquivos_projeto"]:
                raise ErroSandbox("projeto excede max_arquivos_projeto do sandbox")
            if stat.S_ISREG(modo):
                total_bytes += tamanho
            elif not (stat.S_ISDIR(modo) or stat.S_ISLNK(modo)):
                raise ErroSandbox(f"arquivo especial recusado no sandbox: {os.path.relpath(caminho, caminho_projeto)}")
            if total_bytes > limites["tamanho_projeto_mb"] * 1024 * 1024:
                raise ErroSandbox("projeto excede max_tamanho_projeto_mb do sandbox")

    temporario = tempfile.TemporaryDirectory(prefix="eyle-sandbox-projeto-")
    destino = os.path.join(temporario.name, "workspace")
    raiz_real = os.path.realpath(caminho_projeto)

    protected_omitted = set()

    def ignore_protected_resources(directory, names):
        ignored = []
        for name in names:
            absolute = os.path.join(directory, name)
            relative = os.path.relpath(absolute, raiz_real).replace(os.sep, "/")
            if is_protected_workspace_resource(caminho_projeto, relative, index=protected_index):
                ignored.append(name)
                protected_omitted.add(relative)
                continue
            try:
                # Never preserve repository symlinks into a host-executed snapshot.
                # Even an apparently internal absolute symlink could point back to
                # the real workspace when the supervised process backend runs on
                # the host. Capabilities can observe symlinks separately; command
                # execution gets a closed physical snapshot instead.
                if os.path.islink(absolute):
                    ignored.append(name)
            except OSError:
                continue
        return ignored

    try:
        shutil.copytree(caminho_projeto, destino, symlinks=True, ignore=ignore_protected_resources)
    except (OSError, shutil.Error) as erro:
        temporario.cleanup()
        raise ErroSandbox(f"nao foi possivel criar copia isolada do projeto: {erro}")
    temporario.protected_resources_omitted = sorted(protected_omitted)
    return destino, temporario


def _prefixo_prlimit(limites):
    prlimit = shutil.which("prlimit")
    if not prlimit:
        raise ErroSandbox("backend local sem 'prlimit' para impor limites de recurso")
    memoria = limites["memoria_mb"] * 1024 * 1024
    tamanho_arquivo = limites["arquivo_mb"] * 1024 * 1024
    return [
        prlimit,
        f"--cpu={limites['cpu']}:{limites['cpu'] + 1}",
        f"--as={memoria}:{memoria}",
        f"--nproc={limites['processos']}:{limites['processos']}",
        f"--nofile={limites['arquivos']}:{limites['arquivos']}",
        f"--fsize={tamanho_arquivo}:{tamanho_arquivo}",
        "--core=0:0",
        "--",
    ]


def _comando_bwrap(caminho_projeto, argv, cfg, limites):
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise ErroSandbox("Bubblewrap was not found; sandbox com rede bloqueada indisponivel")

    comando = _prefixo_prlimit(limites) + [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
    ]
    if cfg.get("bloquear_rede", True) is False:
        comando.append("--share-net")

    # Apenas runtime e bibliotecas do sistema ficam visiveis, sempre read-only.
    for origem in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt"):
        if os.path.exists(origem):
            comando.extend(["--ro-bind", origem, origem])

    comando.extend(["--dir", "/etc"])
    for arquivo in ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/ld.so.cache", "/etc/localtime", "/etc/resolv.conf", "/etc/hosts", "/etc/ca-certificates.conf"):
        if os.path.exists(arquivo):
            comando.extend(["--ro-bind", arquivo, arquivo])

    if os.path.isdir("/etc/ssl/certs"):
        comando.extend(["--dir", "/etc/ssl", "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs"])

    caminho_real = os.path.realpath(caminho_projeto)
    comando.extend([
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/home",
        "--dir", "/run",
        "--bind", caminho_real, "/workspace",
        "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "PATH", os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "--setenv", "HOME", "/tmp/eyle-home",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", os.environ.get("LANG", "C.UTF-8"),
        "--setenv", "EYLE_SANDBOX", "1",
        "--",
    ])
    comando.extend(argv)
    return comando, None


def _oci_image(cfg):
    image = cfg.get("imagem_oci") or DEFAULT_OCI_IMAGE
    if not isinstance(image, str) or not image.strip():
        raise ErroSandbox("sandbox.imagem_oci precisa ser string nao vazia")
    return image.strip()


def _microsandbox_available():
    from .microsandbox_backend import sdk_available
    return sdk_available()


def _ensure_microsandbox_session(caminho_projeto, cfg, limites, *, bloquear_rede):
    from .microsandbox_backend import MicrosandboxBackendError, MicrosandboxSession

    execution = current_execution()
    if execution is not None and execution.sandbox_microsandbox_session is not None:
        return execution.sandbox_microsandbox_session, False
    try:
        session = MicrosandboxSession(
            caminho_projeto, cfg, limites, block_network=bloquear_rede,
        )
    except MicrosandboxBackendError as exc:
        raise ErroSandbox(str(exc)) from exc
    if execution is not None:
        execution.sandbox_microsandbox_session = session
        execution.sandbox_backend = "microsandbox"
        return session, False
    return session, True


def _ensure_docker_container(caminho_projeto, cfg, limites):
    """Create one writable Docker laboratory per physical job.

    The real workspace is never mounted; ``caminho_projeto`` is already the
    disposable copied snapshot. Container rootfs + snapshot persist until the
    ExecutionContext cleanup boundary, allowing apt/pip/npm/toolchains to remain
    available across multiple run_command calls.
    """
    docker = shutil.which("docker")
    if not docker:
        raise ErroSandbox("Docker was not found")
    execution = current_execution()
    if execution is not None and execution.sandbox_container_name:
        return docker, execution.sandbox_container_name, False

    image = _oci_image(cfg)
    name = f"eyle-sandbox-{uuid.uuid4().hex[:12]}"
    command = [
        docker, "run", "-d", "--pull", "missing", "--name", name,
        "--security-opt", "no-new-privileges",
        "--cpus", str(cfg.get("cpus", 1.0)),
        "--memory", f"{limites['memoria_mb']}m",
        "--pids-limit", str(limites["processos"]),
        "--ulimit", f"nofile={limites['arquivos']}:{limites['arquivos']}",
        "--ulimit", (
            f"fsize={limites['arquivo_mb'] * 1024 * 1024}:"
            f"{limites['arquivo_mb'] * 1024 * 1024}"
        ),
        "--tmpfs", "/tmp:rw,nosuid,size=256m",
        "--mount", f"type=bind,source={os.path.realpath(caminho_projeto)},target=/workspace",
        "--workdir", "/workspace",
        "--env", "HOME=/root", "--env", "TMPDIR=/tmp", "--env", "EYLE_SANDBOX=1",
        "--network", "bridge",
        image, "/bin/sh", "-lc", "while :; do sleep 3600; done",
    ]
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=max(30, min(180, limites["timeout"])), check=False, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ErroSandbox(f"nao foi possivel iniciar Docker sandbox: {exc}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "docker run falhou").strip()[-1200:]
        raise ErroSandbox(f"Docker sandbox indisponivel: {detail}")
    if execution is not None:
        execution.sandbox_container_name = name
        execution.sandbox_docker_binary = docker
        execution.sandbox_backend = "docker"
    return docker, name, execution is None


def _comando_docker(caminho_projeto, shell_command, rel_cwd, cfg, limites):
    docker, name, cleanup_after = _ensure_docker_container(caminho_projeto, cfg, limites)
    workdir = "/workspace" if rel_cwd == "." else "/workspace/" + rel_cwd
    argv = [docker, "exec", "-w", workdir, name, "/bin/sh", "-lc", shell_command]
    return argv, ((docker, name) if cleanup_after else None)


def _ambiente_trusted_local():
    """Ambiente minimo para subprocesso local explicitamente confiado."""
    permitidas = (
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
        "TEMP", "TMP", "USERPROFILE", "HOME", "LANG", "LC_ALL",
        "VIRTUAL_ENV",
    )
    env = {chave: os.environ[chave] for chave in permitidas if os.environ.get(chave)}
    env["EYLE_SANDBOX"] = "trusted_local"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _comando_trusted_local(caminho_projeto, argv, cfg, limites):
    if cfg.get("allow_trusted_local") is not True:
        raise ErroSandbox(
            "backend trusted_local exige sandbox.allow_trusted_local=true"
        )
    # argv ja passou pela allowlist e sempre sera executado com shell=False.
    return list(argv), None


def _comando_processo(caminho_projeto, argv, cfg, limites):
    if cfg.get("bloquear_rede", True):
        raise ErroSandbox(
            "backend 'process' does not block network; use Microsandbox/Bubblewrap/Docker or explicitly allow network access"
        )
    if os.name != "posix":
        raise ErroSandbox("backend 'process' with resource limits requires POSIX and prlimit")
    return _prefixo_prlimit(limites) + argv, None


def _matar_grupo(processo):
    try:
        if os.name == "posix":
            os.killpg(processo.pid, signal.SIGKILL)
        else:
            processo.kill()
    except (OSError, ProcessLookupError):
        pass



def _strong_backend(cfg):
    """Resolve a backend satisfying the unrestricted sandbox isolation contract."""
    backend = str(cfg.get("backend", "auto")).lower()
    if backend == "auto":
        if _microsandbox_available():
            return "microsandbox"
        if shutil.which("docker"):
            return "docker"
        if os.name == "posix" and shutil.which("bwrap"):
            return "bwrap"
        raise ErroSandbox(
            "run_command requires Microsandbox, Docker or Bubblewrap; "
            "trusted_local/process are not strong isolation"
        )
    if backend not in {"microsandbox", "bwrap", "docker"}:
        raise ErroSandbox(
            "run_command accepts only strong backends: microsandbox, docker or bwrap"
        )
    if backend == "microsandbox" and not _microsandbox_available():
        raise ErroSandbox("Microsandbox SDK was not found")
    if backend == "docker" and not shutil.which("docker"):
        raise ErroSandbox("Docker was not found")
    if backend == "bwrap" and not (os.name == "posix" and shutil.which("bwrap")):
        raise ErroSandbox("Bubblewrap was not found")
    return backend


def _safe_sandbox_cwd(workspace, cwd):
    raw = str(cwd or ".").replace("\\", "/").strip() or "."
    if os.path.isabs(raw):
        raise ErroSandbox("cwd do sandbox deve ser relativo ao snapshot")
    target = os.path.realpath(os.path.join(workspace, raw))
    root = os.path.realpath(workspace)
    if target != root and not target.startswith(root + os.sep):
        raise ErroSandbox("cwd tenta escapar do snapshot do sandbox")
    if not os.path.isdir(target):
        raise ErroSandbox("cwd do sandbox nao existe")
    return target


def _agent_sandbox_workspace(caminho_projeto, cfg, limites):
    """Return one writable snapshot that persists for the current job only."""
    execution = current_execution()
    if execution is not None and execution.sandbox_workspace_path and os.path.isdir(execution.sandbox_workspace_path):
        return execution.sandbox_workspace_path, execution.sandbox_tempdir
    workspace, tempdir = _copiar_projeto(caminho_projeto, limites)
    if execution is not None:
        execution.sandbox_workspace_path = workspace
        execution.sandbox_tempdir = tempdir
        execution.sandbox_protected_resources_omitted = len(getattr(tempdir, "protected_resources_omitted", []) or [])
    return workspace, tempdir


def executar_comando_livre_no_sandbox(caminho_projeto, comando, cfg_sandbox=None, *, cwd=".", timeout_segundos=None):
    """Run an unrestricted shell command inside a strong, disposable project snapshot.

    The command may mutate the snapshot, install workspace-local dependencies,
    compile and access the network. It never receives the real workspace as a
    writable mount and never uses trusted_local/process backends. Snapshot state
    persists across run_command calls in the same execution and is destroyed at
    the execution boundary.
    """
    if not os.path.isdir(caminho_projeto):
        return {"executado": False, "ok": False, "codigo": None, "saida": "", "erro": "pasta do projeto nao existe"}
    try:
        cfg = _config_para_projeto(caminho_projeto, cfg_sandbox)
        cfg = dict(cfg)
        cfg["bloquear_rede"] = False
        cfg["copiar_projeto"] = True
        limites = _limites(cfg)
        if timeout_segundos is not None:
            requested = int(timeout_segundos)
            if requested < 1 or requested > limites["timeout"]:
                raise ErroSandbox(f"timeout_segundos deve estar entre 1 e {limites['timeout']}")
            limites["timeout"] = requested
        backend = _strong_backend(cfg)
        workspace, tempdir = _agent_sandbox_workspace(os.path.realpath(caminho_projeto), cfg, limites)
        cwd_host = _safe_sandbox_cwd(workspace, cwd)
        rel_cwd = os.path.relpath(cwd_host, workspace).replace(os.sep, "/")
        shell_command = str(comando or "").strip()
        if not shell_command or "\x00" in shell_command:
            raise ErroSandbox("command vazio ou invalido")
        argv = ["/bin/sh", "-lc", shell_command]
        execution = current_execution()
        if execution is not None:
            execution.sandbox_backend = backend

        if backend == "microsandbox":
            from .microsandbox_backend import MicrosandboxBackendError
            session, cleanup_session = _ensure_microsandbox_session(
                workspace, cfg, limites, bloquear_rede=False,
            )
            max_return = min(limites["saida_kb"] * 1024, 128 * 1024)
            try:
                try:
                    result = session.execute(
                        shell_command, rel_cwd=rel_cwd, timeout=limites["timeout"],
                        max_output_bytes=max_return,
                    )
                except MicrosandboxBackendError as exc:
                    raise ErroSandbox(str(exc)) from exc
            finally:
                if cleanup_session:
                    session.close()
                    if tempdir is not None:
                        tempdir.cleanup()
            if not result.executed:
                return {
                    "executado": False, "ok": False, "codigo": result.code,
                    "saida": result.output, "erro": result.error,
                }
            return {
                "executado": True,
                "ok": (result.code == 0 and not result.timed_out and not result.error),
                "codigo": result.code,
                "saida": result.output,
                "erro": result.error,
                "backend": backend,
                "network_enabled": True,
                "workspace_isolated": True,
                "workspace_transport": getattr(session, "workspace_transport", "unknown"),
                "snapshot_persists_for_job": execution is not None,
                "protected_resources_omitted": int(
                    getattr(execution, "sandbox_protected_resources_omitted", 0) or 0
                ) if execution is not None else len(
                    getattr(tempdir, "protected_resources_omitted", []) or []
                ),
                "real_workspace_changed": False,
                "cwd": rel_cwd,
            }

        if backend == "bwrap":
            argv_exec, cleanup_docker = _comando_bwrap(workspace, argv, cfg, limites)
            # _comando_bwrap always enters /workspace; override to requested relative cwd.
            if "--chdir" in argv_exec:
                idx = argv_exec.index("--chdir")
                argv_exec[idx + 1] = "/workspace" if rel_cwd == "." else "/workspace/" + rel_cwd
        else:
            argv_exec, cleanup_docker = _comando_docker(workspace, shell_command, rel_cwd, cfg, limites)
    except (ErroSandbox, ValueError) as erro:
        return {"executado": False, "ok": False, "codigo": None, "saida": "", "erro": str(erro)}

    timeout = limites["timeout"]
    output_file = tempfile.TemporaryFile(mode="w+b")
    process = None
    timed_out = False
    try:
        process = subprocess.Popen(
            argv_exec, cwd=workspace, stdin=subprocess.DEVNULL, stdout=output_file, stderr=subprocess.STDOUT,
            start_new_session=True, shell=False, env=None,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _matar_grupo(process); code = process.wait()
    except OSError as erro:
        output_file.close()
        return {"executado": False, "ok": False, "codigo": None, "saida": "", "erro": f"nao foi possivel iniciar o sandbox: {erro}"}
    finally:
        if cleanup_docker:
            docker, name = cleanup_docker
            try:
                subprocess.run([docker, "rm", "-f", name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False, shell=False)
            except (OSError, subprocess.TimeoutExpired):
                pass

    output_file.seek(0, os.SEEK_END); size = output_file.tell()
    max_return = min(limites["saida_kb"] * 1024, 128 * 1024)
    output_file.seek(max(0, size - max_return))
    output = output_file.read().decode("utf-8", errors="replace"); output_file.close()
    return {
        "executado": True, "ok": (code == 0 and not timed_out), "codigo": code, "saida": output,
        "erro": f"timeout de {timeout}s excedido" if timed_out else None, "backend": backend,
        "network_enabled": True, "workspace_isolated": True, "snapshot_persists_for_job": True,
        "protected_resources_omitted": int(getattr(current_execution(), "sandbox_protected_resources_omitted", 0) or 0),
        "real_workspace_changed": False, "cwd": rel_cwd,
    }

def _supervised_backend(cfg):
    """Resolve the supervised-test backend mechanically and fail closed."""
    backend = str((cfg or {}).get("backend", "auto")).lower()
    if backend != "auto":
        if backend not in {"microsandbox", "bwrap", "docker", "process", "trusted_local"}:
            raise ErroSandbox(f"unknown sandbox backend: {backend}")
        if backend == "microsandbox" and not _microsandbox_available():
            raise ErroSandbox("Microsandbox SDK was not found")
        return backend
    # Supervised run_tests keeps its existing auto policy. A generic OCI image
    # does not guarantee pytest/npm/tooling, so Microsandbox is explicit here
    # until a test-capable image is deliberately configured.
    if os.name == "posix" and shutil.which("bwrap"):
        return "bwrap"
    if shutil.which("docker"):
        return "docker"
    if os.name == "nt" and (cfg or {}).get("allow_trusted_local") is True:
        return "trusted_local"
    if (cfg or {}).get("bloquear_rede", True) is False:
        return "process"
    raise ErroSandbox(
        "nenhum backend supervisionado disponivel (instale Bubblewrap/configure Docker, "
        "autorize trusted_local no Windows ou configure microsandbox explicitamente com imagem de testes)"
    )


def executar_no_sandbox(caminho_projeto, comando, cfg_sandbox=None):
    """Executa ``comando`` e devolve contrato pequeno, sem levantar excecao.

    ``cfg_sandbox`` e confiavel (config.json da Eyle), nunca lido do projeto.
    A saida capturada e limitada em disco e so a cauda volta ao chamador.
    """
    if not os.path.isdir(caminho_projeto):
        return {"executado": False, "ok": False, "codigo": None,
                "saida": "", "erro": "pasta do projeto nao existe"}

    temporario_projeto = None
    try:
        cfg = _config_para_projeto(caminho_projeto, cfg_sandbox)
        argv = _argv(comando)
        _validar_allowlist(argv, cfg.get("comandos_permitidos"))
        limites = _limites(cfg)
        caminho_execucao = os.path.realpath(caminho_projeto)
        if cfg.get("copiar_projeto", True):
            caminho_execucao, temporario_projeto = _copiar_projeto(caminho_execucao, limites)
        backend = _supervised_backend(cfg)

        if backend == "microsandbox":
            from .microsandbox_backend import MicrosandboxBackendError, MicrosandboxSession
            session = None
            try:
                session = MicrosandboxSession(
                    caminho_execucao, cfg, limites,
                    block_network=bool(cfg.get("bloquear_rede", True)),
                )
                result = session.execute(
                    _texto_comando(argv), rel_cwd=".", timeout=limites["timeout"],
                    max_output_bytes=min(limites["saida_kb"] * 1024, 64 * 1024),
                )
            except MicrosandboxBackendError as exc:
                raise ErroSandbox(str(exc)) from exc
            finally:
                if session is not None:
                    session.close()
            protected_resources_omitted = len(
                getattr(temporario_projeto, "protected_resources_omitted", []) or []
            ) if temporario_projeto is not None else 0
            if temporario_projeto is not None:
                temporario_projeto.cleanup()
                temporario_projeto = None
            return {
                "executado": bool(result.executed),
                "ok": bool(result.executed and result.code == 0 and not result.timed_out and not result.error),
                "codigo": result.code,
                "saida": result.output,
                "erro": result.error,
                "backend": "microsandbox",
                "protected_resources_omitted": protected_resources_omitted,
                "network_isolated": bool(cfg.get("bloquear_rede", True)),
            }
        if backend == "bwrap":
            argv_exec, limpeza_docker = _comando_bwrap(caminho_execucao, argv, cfg, limites)
        elif backend == "docker":
            argv_exec, limpeza_docker = _comando_docker(caminho_execucao, _texto_comando(argv), ".", cfg, limites)
        elif backend == "process":
            argv_exec, limpeza_docker = _comando_processo(caminho_execucao, argv, cfg, limites)
        elif backend == "trusted_local":
            backend = "trusted_local"
            argv_exec, limpeza_docker = _comando_trusted_local(
                caminho_execucao, argv, cfg, limites,
            )
        else:
            raise ErroSandbox(f"unknown sandbox backend: {backend}")
    except ErroSandbox as erro:
        if temporario_projeto is not None:
            temporario_projeto.cleanup()
        return {"executado": False, "ok": False, "codigo": None,
                "saida": "", "erro": str(erro)}

    timeout = limites["timeout"]
    arquivo_saida = tempfile.TemporaryFile(mode="w+b")
    processo = None
    excedeu_timeout = False
    try:
        processo = subprocess.Popen(
            argv_exec,
            cwd=caminho_execucao,
            stdin=subprocess.DEVNULL,
            stdout=arquivo_saida,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            shell=False,
            env=_ambiente_trusted_local() if backend == "trusted_local" else None,
        )
        try:
            codigo = processo.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            excedeu_timeout = True
            _matar_grupo(processo)
            codigo = processo.wait()
    except OSError as erro:
        arquivo_saida.close()
        if temporario_projeto is not None:
            temporario_projeto.cleanup()
        return {"executado": False, "ok": False, "codigo": None,
                "saida": "", "erro": f"nao foi possivel iniciar o sandbox: {erro}"}
    finally:
        if limpeza_docker:
            docker, nome = limpeza_docker
            try:
                subprocess.run(
                    [docker, "rm", "-f", nome], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False, shell=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    arquivo_saida.seek(0, os.SEEK_END)
    tamanho = arquivo_saida.tell()
    max_retorno = min(limites["saida_kb"] * 1024, 64 * 1024)
    arquivo_saida.seek(max(0, tamanho - max_retorno))
    saida = arquivo_saida.read().decode("utf-8", errors="replace")
    arquivo_saida.close()
    protected_resources_omitted = len(getattr(temporario_projeto, "protected_resources_omitted", []) or []) if temporario_projeto is not None else 0
    if temporario_projeto is not None:
        temporario_projeto.cleanup()

    if excedeu_timeout:
        return {"executado": True, "ok": False, "codigo": codigo,
                "saida": saida, "erro": f"timeout de {timeout}s excedido",
                "backend": backend,
                "protected_resources_omitted": protected_resources_omitted,
                "network_isolated": backend in {"microsandbox", "bwrap", "docker"}}
    return {"executado": True, "ok": codigo == 0, "codigo": codigo,
            "saida": saida, "erro": None, "backend": backend,
            "protected_resources_omitted": protected_resources_omitted,
            "network_isolated": backend in {"microsandbox", "bwrap", "docker"}}
