#!/usr/bin/env python3
"""Execucao de comandos de projeto com isolamento e limites.

Atualizacao 28. O modulo nao oferece um fallback silencioso para um processo
comum quando a politica exige rede bloqueada: sem Bubblewrap (Linux) ou uma
imagem Docker explicitamente configurada, a execucao falha fechada.

O projeto continua gravavel porque suites de teste costumam criar caches e
artefatos. Bubblewrap/Docker oferecem isolamento forte. Em Windows, um modo
``trusted_local`` explicitamente autorizado pode executar somente comandos da
allowlist, sem shell, em snapshot temporario, com timeout e ambiente filtrado.
Esse modo nao promete isolamento de rede nem limites de kernel.
"""
import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import uuid


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
    if hasattr(shlex, "join"):
        return shlex.join(argv)
    return " ".join(shlex.quote(item) for item in argv)


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
    total_itens = 0
    total_bytes = 0
    for raiz, pastas, arquivos in os.walk(caminho_projeto, followlinks=False):
        for nome in list(pastas) + list(arquivos):
            caminho = os.path.join(raiz, nome)
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
    try:
        shutil.copytree(caminho_projeto, destino, symlinks=True)
    except (OSError, shutil.Error) as erro:
        temporario.cleanup()
        raise ErroSandbox(f"nao foi possivel criar copia isolada do projeto: {erro}")
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
        raise ErroSandbox("Bubblewrap nao encontrado; sandbox com rede bloqueada indisponivel")

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
    for arquivo in ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/ld.so.cache", "/etc/localtime"):
        if os.path.exists(arquivo):
            comando.extend(["--ro-bind", arquivo, arquivo])

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


def _comando_docker(caminho_projeto, argv, cfg, limites):
    docker = shutil.which("docker")
    imagem = cfg.get("imagem_docker")
    if not docker:
        raise ErroSandbox("Docker nao encontrado")
    if not isinstance(imagem, str) or not imagem.strip():
        raise ErroSandbox("backend Docker exige sandbox.imagem_docker explicita")

    nome = f"eyle-sandbox-{uuid.uuid4().hex[:12]}"
    comando = [
        docker, "run", "--rm", "--pull", "never", "--name", nome,
        "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--cpus", str(cfg.get("cpus", 1.0)),
        "--memory", f"{limites['memoria_mb']}m",
        "--pids-limit", str(limites["processos"]),
        "--ulimit", f"nofile={limites['arquivos']}:{limites['arquivos']}",
        "--ulimit", (
            f"fsize={limites['arquivo_mb'] * 1024 * 1024}:"
            f"{limites['arquivo_mb'] * 1024 * 1024}"
        ),
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
        "--mount", f"type=bind,source={os.path.realpath(caminho_projeto)},target=/workspace",
        "--workdir", "/workspace",
        "--env", "HOME=/tmp",
        "--env", "TMPDIR=/tmp",
        "--env", "EYLE_SANDBOX=1",
    ]
    comando.extend(["--network", "none" if cfg.get("bloquear_rede", True) else "bridge"])
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        comando.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    comando.append(imagem)
    comando.extend(argv)
    return comando, (docker, nome)


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
            "backend 'processo' nao bloqueia rede; use Bubblewrap/Docker ou autorize rede explicitamente"
        )
    if os.name != "posix":
        raise ErroSandbox("backend 'processo' com limites requer sistema POSIX e prlimit")
    return _prefixo_prlimit(limites) + argv, None


def _matar_grupo(processo):
    try:
        if os.name == "posix":
            os.killpg(processo.pid, signal.SIGKILL)
        else:
            processo.kill()
    except (OSError, ProcessLookupError):
        pass


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
        backend = str(cfg.get("backend", "auto")).lower()

        if backend == "auto":
            if os.name == "posix" and shutil.which("bwrap"):
                backend = "bwrap"
            elif shutil.which("docker") and cfg.get("imagem_docker"):
                backend = "docker"
            elif os.name == "nt" and cfg.get("allow_trusted_local") is True:
                backend = "trusted_local"
            elif cfg.get("bloquear_rede", True) is False:
                backend = "processo"
            else:
                raise ErroSandbox(
                    "nenhum backend seguro disponivel (instale Bubblewrap/configure Docker "
                    "ou autorize trusted_local no Windows)"
                )

        if backend == "bwrap":
            argv_exec, limpeza_docker = _comando_bwrap(caminho_execucao, argv, cfg, limites)
        elif backend == "docker":
            argv_exec, limpeza_docker = _comando_docker(caminho_execucao, argv, cfg, limites)
        elif backend in ("process", "processo"):
            argv_exec, limpeza_docker = _comando_processo(caminho_execucao, argv, cfg, limites)
        elif backend in ("trusted_local", "local_confiavel"):
            backend = "trusted_local"
            argv_exec, limpeza_docker = _comando_trusted_local(
                caminho_execucao, argv, cfg, limites,
            )
        else:
            raise ErroSandbox(f"backend de sandbox desconhecido: {backend}")
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
        if excedeu_timeout and limpeza_docker:
            docker, nome = limpeza_docker
            try:
                subprocess.run(
                    [docker, "rm", "-f", nome],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    arquivo_saida.seek(0, os.SEEK_END)
    tamanho = arquivo_saida.tell()
    max_retorno = min(limites["saida_kb"] * 1024, 64 * 1024)
    arquivo_saida.seek(max(0, tamanho - max_retorno))
    saida = arquivo_saida.read().decode("utf-8", errors="replace")
    arquivo_saida.close()
    if temporario_projeto is not None:
        temporario_projeto.cleanup()

    if excedeu_timeout:
        return {"executado": True, "ok": False, "codigo": codigo,
                "saida": saida, "erro": f"timeout de {timeout}s excedido",
                "backend": backend,
                "network_isolated": backend in {"bwrap", "docker"}}
    return {"executado": True, "ok": codigo == 0, "codigo": codigo,
            "saida": saida, "erro": None, "backend": backend,
            "network_isolated": backend in {"bwrap", "docker"}}
