from pathlib import Path

import pytest

from eyle.core import sandbox as sandbox_mod
from eyle.core import tools
from eyle.core.workspace_io import ErroLeituraProjeto, ler_faixa_projeto, listar_arvore_projeto
from eyle.core.workspace_policy import _is_protected_resource_path


def _ctx(root):
    return {
        "projeto": {"caminho_origem": str(root)},
        "config": {"agent": {"max_file_read_lines": 400, "max_search_matches": 40, "max_search_ranges": 12, "max_search_range_lines": 16}},
    }


def test_normal_source_is_never_blocked_by_secret_like_content(tmp_path):
    content = """token = bind_execution(execution)\napi_key = 'literal-that-used-to-trigger-the-scanner'\npassword = config.password\nPRIVATE = '-----BEGIN PRIVATE KEY-----'\n"""
    (tmp_path / "agent.py").write_text(content, encoding="utf-8")

    result = ler_faixa_projeto(tmp_path, "agent.py", 1, 4, max_linhas=20)
    assert result["content"] == content

    search = tools.executar_tool("search_code", {"query": "api_key"}, _ctx(tmp_path))
    assert search["ok"] is True
    assert search["detail"]["materialized_files"] == ["agent.py"]
    assert search["detail"]["protected_resources_excluded"] == 0


