from __future__ import annotations

import json
from pathlib import Path

from eyle.devtools import release_identity


def test_release_verifier_rejects_runtime_state(tmp_path):
    (tmp_path / "context").mkdir()
    (tmp_path / "context" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "context" / "telemetry.sqlite3").write_bytes(b"dirty")
    assert release_identity._runtime_state_violations(tmp_path) == ["context/telemetry.sqlite3"]


def test_release_verifier_rejects_generated_cache(tmp_path):
    cache = tmp_path / "eyle" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"cache")
    violations = release_identity._generated_artifact_violations(tmp_path)
    assert "eyle/__pycache__/" in violations
    assert "eyle/__pycache__/x.pyc" in violations


def test_release_manifest_public_tools_match_registry():
    base = Path(__file__).resolve().parent.parent
    manifest = json.loads((base / "release_manifest.json").read_text(encoding="utf-8"))
    assert release_identity._public_tool_violations(base, manifest) == []
    assert manifest["publication"]["requires_extracted_artifact_verification"] is True


def test_release_verifier_rejects_removed_core_modules(tmp_path):
    target = tmp_path / "eyle" / "core"
    target.mkdir(parents=True)
    (target / "execution_trace.py").write_text("# resurrected", encoding="utf-8")
    (target / "operational_feedback.py").write_text("# resurrected", encoding="utf-8")
    assert release_identity._removed_contract_violations(tmp_path) == [
        "eyle/core/execution_trace.py",
        "eyle/core/operational_feedback.py",
    ]


