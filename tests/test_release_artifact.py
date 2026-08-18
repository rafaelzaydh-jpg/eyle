from __future__ import annotations

import json
from pathlib import Path

from eyle.devtools import release_identity


def test_release_verifier_rejects_runtime_state(tmp_path):
    (tmp_path/"context").mkdir(); (tmp_path/"context"/".gitkeep").write_text("",encoding="utf-8")
    (tmp_path/"context"/"telemetry.sqlite3").write_bytes(b"dirty")
    assert any("context/telemetry.sqlite3" in item for item in release_identity._artifact_violations(tmp_path))


def test_release_verifier_rejects_generated_cache(tmp_path):
    cache=tmp_path/"eyle"/"__pycache__"; cache.mkdir(parents=True); (cache/"x.pyc").write_bytes(b"cache")
    violations=release_identity._artifact_violations(tmp_path)
    assert any("eyle/__pycache__/" in item for item in violations)
    assert any("eyle/__pycache__/x.pyc" in item for item in violations)


def test_release_manifest_capabilities_match_registry():
    from tests.canonical import standard_registry
    base=Path(__file__).resolve().parent.parent
    manifest=json.loads((base/"release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["public_capabilities"] == standard_registry().names()
    assert manifest["bundled_providers"] == ["standard"]
    assert manifest["publication"]["requires_extracted_artifact_verification"] is True


def test_release_verifier_core_contract_is_exact(tmp_path):
    core=tmp_path/"eyle"/"core"; core.mkdir(parents=True)
    for name in release_identity.CORE_FILES:
        (core/name).write_text("",encoding="utf-8")
    (core/"tools.py").write_text("# resurrected",encoding="utf-8")
    actual={p.name for p in core.glob("*.py")}
    assert actual != release_identity.CORE_FILES


def test_release_verifier_rejects_legacy_provider_paths(tmp_path):
    legacy=tmp_path/"eyle"/"providers"/"standard.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# compatibility facade",encoding="utf-8")
    assert str(legacy.relative_to(tmp_path)).replace("\\","/") in release_identity.FORBIDDEN_PATHS
