from __future__ import annotations

from eyle.core.ecc import catalog
from llm.executar import PROMPT_ECC
from llm.structured import contract_instruction
from tests.canonical import base_config, standard_registry


def test_rev253_prompt_is_shorter_clearer_and_teaches_intuitive_memory():
    lower = PROMPT_ECC.lower()
    assert len(PROMPT_ECC) < 7000
    assert "understand what the user means" in lower
    assert "not every message is a task" in lower
    assert "anything that may be useful again in the future can become memory" in lower
    assert "do not need to wait for the user" in lower
    assert "fresh does not mean" in lower
    assert "memory can also be wrong" in lower
    assert "having a possible answer is not the same as having enough support" in lower
    assert "do not optimize memory by guessing" not in lower
    assert "sole semantic authority" not in lower
    assert "transient semantic interpretation" not in lower


def test_rev253_structured_instruction_is_only_a_small_format_reminder():
    instruction = contract_instruction("ecc")
    assert len(instruction) < 700
    assert "one ecc json object only" in instruction.lower()
    assert "memory" in instruction.lower()
    assert "objective" in instruction.lower()
    assert "'unchanged'" not in instruction


def test_recall_language_is_simple_and_has_no_task_bias():
    registry = standard_registry()
    surface = catalog(registry, base_config(), registry.names())
    recall = next(item for item in surface["explorar"] if item["operation"] == "recall")
    text = str(recall).lower()
    assert "this run" in text
    assert "active-task" not in text
    assert "active task" not in text


def test_standard_ecc_operation_catalog_is_smaller_and_plain(tmp_path):
    registry = standard_registry()
    cfg = base_config(tests_enabled=True)
    available = registry.available_names({
        "config": cfg,
        "provider_context": {
            "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
        },
    })
    surface = catalog(registry, cfg, available)
    import json
    text = json.dumps(surface, ensure_ascii=False, separators=(",", ":"))
    lower = text.lower()
    assert len(text) < 6500
    assert "bounded tree" not in lower
    assert "semantic ranking" not in lower
    assert "runtime-reachability proof" not in lower
    assert "safe copy" in lower


def test_public_history_exposes_objective_presence_without_objective_body():
    from eyle.runtime.history import build_public_job_history

    history = build_public_job_history({
        "id": 7,
        "status": "completed",
        "resultado": {
            "details": {
                "status": "completed",
                "turns": 3,
                "objective_present": True,
                "objective_children": 2,
                "llm_calls": [],
                "operation_history": [],
                "llm_usage": {},
            }
        },
    })
    assert history["agent"]["objective_present"] is True
    assert history["agent"]["objective_children"] == 2
    assert "objective_state" not in history["agent"]
