"""Physical transaction atomicity, rollback and stale-state tests."""
from __future__ import annotations

import os

import eyle.core.transactions as transactions
from eyle.core.text_hash import hash_texto


def _replace(path, before, after):
    return {
        "operation": "replace",
        "path": path,
        "content": after,
        "file_hash_expected": hash_texto(before),
    }


def test_multi_file_transaction_rolls_back_first_write_when_second_fails(monkeypatch, tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    real_write = transactions._escrever_arquivo_atomico
    calls = []

    def fail_second(path, content):
        calls.append(os.path.basename(path))
        if os.path.basename(path) == "second.txt":
            raise OSError("simulated second write failure")
        return real_write(path, content)

    monkeypatch.setattr(transactions, "_escrever_arquivo_atomico", fail_second)
    result = transactions.apply_patch_set(str(tmp_path), [
        _replace("first.txt", "one\n", "ONE\n"),
        _replace("second.txt", "two\n", "TWO\n"),
    ])

    assert result["ok"] is False
    assert result["error_code"] == "PATCH_TRANSACTION_FAILED"
    assert result["rollback_confirmed"] is True
    assert result["applied_before_failure"] == ["first.txt"]
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"
    assert calls[:2] == ["first.txt", "second.txt"]


def test_transaction_reports_unconfirmed_rollback_instead_of_hiding_it(monkeypatch, tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    real_write = transactions._escrever_arquivo_atomico

    def fail_second(path, content):
        if os.path.basename(path) == "second.txt":
            raise OSError("apply failure")
        return real_write(path, content)

    monkeypatch.setattr(transactions, "_escrever_arquivo_atomico", fail_second)
    monkeypatch.setattr(transactions, "rollback_patch_set", lambda patches: {
        "ok": False, "failures": ["first.txt: rollback failure"], "restored": [],
    })

    result = transactions.apply_patch_set(str(tmp_path), [
        _replace("first.txt", "one\n", "ONE\n"),
        _replace("second.txt", "two\n", "TWO\n"),
    ])

    assert result["ok"] is False
    assert result["error_code"] == "PATCH_TRANSACTION_ROLLBACK_FAILED"
    assert result["rollback_confirmed"] is False
    assert result["rollback"]["failures"]


def test_create_rollback_removes_created_file_and_empty_directories(tmp_path):
    result = transactions.apply_patch_set(str(tmp_path), [{
        "operation": "create",
        "path": "nested/deeper/new.txt",
        "content": "created\n",
    }])
    assert result["ok"] is True
    assert (tmp_path / "nested" / "deeper" / "new.txt").is_file()

    rollback = transactions.rollback_patch_set(result["applied_patches"])
    assert rollback["ok"] is True
    assert not (tmp_path / "nested" / "deeper" / "new.txt").exists()
    assert not (tmp_path / "nested").exists()


def test_delete_rollback_restores_exact_content(tmp_path):
    target = tmp_path / "old.txt"
    original = "alpha\nbeta\n"
    target.write_text(original, encoding="utf-8")
    result = transactions.apply_patch_set(str(tmp_path), [{
        "operation": "delete",
        "path": "old.txt",
        "file_hash_expected": hash_texto(original),
    }])
    assert result["ok"] is True
    assert not target.exists()

    rollback = transactions.rollback_patch_set(result["applied_patches"])
    assert rollback["ok"] is True
    assert target.read_text(encoding="utf-8") == original


def test_stale_hash_fails_before_any_mutation(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")

    result = transactions.apply_patch_set(str(tmp_path), [
        _replace("first.txt", "WRONG\n", "ONE\n"),
        _replace("second.txt", "two\n", "TWO\n"),
    ])
    assert result["ok"] is False
    assert result["error_code"] == "STALE_PATCH"
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"
