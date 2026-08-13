import inspect
import json

from eyle.core import agent, tools
from eyle.core.agent import AgentSession
from llm.executar import PROMPT_AGENTE
from llm.structured import contract_instruction


def _walk_descriptions(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "description" and isinstance(item, str):
                yield item
            yield from _walk_descriptions(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_descriptions(item)


def test_fixed_model_surface_is_path_or_fact_not_strategy():
    fixed = [PROMPT_AGENTE, contract_instruction("agent")]
    for entry in tools.TOOLS.values():
        fixed.append(str(entry.get("description") or ""))
        fixed.append(str(entry.get("returns") or ""))
        fixed.extend(str(item) for item in (entry.get("caveats") or []))
        fixed.extend(_walk_descriptions(entry.get("input_schema") or {}))
    text = "\n".join(fixed).lower()
    for phrase in (
        "not a prerequisite",
        "usually do not need",
        "do not create",
        "never use it merely",
        "choose one capability",
        "decide again from the unchanged",
    ):
        assert phrase not in text


def test_transport_instruction_does_not_reteach_schema_semantics():
    instruction = contract_instruction("agent")
    assert len(instruction) < 220
    assert "may be empty" in instruction
    assert "completion_criteria" not in instruction
    assert "before Final" not in instruction


def test_runtime_feedback_has_no_instruction_channel():
    source = inspect.getsource(agent)
    assert '"instruction"' not in source
    assert "Choose one capability from capability_index" not in source
    assert "semantic_followup" not in source


def test_capability_index_stays_discovery_only_and_compact():
    index = tools.gerar_indice_capabilities()
    encoded = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    assert len(index) == len(tools.TOOLS)
    assert len(encoded) < 1800
    assert "symbol_relations(symbol:str" in "\n".join(index)
    assert "..." in next(item for item in index if item.startswith("symbol_relations("))


def test_direct_final_requires_no_artificial_task_or_investigation(monkeypatch, tmp_path):
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        return {
            "action": {"kind": "final", "answer": "Ooi 😄", "limitations": [], "grounding_ids": []},
            "investigation_updates": [],
            "task_updates": [],
        }

    monkeypatch.setattr(agent, "executar_agente_llm", fake)
    status, text, pending, _details = agent.executar_agente(
        "ooi", {"llm": {"context_window_tokens": 10000, "agent_max_tokens": 3600}, "context_engine": {"safety_margin_tokens": 500, "chars_per_token_fallback": 3, "cached_prompt_weight": 0.2}, "agent": {}, "codar": {"ativado": True, "testes": {"ativado": False}}},
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text == "Ooi 😄"
    assert pending is None
    assert prompts[0]["investigation"] == []
    assert "task_state" not in prompts[0]


def test_runtime_no_action_feedback_reports_state_without_strategy(monkeypatch, tmp_path):
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {"action": {}, "investigation_updates": [], "task_updates": []}
        assert payload["runtime_feedback"]
        notice = json.loads(payload["runtime_feedback"])
        assert notice["code"] == "NO_ACTION"
        assert notice["state_unchanged"] is True
        assert "instruction" not in notice
        return {"action": {"kind": "final", "answer": "ok", "limitations": [], "grounding_ids": []}, "investigation_updates": [], "task_updates": []}

    monkeypatch.setattr(agent, "executar_agente_llm", fake)
    status, text, _, _ = agent.executar_agente(
        "oi", {"llm": {"context_window_tokens": 10000, "agent_max_tokens": 3600}, "context_engine": {"safety_margin_tokens": 500, "chars_per_token_fallback": 3, "cached_prompt_weight": 0.2}, "agent": {}, "codar": {"ativado": True, "testes": {"ativado": False}}},
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text == "ok"


def test_capability_model_prose_has_hard_concision_bounds():
    for name, entry in tools.TOOLS.items():
        assert len(str(entry.get("description") or "")) <= 90, name
        assert len(str(entry.get("returns") or "")) <= 100, name
        for caveat in entry.get("caveats") or []:
            assert len(str(caveat)) <= 120, name
        for description in _walk_descriptions(entry.get("input_schema") or {}):
            assert len(description) <= 120, (name, description)


def _non_docstring_literals(path):
    import ast
    from pathlib import Path
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                doc_nodes.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            yield node.value


def test_model_return_path_strings_do_not_smuggle_strategy_advice():
    text = "\n".join(
        literal
        for path in ("eyle/core/agent.py", "eyle/core/tools.py", "eyle/core/sandbox.py")
        for literal in _non_docstring_literals(path)
    ).lower()
    for phrase in (
        "choose one capability",
        "use an open fr-*",
        "; use replace",
        "decide again from the unchanged",
        "should use",
        "try another tool",
    ):
        assert phrase not in text
