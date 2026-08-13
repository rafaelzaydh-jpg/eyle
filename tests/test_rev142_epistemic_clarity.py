from tests.canonical import run_agent
import json

from eyle.core import agent
import eyle.providers.memory as memory_provider
from eyle.providers import standard as tools
from eyle.core.token_budget import estimate_tokens
from llm.executar import PROMPT_AGENTE
from tests.canonical import agent_complete, base_config


def test_prompt_distinguishes_epistemic_sources_without_mandating_workflow():
    text = PROMPT_AGENTE
    assert "prior_conversation can resolve references" in text
    assert "Persistent Memory and prior_conversation are context" in text
    assert "available_capabilities is the capability surface physically available now" in text
    assert "An available capability is not evidence that it was called" in text
    assert "runtime_observations and current_material contain observations" in text
    assert "Leave those arrays empty when the answer genuinely relies only" in text
    lower = text.lower()
    for forced in (
        "must use a capability", "must create a task", "must create an investigation",
        "always inspect", "always use tools", "before answering, inspect",
    ):
        assert forced not in lower

def test_model_payload_uses_epistemically_clear_projection_names(monkeypatch, tmp_path):
    seen = []

    def fake(prompt, _config):
        payload = json.loads(prompt)
        seen.append(payload)
        return agent_complete("ok")

    monkeypatch.setattr(agent, "executar_agente_llm", fake)
    status, text, _, _ = run_agent(agent, 
        "oi", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
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
    caveats = memory_provider.CAPABILITIES["search"].get("caveats") or []
    assert any("not proof of current external state" in str(item) for item in caveats)


def test_epistemic_clarity_is_not_constrained_by_old_cost_size_target():
    assert "WORLD AND CAPABILITIES" in PROMPT_AGENTE
    assert "COMPLETE COORDINATES" in PROMPT_AGENTE
    assert estimate_tokens(PROMPT_AGENTE, 3) > 570

