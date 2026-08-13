"""Deterministic post-write verification for confirmed edits.

Verification stays outside the LLM loop. After a confirmed write the
runtime compiles changed Python files in an isolated temporary copy, runs the
project test suite when one exists, and rereads every promised output from the
live workspace before it can report success.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List

from .security import _resolver_caminho_seguro
from .text_hash import hash_texto



def _tests_config(config):
    providers = (config or {}).get("providers") or {}
    standard = providers.get("standard") or {} if isinstance(providers, dict) else {}
    tests = standard.get("tests") or {} if isinstance(standard, dict) else {}
    return tests if isinstance(tests, dict) else {}

def _unique_python_paths(paths: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for raw in paths or []:
        path = str(raw or "").strip().replace("\\", "/")
        if not path or not path.lower().endswith(".py") or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def run_compileall_for_changes(project_root: str, paths: Iterable[str], timeout_seconds: int = 60) -> Dict[str, Any]:
    """Run the real ``compileall`` module for changed Python files.

    Files are copied to a temporary tree first, so verification never leaves
    ``__pycache__`` artifacts in the user's workspace and never executes project
    code. Deleted Python files are naturally omitted because there is no final
    source file to compile.
    """
    python_paths = _unique_python_paths(paths)
    copied: List[str] = []
    with tempfile.TemporaryDirectory(prefix="eyle-compileall-") as temp_root:
        for path in python_paths:
            absolute = _resolver_caminho_seguro(project_root, path)
            if absolute is None:
                return {
                    "required": True,
                    "executed": False,
                    "ok": False,
                    "error_code": "COMPILEALL_UNSAFE_PATH",
                    "detail": f"Caminho Python inseguro durante compileall: {path}.",
                    "files": copied,
                }
            if not os.path.isfile(absolute):
                continue
            destination = os.path.join(temp_root, *path.split("/"))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            try:
                shutil.copyfile(absolute, destination)
            except OSError as error:
                return {
                    "required": True,
                    "executed": False,
                    "ok": False,
                    "error_code": "COMPILEALL_COPY_FAILED",
                    "detail": f"Não foi possível preparar {path} para compileall: {error}.",
                    "files": copied,
                }
            copied.append(path)

        if not copied:
            return {
                "required": False,
                "executed": False,
                "ok": True,
                "error_code": None,
                "detail": "Nenhum arquivo Python final precisava de compileall.",
                "files": [],
            }

        command = [sys.executable, "-m", "compileall", "-q", "--", *copied]
        try:
            completed = subprocess.run(
                command,
                cwd=temp_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, int(timeout_seconds)),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            return {
                "required": True,
                "executed": False,
                "ok": False,
                "error_code": "COMPILEALL_EXECUTION_FAILED",
                "detail": f"compileall não pôde ser executado: {error}.",
                "files": copied,
            }

        output = (completed.stdout or "").strip()[-4000:]
        if completed.returncode != 0:
            return {
                "required": True,
                "executed": True,
                "ok": False,
                "error_code": "COMPILEALL_FAILED",
                "detail": f"compileall falhou para {', '.join(copied)}.\n{output}".rstrip(),
                "files": copied,
            }
        return {
            "required": True,
            "executed": True,
            "ok": True,
            "error_code": None,
            "detail": f"compileall passou para {len(copied)} arquivo(s) Python.",
            "files": copied,
        }


def verify_after_write(config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Run the configured project test capability after a confirmed write.

    This code-domain verification policy belongs to the post-write boundary,
    not Agent's capability-selection logic.
    """
    enabled = bool(_tests_config(config).get("enabled", False))
    if not enabled:
        return {
            "status": "skipped", "ok": True, "executed": False,
            "error_code": "TESTS_DISABLED", "detail": "Execução de testes desativada explicitamente.",
        }
    registry = (context or {}).get("registry")
    if registry is None or "run_tests" not in registry.names():
        return {
            "status": "skipped", "ok": True, "executed": False,
            "error_code": "TESTS_NOT_FOUND", "detail": "Capability de testes indisponível.",
        }
    return registry.execute("run_tests", {}, context)


def expected_outputs_from_patches(applied_patches: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outputs: List[Dict[str, Any]] = []
    for patch in applied_patches or []:
        path = str(patch.get("path") or "").replace("\\", "/")
        operation = str(patch.get("operation") or "")
        item: Dict[str, Any] = {"path": path, "operation": operation}
        if operation != "delete":
            content = patch.get("result_content")
            if not isinstance(content, str):
                content = patch.get("content")
            if isinstance(content, str):
                item["expected_hash"] = hash_texto(content)
        outputs.append(item)
    return outputs


def verify_expected_outputs(project_root: str, outputs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Reread every changed file and confirm promised creates/deletes exactly."""
    checked: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    for expected in outputs or []:
        path = str(expected.get("path") or "").replace("\\", "/")
        operation = str(expected.get("operation") or "")
        absolute = _resolver_caminho_seguro(project_root, path)
        if absolute is None:
            failures.append({"path": path, "reason": "unsafe_path"})
            continue
        if operation == "delete":
            if os.path.exists(absolute):
                failures.append({"path": path, "reason": "delete_not_applied"})
            else:
                checked.append({"path": path, "operation": operation, "state": "absent"})
            continue
        if not os.path.isfile(absolute):
            reason = "created_file_missing" if operation == "create" else "written_file_missing"
            failures.append({"path": path, "reason": reason})
            continue
        try:
            with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError as error:
            failures.append({"path": path, "reason": f"reread_failed:{error}"})
            continue
        current_hash = hash_texto(content)
        expected_hash = expected.get("expected_hash")
        if expected_hash and current_hash != expected_hash:
            failures.append({"path": path, "reason": "content_mismatch"})
            continue
        checked.append({
            "path": path,
            "operation": operation,
            "state": "present",
            "file_hash": current_hash,
            "bytes": len(content.encode("utf-8")),
        })
    return {
        "ok": not failures,
        "checked": checked,
        "failures": failures,
        "detail": (
            f"{len(checked)} saída(s) relida(s) e confirmada(s)."
            if not failures else
            "Falha na releitura final: " + ", ".join(
                f"{item['path']} ({item['reason']})" for item in failures
            )
        ),
    }
