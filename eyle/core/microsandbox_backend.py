#!/usr/bin/env python3
"""Microsandbox physical backend for Eyle command execution.

This module owns only provider/runtime-specific mechanics.  Core semantics do
not depend on microsandbox.  A disposable Eyle workspace snapshot is the only
host path mounted writable into the microVM; the real workspace is never
mounted here.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import math
import os
import threading
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


class MicrosandboxBackendError(RuntimeError):
    """The physical microsandbox backend could not satisfy its contract."""


def sdk_available() -> bool:
    """Return whether the Python SDK is importable without starting a VM."""
    try:
        return importlib.util.find_spec("microsandbox") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _windows_guest_staging_required() -> bool:
    """Avoid Microsandbox virtio-fs bind mounts on native Windows hosts.

    Microsandbox 0.6.8 has known Windows passthrough-fs errno/flush defects that
    can surface valid reads as EACCES/ELOOP.  The sandbox's private rootfs and
    SandboxFs transport do not depend on that host bind-mount path.
    """
    return os.name == "nt"


def _load_sdk() -> SimpleNamespace:
    try:
        module = importlib.import_module("microsandbox")
        types = importlib.import_module("microsandbox.types")
    except Exception as exc:  # import failure is a physical availability fact
        raise MicrosandboxBackendError(f"Microsandbox SDK indisponivel: {exc}") from exc

    required = ("Sandbox", "Volume", "Network", "install", "is_installed")
    missing = [name for name in required if not hasattr(module, name)]
    if missing or not hasattr(types, "Rlimit"):
        detail = ", ".join(missing + (["Rlimit"] if not hasattr(types, "Rlimit") else []))
        raise MicrosandboxBackendError(f"Microsandbox SDK incompleto: {detail}")

    method_requirements = {
        "Sandbox": (module.Sandbox, ("create", "remove")),
        "Volume": (module.Volume, ("bind",)),
        "Network": (module.Network, ("none", "from_profiles")),
        "Rlimit": (types.Rlimit, ("cpu", "as_", "nproc", "nofile", "fsize")),
    }
    method_missing = [
        f"{owner}.{method}"
        for owner, (target, methods) in method_requirements.items()
        for method in methods
        if not hasattr(target, method)
    ]
    if method_missing:
        raise MicrosandboxBackendError(
            "Microsandbox SDK 0.6.8 incompativel com o contrato fisico esperado: "
            + ", ".join(method_missing)
        )
    return SimpleNamespace(
        Sandbox=module.Sandbox,
        Volume=module.Volume,
        Network=module.Network,
        Rlimit=types.Rlimit,
        install=module.install,
        is_installed=module.is_installed,
    )


def _cpu_count(value: Any) -> int:
    if isinstance(value, bool):
        raise MicrosandboxBackendError("sandbox.cpus deve ser numero positivo")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MicrosandboxBackendError("sandbox.cpus deve ser numero positivo") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise MicrosandboxBackendError("sandbox.cpus deve ser numero positivo")
    # Microsandbox allocates virtual CPUs as an integer.  Round fractional Eyle
    # configuration up so the requested capacity is never silently reduced.
    return max(1, min(64, int(math.ceil(parsed))))


def _tail_append(buffer: bytearray, data: bytes, maximum: int) -> None:
    if not data:
        return
    buffer.extend(data)
    overflow = len(buffer) - maximum
    if overflow > 0:
        del buffer[:overflow]


def _event_kind(event: Any) -> str:
    raw = getattr(event, "event_type", "")
    value = getattr(raw, "value", raw)
    return str(value).lower()


@dataclass(frozen=True)
class MicrosandboxExecResult:
    executed: bool
    code: int | None
    output: str
    timed_out: bool = False
    error: str | None = None


class MicrosandboxSession:
    """One microVM lifecycle bound to one Eyle physical execution context."""

    def __init__(
        self,
        workspace: str,
        cfg: dict[str, Any],
        limits: dict[str, int],
        *,
        block_network: bool,
    ) -> None:
        self.workspace = os.path.realpath(workspace)
        self.cfg = dict(cfg or {})
        self.limits = dict(limits)
        self.block_network = bool(block_network)
        self.workspace_transport = (
            "guest_fs_copy" if _windows_guest_staging_required() else "bind_mount"
        )
        self.name = f"eyle-{uuid.uuid4().hex[:20]}"
        self._sdk = _load_sdk()
        self._sandbox: Any = None
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._loop_main,
            name=f"eyle-microsandbox-{self.name[-8:]}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise MicrosandboxBackendError("Microsandbox event loop nao iniciou")

        # The Python wheel and the local runtime are separate physical layers.
        # The official SDK exposes install()/is_installed(); bootstrap the runtime
        # inside this session's running event loop so first use needs no manual step.
        setup_timeout = max(120, min(900, int(self.limits.get("timeout", 60)) + 300))
        try:
            self._submit(self._ensure_runtime(), timeout=setup_timeout)
            create_timeout = 600
            self._sandbox = self._submit(self._create(), timeout=create_timeout)
        except Exception:
            self._stop_loop()
            raise

    def _loop_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            try:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        self._loop.close()

    def _submit(self, coroutine: Any, *, timeout: float) -> Any:
        if self._closed:
            raise MicrosandboxBackendError("sessao Microsandbox ja encerrada")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise MicrosandboxBackendError(
                f"Microsandbox excedeu timeout fisico de {int(timeout)}s"
            ) from exc
        except MicrosandboxBackendError:
            raise
        except Exception as exc:
            raise MicrosandboxBackendError(f"Microsandbox indisponivel: {exc}") from exc

    async def _ensure_runtime(self) -> None:
        try:
            if not bool(self._sdk.is_installed()):
                await self._sdk.install()
            if not bool(self._sdk.is_installed()):
                raise MicrosandboxBackendError(
                    "runtime Microsandbox permaneceu indisponivel apos install()"
                )
        except MicrosandboxBackendError:
            raise
        except Exception as exc:
            raise MicrosandboxBackendError(
                f"falha ao preparar runtime Microsandbox: {exc}"
            ) from exc

    async def _create(self) -> Any:
        image = self.cfg.get("imagem_oci") or "python:3.12-slim"
        if not isinstance(image, str) or not image.strip():
            raise MicrosandboxBackendError("sandbox.imagem_oci precisa ser string nao vazia")

        guest_staging = _windows_guest_staging_required()
        create_kwargs: dict[str, Any] = {
            "image": image.strip(),
            "cpus": _cpu_count(self.cfg.get("cpus", 1.0)),
            "memory": int(self.limits["memoria_mb"]),
            # When Windows stages through SandboxFs, /workspace does not exist
            # until after the VM is alive.  Every command still receives an
            # explicit cwd, so creation can safely start at /.
            "workdir": "/" if guest_staging else "/workspace",
            "env": {"HOME": "/root", "TMPDIR": "/tmp", "EYLE_SANDBOX": "1"},
            "pull_policy": "if_missing",
            "replace": True,
        }
        if not guest_staging:
            create_kwargs["volumes"] = {
                "/workspace": self._sdk.Volume.bind(self.workspace, readonly=False)
            }

        # Pin the exact v0.6.8 network factories instead of relying on a
        # historical helper name or an implicit default. Normal run_command gets
        # the canonical public profile; supervised execution gets no network.
        create_kwargs["network"] = (
            self._sdk.Network.none()
            if self.block_network
            else self._sdk.Network.from_profiles("public")
        )
        try:
            sandbox = await self._sdk.Sandbox.create(self.name, **create_kwargs)
            if guest_staging:
                await self._stage_workspace_into_guest(sandbox)
            return sandbox
        except MicrosandboxBackendError:
            raise
        except Exception as exc:
            raise MicrosandboxBackendError(f"falha ao criar microVM Microsandbox: {exc}") from exc

    async def _stage_workspace_into_guest(self, sandbox: Any) -> None:
        """Copy the disposable host snapshot into the VM-private rootfs.

        This path is intentionally used on native Windows instead of a bind
        mount.  It avoids virtio-fs entirely while keeping the sandbox writable
        and persistent for the lifetime of the current Eyle job.
        """
        fs = getattr(sandbox, "fs", None)
        if fs is None:
            raise MicrosandboxBackendError(
                "Microsandbox SDK sem Sandbox.fs para staging seguro no Windows"
            )

        try:
            if not await fs.exists("/workspace"):
                await fs.mkdir("/workspace")

            directories: list[tuple[str, str]] = []
            files: list[tuple[str, str]] = []
            root = self.workspace
            for current, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = [
                    name for name in dirnames
                    if not os.path.islink(os.path.join(current, name))
                ]
                rel_current = os.path.relpath(current, root)
                guest_current = "/workspace" if rel_current == "." else (
                    "/workspace/" + rel_current.replace(os.sep, "/")
                )
                if rel_current != ".":
                    directories.append((current, guest_current))
                for name in filenames:
                    host_path = os.path.join(current, name)
                    if os.path.islink(host_path):
                        continue
                    rel_path = os.path.relpath(host_path, root).replace(os.sep, "/")
                    files.append((host_path, "/workspace/" + rel_path))

            for _host_dir, guest_dir in directories:
                if not await fs.exists(guest_dir):
                    await fs.mkdir(guest_dir)

            # SandboxFs.copy_from_host uses the agent channel rather than a
            # passthrough filesystem.  A small bounded fan-out keeps large
            # projects practical without depending on guest tooling such as tar.
            semaphore = asyncio.Semaphore(16)

            async def copy_one(host_path: str, guest_path: str) -> None:
                async with semaphore:
                    await fs.copy_from_host(host_path, guest_path)

            if files:
                await asyncio.gather(*(copy_one(h, g) for h, g in files))
        except Exception as exc:
            raise MicrosandboxBackendError(
                f"falha ao copiar snapshot para filesystem privado da microVM: {exc}"
            ) from exc

    def _rlimits(self) -> list[Any]:
        memory_bytes = int(self.limits["memoria_mb"]) * 1024 * 1024
        file_bytes = int(self.limits["arquivo_mb"]) * 1024 * 1024
        Rlimit = self._sdk.Rlimit
        return [
            Rlimit.cpu(int(self.limits["cpu"])),
            Rlimit.as_(soft=memory_bytes, hard=memory_bytes),
            Rlimit.nproc(int(self.limits["processos"])),
            Rlimit.nofile(int(self.limits["arquivos"])),
            Rlimit.fsize(file_bytes),
        ]

    async def _execute_stream(
        self,
        script: str,
        *,
        guest_cwd: str,
        timeout: int,
        max_output_bytes: int,
    ) -> MicrosandboxExecResult:
        try:
            handle = await self._sandbox.shell_stream(
                script,
                cwd=guest_cwd,
                timeout=float(timeout),
                rlimits=self._rlimits(),
            )
        except Exception as exc:
            return MicrosandboxExecResult(
                executed=False,
                code=None,
                output="",
                error=f"nao foi possivel iniciar comando na microVM: {exc}",
            )

        tail = bytearray()
        code: int | None = None
        failed: str | None = None

        async def drain() -> None:
            nonlocal code, failed
            async for event in handle:
                kind = _event_kind(event)
                if kind in {"stdout", "stderr", "stdin_error", "failed"}:
                    data = getattr(event, "data", None)
                    if data:
                        _tail_append(tail, bytes(data), max_output_bytes)
                    if kind == "failed":
                        failed = bytes(data or b"").decode("utf-8", errors="replace") or "spawn failure"
                if kind == "exited":
                    event_code = getattr(event, "code", None)
                    code = int(event_code) if event_code is not None else None

        # Keep an independent host-side watchdog around the SDK's guest timeout.
        # If either layer fires, kill only this guest process, not the whole VM,
        # so the per-job laboratory remains usable for a corrective command.
        try:
            await asyncio.wait_for(drain(), timeout=float(timeout) + 5.0)
        except asyncio.TimeoutError:
            try:
                await handle.kill()
            except Exception:
                pass
            return MicrosandboxExecResult(
                executed=True,
                code=code,
                output=tail.decode("utf-8", errors="replace"),
                timed_out=True,
                error=f"timeout de {timeout}s excedido",
            )
        except Exception as exc:
            return MicrosandboxExecResult(
                executed=True,
                code=code,
                output=tail.decode("utf-8", errors="replace"),
                error=f"falha durante execucao na microVM: {exc}",
            )

        if code is None:
            try:
                waited_code, _success = await handle.wait()
                code = int(waited_code)
            except Exception as exc:
                return MicrosandboxExecResult(
                    executed=True,
                    code=None,
                    output=tail.decode("utf-8", errors="replace"),
                    error=f"nao foi possivel obter status do comando: {exc}",
                )

        return MicrosandboxExecResult(
            executed=True,
            code=code,
            output=tail.decode("utf-8", errors="replace"),
            error=failed,
        )

    def execute(
        self,
        script: str,
        *,
        rel_cwd: str,
        timeout: int,
        max_output_bytes: int,
    ) -> MicrosandboxExecResult:
        guest_cwd = "/workspace" if rel_cwd in {"", "."} else f"/workspace/{rel_cwd}"
        # The outer wait is longer than the guest watchdog to leave cleanup room.
        outer = float(timeout) + 15.0
        return self._submit(
            self._execute_stream(
                script,
                guest_cwd=guest_cwd,
                timeout=int(timeout),
                max_output_bytes=max(1, int(max_output_bytes)),
            ),
            timeout=outer,
        )

    async def _shutdown(self) -> None:
        sandbox = self._sandbox
        self._sandbox = None
        if sandbox is None:
            return
        try:
            await sandbox.stop(timeout=5.0)
        except Exception:
            try:
                await sandbox.kill(timeout=5.0)
            except Exception:
                pass
        try:
            await self._sdk.Sandbox.remove(self.name)
        except Exception:
            pass

    def _stop_loop(self) -> None:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._sandbox is not None and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
                try:
                    future.result(timeout=15)
                except Exception:
                    future.cancel()
        finally:
            self._closed = True
            self._stop_loop()
