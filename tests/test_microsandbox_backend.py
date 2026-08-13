#!/usr/bin/env python3
"""Physical Microsandbox backend contract tests without requiring virtualization."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import eyle.core.microsandbox_backend as msb_mod
import eyle.core.sandbox as sandbox_mod
from eyle.core.execution_context import ExecutionContext, bind_execution, reset_execution


class FakeRlimit:
    @staticmethod
    def cpu(value): return ("cpu", value)
    @staticmethod
    def as_(soft, hard): return ("as", soft, hard)
    @staticmethod
    def nproc(value): return ("nproc", value)
    @staticmethod
    def nofile(value): return ("nofile", value)
    @staticmethod
    def fsize(value): return ("fsize", value)


class FakeNetwork:
    @staticmethod
    def none(): return "network:none"
    @staticmethod
    def from_profiles(*profiles): return ("network:profiles", profiles)


class FakeVolume:
    calls = []

    @staticmethod
    def bind(path, **kwargs):
        value = (os.path.realpath(path), dict(kwargs))
        FakeVolume.calls.append(value)
        return value


class FakeEvent:
    def __init__(self, event_type, data=b"", code=None):
        self.event_type = event_type
        self.data = data
        self.code = code


class FakeHandle:
    def __init__(self, events):
        self._events = list(events)
        self._index = 0
        self.killed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event

    async def wait(self):
        return 0, True

    async def kill(self):
        self.killed = True


class FakeFs:
    def __init__(self):
        self.paths = {"/"}
        self.mkdir_calls = []
        self.copy_calls = []
        self.copy_to_host_calls = []

    async def exists(self, path):
        return path in self.paths

    async def mkdir(self, path):
        self.mkdir_calls.append(path)
        self.paths.add(path)

    async def copy_from_host(self, host_path, guest_path):
        self.copy_calls.append((os.path.realpath(host_path), guest_path))
        self.paths.add(guest_path)

    async def copy_to_host(self, guest_path, host_path):
        self.copy_to_host_calls.append((guest_path, os.path.realpath(host_path)))
        with open(host_path, "wb") as handle:
            handle.write(b"fake-export")


class FakeSandboxInstance:
    def __init__(self):
        self.shell_calls = []
        self.stopped = False
        self.killed = False
        self.fs = FakeFs()

    async def shell_stream(self, script, **kwargs):
        self.shell_calls.append((script, dict(kwargs)))
        return FakeHandle([
            FakeEvent("stdout", b"HEAD-" + b"A" * 100),
            FakeEvent("stderr", b"-TAIL"),
            FakeEvent("exited", code=0),
        ])

    async def stop(self, timeout=None):
        self.stopped = True

    async def kill(self, timeout=None):
        self.killed = True


class FakeSandbox:
    create_calls = []
    remove_calls = []
    instance = None

    @staticmethod
    async def create(name, **kwargs):
        FakeSandbox.create_calls.append((name, dict(kwargs)))
        FakeSandbox.instance = FakeSandboxInstance()
        return FakeSandbox.instance

    @staticmethod
    async def remove(name):
        FakeSandbox.remove_calls.append(name)


def _fake_sdk(installed=True, *, install_error=None):
    state = {"installed": bool(installed), "install_calls": 0}

    async def install():
        state["install_calls"] += 1
        if install_error is not None:
            raise install_error
        state["installed"] = True

    sdk = SimpleNamespace(
        Sandbox=FakeSandbox,
        Volume=FakeVolume,
        Network=FakeNetwork,
        Rlimit=FakeRlimit,
        install=install,
        is_installed=lambda: state["installed"],
        _state=state,
    )
    return sdk


def _limits():
    return {
        "timeout": 5, "cpu": 4, "memoria_mb": 256,
        "processos": 32, "arquivos": 64, "saida_kb": 16,
        "arquivo_mb": 32, "arquivos_projeto": 1000,
        "tamanho_projeto_mb": 64,
    }


def _reset_fakes():
    FakeVolume.calls.clear()
    FakeSandbox.create_calls.clear()
    FakeSandbox.remove_calls.clear()
    FakeSandbox.instance = None


def test_unrestricted_auto_prefers_microsandbox_before_other_strong_backends(monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: True)
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda _name: "/fake/docker")
    assert sandbox_mod._strong_backend({"backend": "auto"}) == "microsandbox"


def test_supervised_auto_does_not_assume_generic_oci_has_test_tooling(monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: True)
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: "/fake/docker" if name == "docker" else None)
    assert sandbox_mod._supervised_backend({"backend": "auto"}) == "docker"


def test_explicit_microsandbox_fails_closed_when_sdk_missing(monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: False)
    with pytest.raises(sandbox_mod.ErroSandbox, match="SDK was not found"):
        sandbox_mod._strong_backend({"backend": "microsandbox"})


def test_session_mounts_only_supplied_snapshot_and_uses_v068_public_profile(monkeypatch, tmp_path):
    _reset_fakes()
    monkeypatch.setattr(msb_mod, "_windows_guest_staging_required", lambda: False)
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: _fake_sdk())
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    session = msb_mod.MicrosandboxSession(
        str(snapshot), {"imagem_oci": "python:3.12-slim", "cpus": 1.25},
        _limits(), block_network=False,
    )
    try:
        result = session.execute("echo ok", rel_cwd=".", timeout=3, max_output_bytes=32)
        assert result.executed is True and result.code == 0 and result.error is None
        assert result.output.endswith("-TAIL")
        assert len(result.output.encode("utf-8")) <= 32

        _name, create = FakeSandbox.create_calls[-1]
        assert create["image"] == "python:3.12-slim"
        assert create["cpus"] == 2
        assert create["memory"] == 256
        assert session.workspace_transport == "bind_mount"
        assert create["workdir"] == "/workspace"
        assert create["network"] == ("network:profiles", ("public",))
        assert set(create["volumes"]) == {"/workspace"}
        mounted_path, mount_flags = create["volumes"]["/workspace"]
        assert mounted_path == os.path.realpath(snapshot)
        assert mount_flags == {"readonly": False}

        _script, shell = FakeSandbox.instance.shell_calls[-1]
        assert shell["cwd"] == "/workspace"
        assert ("cpu", 4) in shell["rlimits"]
        assert ("nproc", 32) in shell["rlimits"]
        assert ("nofile", 64) in shell["rlimits"]
        assert any(item[0] == "as" for item in shell["rlimits"])
        assert any(item[0] == "fsize" for item in shell["rlimits"])
    finally:
        name = session.name
        session.close()
    assert FakeSandbox.instance.stopped is True
    assert FakeSandbox.remove_calls == [name]


def test_windows_session_avoids_bind_mount_and_stages_snapshot_via_sandbox_fs(monkeypatch, tmp_path):
    _reset_fakes()
    monkeypatch.setattr(msb_mod, "_windows_guest_staging_required", lambda: True)
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: _fake_sdk())
    snapshot = tmp_path / "snapshot"
    nested = snapshot / "pkg"
    nested.mkdir(parents=True)
    (snapshot / "README.md").write_text("hello", encoding="utf-8")
    (nested / "main.py").write_text("print('ok')", encoding="utf-8")

    session = msb_mod.MicrosandboxSession(
        str(snapshot), {"imagem_oci": "python:3.12-slim", "cpus": 1},
        _limits(), block_network=False,
    )
    try:
        _name, create = FakeSandbox.create_calls[-1]
        assert session.workspace_transport == "guest_fs_copy"
        assert create["workdir"] == "/"
        assert "volumes" not in create
        assert FakeVolume.calls == []
        assert create["network"] == ("network:profiles", ("public",))
        fs = FakeSandbox.instance.fs
        assert "/workspace" in fs.mkdir_calls
        assert "/workspace/pkg" in fs.mkdir_calls
        assert (os.path.realpath(snapshot / "README.md"), "/workspace/README.md") in fs.copy_calls
        assert (os.path.realpath(nested / "main.py"), "/workspace/pkg/main.py") in fs.copy_calls
        result = session.execute("python pkg/main.py", rel_cwd=".", timeout=3, max_output_bytes=32)
        assert result.executed is True and result.code == 0
        _script, shell = FakeSandbox.instance.shell_calls[-1]
        assert shell["cwd"] == "/workspace"
    finally:
        session.close()


def test_session_bootstraps_missing_runtime_once(monkeypatch, tmp_path):
    _reset_fakes()
    sdk = _fake_sdk(installed=False)
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: sdk)
    session = msb_mod.MicrosandboxSession(str(tmp_path), {}, _limits(), block_network=False)
    try:
        assert sdk._state["install_calls"] == 1
        assert sdk.is_installed() is True
        assert len(FakeSandbox.create_calls) == 1
    finally:
        session.close()


def test_session_does_not_reinstall_existing_runtime(monkeypatch, tmp_path):
    _reset_fakes()
    sdk = _fake_sdk(installed=True)
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: sdk)
    session = msb_mod.MicrosandboxSession(str(tmp_path), {}, _limits(), block_network=False)
    try:
        assert sdk._state["install_calls"] == 0
    finally:
        session.close()


def test_session_runtime_bootstrap_failure_is_explicit(monkeypatch, tmp_path):
    _reset_fakes()
    sdk = _fake_sdk(installed=False, install_error=RuntimeError("download failed"))
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: sdk)
    with pytest.raises(msb_mod.MicrosandboxBackendError, match="falha ao preparar runtime Microsandbox: download failed"):
        msb_mod.MicrosandboxSession(str(tmp_path), {}, _limits(), block_network=False)
    assert sdk._state["install_calls"] == 1
    assert FakeSandbox.create_calls == []


def test_session_uses_network_none_when_blocked(monkeypatch, tmp_path):
    _reset_fakes()
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: _fake_sdk())
    session = msb_mod.MicrosandboxSession(str(tmp_path), {}, _limits(), block_network=True)
    try:
        assert FakeSandbox.create_calls[-1][1]["network"] == "network:none"
    finally:
        session.close()


def test_run_command_reuses_one_microsandbox_session_per_execution(monkeypatch, tmp_path):
    class Session:
        created = 0
        closed = 0
        commands = []
        def __init__(self, workspace, cfg, limits, *, block_network):
            type(self).created += 1
            self.workspace = workspace
            self.workspace_transport = "guest_fs_copy"
        def execute(self, script, *, rel_cwd, timeout, max_output_bytes):
            type(self).commands.append((script, rel_cwd))
            return msb_mod.MicrosandboxExecResult(True, 0, "ok\n")
        def close(self): type(self).closed += 1

    monkeypatch.setattr(msb_mod, "MicrosandboxSession", Session)
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: True)
    ctx = ExecutionContext(0.0, 100.0, "task", 1, 1000)
    token = bind_execution(ctx)
    try:
        cfg = {"backend": "auto", "timeout_segundos": 5, "memoria_mb": 128,
               "cpu_segundos": 5, "max_processos": 16, "max_arquivos_abertos": 32,
               "max_saida_kb": 16, "max_arquivo_mb": 16, "cpus": 1}
        first = sandbox_mod.executar_comando_livre_no_sandbox(str(tmp_path), "echo one", cfg)
        second = sandbox_mod.executar_comando_livre_no_sandbox(str(tmp_path), "echo two", cfg)
        assert first["ok"] is True and second["ok"] is True
        assert first["workspace_transport"] == "guest_fs_copy"
        assert second["workspace_transport"] == "guest_fs_copy"
        assert Session.created == 1
        assert Session.commands == [("echo one", "."), ("echo two", ".")]
        assert ctx.sandbox_microsandbox_session is not None
        ctx.cleanup_sandbox()
        assert Session.closed == 1
        assert ctx.sandbox_microsandbox_session is None
    finally:
        reset_execution(token)


def test_execution_context_closes_microvm_before_snapshot_cleanup():
    order = []
    class Session:
        def close(self): order.append("microvm")
    class Temp:
        def cleanup(self): order.append("snapshot")
    ctx = ExecutionContext(0.0, 100.0, "task", 1, 1000)
    ctx.sandbox_microsandbox_session = Session()
    ctx.sandbox_tempdir = Temp()
    ctx.cleanup_sandbox()
    assert order == ["microvm", "snapshot"]


def test_supervised_microsandbox_is_one_off_and_network_isolated(monkeypatch, tmp_path):
    class Session:
        created = []
        closed = 0
        def __init__(self, workspace, cfg, limits, *, block_network):
            type(self).created.append((os.path.realpath(workspace), block_network))
        def execute(self, script, *, rel_cwd, timeout, max_output_bytes):
            return msb_mod.MicrosandboxExecResult(True, 0, "2 passed\n")
        def close(self): type(self).closed += 1

    monkeypatch.setattr(msb_mod, "MicrosandboxSession", Session)
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: True)
    cfg = {
        "backend": "microsandbox", "bloquear_rede": True,
        "comandos_permitidos": [["python", "-m", "pytest"]],
        "timeout_segundos": 5, "cpu_segundos": 5, "memoria_mb": 128,
        "max_processos": 16, "max_arquivos_abertos": 32,
        "max_saida_kb": 16, "max_arquivo_mb": 16,
    }
    result = sandbox_mod.executar_no_sandbox(str(tmp_path), ["python", "-m", "pytest"], cfg)
    assert result["executado"] is True and result["ok"] is True
    assert result["backend"] == "microsandbox"
    assert result["network_isolated"] is True
    assert Session.created and Session.created[-1][1] is True
    assert Session.closed == 1


def test_microsandbox_runtime_failure_does_not_hide_behind_docker_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_mod, "_microsandbox_available", lambda: True)
    def fail(*args, **kwargs):
        raise sandbox_mod.ErroSandbox("microVM unavailable")
    monkeypatch.setattr(sandbox_mod, "_ensure_microsandbox_session", fail)
    monkeypatch.setattr(sandbox_mod, "_comando_docker", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fallback should not run")))
    result = sandbox_mod.executar_comando_livre_no_sandbox(str(tmp_path), "echo ok", {"backend": "auto"})
    assert result["executado"] is False
    assert "microVM unavailable" in result["erro"]


def test_load_sdk_rejects_wrong_network_api_shape(monkeypatch):
    class WrongNetwork:
        @staticmethod
        def none(): return "network:none"
        # Intentionally no from_profiles(): this mirrors the live class of
        # integration error that Rev1.2.3.2 exposed on Windows.

    fake_module = SimpleNamespace(
        Sandbox=FakeSandbox,
        Volume=FakeVolume,
        Network=WrongNetwork,
        install=lambda: None,
        is_installed=lambda: True,
    )
    fake_types = SimpleNamespace(Rlimit=FakeRlimit)

    def fake_import(name):
        if name == "microsandbox":
            return fake_module
        if name == "microsandbox.types":
            return fake_types
        raise ImportError(name)

    monkeypatch.setattr(msb_mod.importlib, "import_module", fake_import)
    with pytest.raises(msb_mod.MicrosandboxBackendError, match=r"Network\.from_profiles"):
        msb_mod._load_sdk()


def test_load_sdk_accepts_exact_v068_surface(monkeypatch):
    async def install():
        return None

    fake_module = SimpleNamespace(
        Sandbox=FakeSandbox,
        Volume=FakeVolume,
        Network=FakeNetwork,
        install=install,
        is_installed=lambda: True,
    )
    fake_types = SimpleNamespace(Rlimit=FakeRlimit)

    def fake_import(name):
        if name == "microsandbox":
            return fake_module
        if name == "microsandbox.types":
            return fake_types
        raise ImportError(name)

    monkeypatch.setattr(msb_mod.importlib, "import_module", fake_import)
    sdk = msb_mod._load_sdk()
    assert sdk.Network is FakeNetwork
    assert sdk.Rlimit is FakeRlimit
    assert sdk.Sandbox is FakeSandbox


def test_windows_session_can_export_one_guest_file_via_sdk(monkeypatch, tmp_path):
    _reset_fakes()
    monkeypatch.setattr(msb_mod, "_windows_guest_staging_required", lambda: True)
    monkeypatch.setattr(msb_mod, "_load_sdk", lambda: _fake_sdk())
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    session = msb_mod.MicrosandboxSession(str(snapshot), {}, _limits(), block_network=False)
    try:
        target = tmp_path / "candidate.zip"
        session.copy_to_host("/tmp/candidate.zip", str(target), timeout=5)
        assert target.read_bytes() == b"fake-export"
        assert FakeSandbox.instance.fs.copy_to_host_calls == [
            ("/tmp/candidate.zip", os.path.realpath(target))
        ]
    finally:
        session.close()
