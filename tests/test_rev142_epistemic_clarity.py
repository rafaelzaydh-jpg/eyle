import json

from eyle.core import agent, tools
from eyle.core.token_budget import estimate_tokens
from llm.executar import PROMPT_AGENTE
from tests.canonical import agent_final, base_config


def test_prompt_distinguishes_epistemic_sources_without_mandating_workflow():
    text = PROMPT_AGENTE
    assert "prior_conversation is retained context" in text
    assert "Memory is persistent prior cognition" in text
    assert "available_capabilities names invokable actions" in text
    assert "not evidence of current workspace or implementation state" in text
    assert "runtime_observations/current_material represent current physically observed state" in text
    assert "do not present prior context, Memory, capability metadata or inference as newly observed fact" in text
    assert "direct Final remains valid" in text
    lower = text.lower()
    for forced in (
        "must use a capability", "must create a task", "must create an investigation",
        "always inspect", "always use tools", "if tools", "before answering, inspect",
    ):
        assert forced not in lower


def test_model_payload_uses_epistemically_clear_projection_names(monkeypatch, tmp_path):
    seen = []

    def fake(prompt, _config):
        payload = json.loads(prompt)
        seen.append(payload)
        return agent_final("ok")

    monkeypatch.setattr(agent, "executar_agente_llm", fake)
    status, text, _, _ = agent.executar_agente(
        "oi", base_config(), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success" and text == "ok"
    payload = seen[0]
    assert "prior_conversation" in payload
    assert "available_capabilities" in payload
    assert "runtime_observations" in payload
    assert "latest_capability_results" in payload
    assert "current_material" in payload
    for old in (
        "conversation_background", "capability_index", "observation_map",
        "latest_tool_results", "grounding_index",
    ):
        assert old not in payload


def test_memory_contract_states_prior_context_boundary():
    caveats = tools.TOOLS["memory_search"].get("caveats") or []
    assert any("not proof of current external state" in str(item) for item in caveats)


def test_epistemic_clarity_does_not_regress_fixed_prompt_size():
    assert len(PROMPT_AGENTE) < 1700
    assert estimate_tokens(PROMPT_AGENTE, 3) <= 540