def test_only_path_identified_secret_resources_are_read_protected(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=real-secret\n", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"token":"real-secret"}\n', encoding="utf-8")
    (tmp_path / "private.pem").write_text("private material\n", encoding="utf-8")
    (tmp_path / "server.key").write_text("private material\n", encoding="utf-8")

    for name in (".env", "credentials.json", "private.pem", "server.key"):
        assert _is_protected_resource_path(name) is True
        with pytest.raises(ErroLeituraProjeto, match="protected resource") as exc:
            ler_faixa_projeto(tmp_path, name, 1, 1)
        assert exc.value.error_code == "PROTECTED_RESOURCE_READ_BLOCKED"


def test_public_and_ambiguous_pem_material_remains_readable(tmp_path):
    (tmp_path / "public.pem").write_text("-----BEGIN PUBLIC KEY-----\nPUBLIC\n", encoding="utf-8")
    (tmp_path / "certificate.pem").write_text("-----BEGIN CERTIFICATE-----\nCERT\n", encoding="utf-8")
    (tmp_path / "generic.pem").write_text("-----BEGIN PRIVATE KEY-----\nMISPLACED\n", encoding="utf-8")
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAA public\n", encoding="utf-8")

    for name in ("public.pem", "certificate.pem", "generic.pem", "id_ed25519.pub"):
        assert _is_protected_resource_path(name) is False
        assert ler_faixa_projeto(tmp_path, name, 1, 2)["file"] == name


def test_tree_keeps_protected_resource_structural_existence_visible(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("token = bind_execution(execution)\n", encoding="utf-8")

    tree = listar_arvore_projeto(tmp_path, limite=20, profundidade=3)
    entries = {item["path"]: item for item in tree["entries"]}
    assert entries[".env"]["content_access"] == "protected"
    assert "content_access" not in entries["agent.py"]
    assert tree["protected_resources"] == 1


def test_search_excludes_protected_content_without_hiding_normal_matches(tmp_path):
    (tmp_path / ".env").write_text("needle=secret\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("needle = 'visible'\n", encoding="utf-8")

    result = tools.executar_tool("search_code", {"query": "needle"}, _ctx(tmp_path))
    detail = result["detail"]
    assert result["ok"] is True
    assert detail["materialized_files"] == ["app.py"]
    assert detail["protected_resources_excluded"] == 1
    assert detail["coverage_scope"] == "readable_workspace_files"


def test_sandbox_preserves_normal_source_even_when_content_looks_sensitive(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("api_key='literal-in-code'\n", encoding="utf-8")
    (tmp_path / "public.pem").write_text("-----BEGIN PUBLIC KEY-----\nPUBLIC\n", encoding="utf-8")
    limits = sandbox_mod._limites({
        "timeout_segundos": 5, "cpu_segundos": 5, "memoria_mb": 256,
        "max_processos": 16, "max_arquivos_abertos": 32, "max_saida_kb": 32,
        "max_arquivo_mb": 64, "max_arquivos_projeto": 1000, "max_tamanho_projeto_mb": 64,
    })
    snapshot, tempdir = sandbox_mod._copiar_projeto(str(tmp_path), limits)
    try:
        assert not Path(snapshot, ".env").exists()
        assert Path(snapshot, "agent.py").exists()
        assert Path(snapshot, "public.pem").exists()
        assert getattr(tempdir, "protected_resources_omitted", []) == [".env"]
    finally:
        tempdir.cleanup()


def test_symbol_relations_does_not_drop_normal_source_for_sensitive_words(tmp_path):
    (tmp_path / "main.py").write_text(
        "def target():\n    token = bind_execution(execution)\n\nif __name__ == '__main__':\n    target()\n",
        encoding="utf-8",
    )
    result = tools.executar_tool(
        "symbol_relations",
        {"symbol": "target", "query": "reachability", "direction": "incoming", "include_text_references": False},
        _ctx(tmp_path),
    )
    coverage = result["coverage"]
    assert result["ok"] is True
    assert coverage["complete"] is True
    assert coverage["facts"]["objective_complete"] is True
    assert coverage["facts"]["objective_result"] == "reachable"
    assert coverage["facts"]["protected_resources_skipped"] == 0


def test_git_status_shows_protected_path_but_git_diff_omits_its_content(tmp_path):
    import subprocess
    from eyle.core.git_tools import git_diff, git_status

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text("TOKEN=old\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    (tmp_path / ".env").write_text("TOKEN=TOP_SECRET_VALUE\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 2\n", encoding="utf-8")

    status = git_status(str(tmp_path))
    env = next(item for item in status["entries"] if item["path"] == ".env")
    assert env["content_access"] == "protected"

    diff = git_diff(str(tmp_path))
    assert diff["ok"] is True
    assert diff["protected_resources_omitted"] == 1
    assert "TOP_SECRET_VALUE" not in diff["diff"]
    assert "value = 2" in diff["diff"]

    blocked = git_diff(str(tmp_path), path=".env")
    assert blocked["error_code"] == "PROTECTED_RESOURCE_READ_BLOCKED"


def test_env_templates_are_readable_contract_examples(tmp_path):
    for name in (".env.example", ".env.sample", ".env.template", ".env.dist", ".env.production.example"):
        (tmp_path / name).write_text("TOKEN=placeholder\n", encoding="utf-8")
        assert _is_protected_resource_path(name) is False
        assert ler_faixa_projeto(tmp_path, name, 1, 1)["file"] == name
    tree = listar_arvore_projeto(tmp_path, limite=50, profundidade=3)
    visible = {item["path"] for item in tree["entries"]}
    assert {".env.example", ".env.sample", ".env.template", ".env.dist", ".env.production.example"}.issubset(visible)


def test_symlink_and_hardlink_aliases_share_protected_physical_identity(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=TOP_SECRET\n", encoding="utf-8")
    symlink = tmp_path / "alias.txt"
    symlink.symlink_to(env.name)
    hardlink = tmp_path / "copy.txt"
    hardlink.hardlink_to(env)
    for name in ("alias.txt", "copy.txt"):
        with pytest.raises(ErroLeituraProjeto) as exc:
            ler_faixa_projeto(tmp_path, name, 1, 1)
        assert exc.value.error_code == "PROTECTED_RESOURCE_READ_BLOCKED"
        result = tools.executar_tool("search_code", {"query": "TOP_SECRET"}, _ctx(tmp_path))
        assert result["ok"] is True
        assert name not in result["detail"]["materialized_files"]


def test_search_negative_observation_preserves_protected_coverage_boundary(tmp_path):
    (tmp_path / ".env").write_text("needle=secret\n", encoding="utf-8")
    result = tools.executar_tool("search_code", {"query": "needle"}, _ctx(tmp_path))
    detail = result["detail"]
    assert detail["scope_complete"] is True
    assert detail["coverage_complete"] is False
    assert result["coverage"]["complete"] is False
    assert any(item.get("kind") == "protected_resource" for item in result["coverage"]["boundaries"])
    assert result["coverage"]["facts"]["coverage_scope"] == "readable_workspace_files"
    assert any(item.get("kind") == "protected_resource" and item.get("count") == 1 for item in result["coverage"]["boundaries"])
    assert len(result["observations"]) == 1
    assert result["observations"][0]["locator"] == {"kind": "capability", "name": "search_code", "source": "workspace"}


def test_sandbox_omits_symlink_and_hardlink_aliases_of_protected_resource(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=TOP_SECRET\n", encoding="utf-8")
    (tmp_path / "alias.txt").symlink_to(".env")
    (tmp_path / "copy.txt").hardlink_to(tmp_path / ".env")
    limits = sandbox_mod._limites({
        "timeout_segundos": 5, "cpu_segundos": 5, "memoria_mb": 256,
        "max_processos": 16, "max_arquivos_abertos": 32, "max_saida_kb": 32,
        "max_arquivo_mb": 64, "max_arquivos_projeto": 1000, "max_tamanho_projeto_mb": 64,
    })
    snapshot, tempdir = sandbox_mod._copiar_projeto(str(tmp_path), limits)
    try:
        assert not Path(snapshot, ".env").exists()
        assert not Path(snapshot, "alias.txt").exists()
        assert not Path(snapshot, "copy.txt").exists()
        omitted = set(getattr(tempdir, "protected_resources_omitted", []))
        assert {".env", "alias.txt", "copy.txt"}.issubset(omitted)
    finally:
        tempdir.cleanup()


def test_git_diff_omits_hardlink_alias_of_protected_resource(tmp_path):
    import subprocess
    from eyle.core.git_tools import git_diff

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    env = tmp_path / ".env"
    env.write_text("TOKEN=old\n", encoding="utf-8")
    (tmp_path / "alias.txt").hardlink_to(env)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    env.write_text("TOKEN=NEW_TOP_SECRET\n", encoding="utf-8")
    result = git_diff(str(tmp_path))
    assert result["ok"] is True
    assert "NEW_TOP_SECRET" not in result["diff"]
    protected = {item["path"] for item in result["files"] if item.get("content_access") == "protected"}
    assert {".env", "alias.txt"}.issubset(protected)


def test_patch_dry_run_does_not_read_protected_resource_or_alias(tmp_path):
    from eyle.core.transactions import dry_run_patch_set
    from eyle.core.text_hash import hash_texto

    env = tmp_path / ".env"
    env.write_text("TOKEN=TOP_SECRET\n", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    alias.hardlink_to(env)
    for path in (".env", "alias.txt"):
        result = dry_run_patch_set(str(tmp_path), [{
            "operation": "replace", "path": path, "content": "replacement\n",
            "file_hash_expected": hash_texto("TOKEN=TOP_SECRET\n"),
        }])
        assert result["ok"] is False
        assert result["error_code"] == "PROTECTED_RESOURCE_READ_BLOCKED"
        assert "TOP_SECRET" not in str(result)


def test_resource_scoped_block_is_reusable_across_read_ranges(tmp_path):
    from eyle.core.session import AgentSession
    from eyle.core.observation import record

    (tmp_path / ".env").write_text("TOKEN=TOP_SECRET\n", encoding="utf-8")
    session = AgentSession(request="test")
    arguments = {"path": ".env", "line_start": 1, "line_end": 1}
    result = tools.executar_tool("read_file", arguments, _ctx(tmp_path))
    assert result["retryable"] is False
    assert result["failure_scope"] == "resource"
    model_result = {"tool": "read_file", **result, "grounding_ids": []}
    signature = tools.capability_observation_signature("read_file", arguments)
    record(session, signature, "read_file", arguments, result, model_result)

    replayable = tools.capability_find_resource_failure(
        "read_file", {"path": ".env", "line_start": 100, "line_end": 120},
        session.observation_ledger["entries"], session.workspace_epoch,
    )
    assert replayable is not None
    assert replayable["failure_scope"] == "resource"
    assert replayable["failure_resource"] == ".env"


def test_resource_scoped_block_survives_session_serialization(tmp_path):
    from eyle.core.session import AgentSession
    from eyle.core.observation import record
    from eyle.core.agent import _rehydrate_observation

    (tmp_path / ".env").write_text("TOKEN=TOP_SECRET\n", encoding="utf-8")
    session = AgentSession(request="test")
    arguments = {"path": ".env", "line_start": 1, "line_end": 1}
    result = tools.executar_tool("read_file", arguments, _ctx(tmp_path))
    model_result = {"tool": "read_file", **result, "grounding_ids": []}
    signature = tools.capability_observation_signature("read_file", arguments)
    record(session, signature, "read_file", arguments, result, model_result)

    restored = AgentSession.from_dict(session.to_dict())
    entry = tools.capability_find_resource_failure(
        "read_file", {"path": ".env", "line_start": 20, "line_end": 30},
        restored.observation_ledger["entries"], restored.workspace_epoch,
    )
    assert entry is not None
    replay = _rehydrate_observation(restored, entry, _ctx(tmp_path)["config"])
    assert replay["ok"] is False
    assert replay["error_code"] == "PROTECTED_RESOURCE_READ_BLOCKED"
    assert replay["failure_scope"] == "resource"
    assert replay["executed"] is False


def test_protected_symlink_does_not_hide_normal_target_source(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".env").symlink_to("app.py")
    with pytest.raises(ErroLeituraProjeto) as exc:
        ler_faixa_projeto(tmp_path, ".env", 1, 1)
    assert exc.value.error_code == "PROTECTED_RESOURCE_READ_BLOCKED"
    assert ler_faixa_projeto(tmp_path, "app.py", 1, 1)["content"] == "value = 1\n"
