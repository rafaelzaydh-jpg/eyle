import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.agent import _normalizar_conclusao
from engine.agent_state import AgentState
from engine.agent_tools import gerar_catalogo_tools
from engine.compiler import montar_prompt_agente
from llm.executar import PROMPT_AGENTE


def test_agent_system_protocol_is_english_and_keeps_user_language_contract():
    assert "Allowed JSON formats" in PROMPT_AGENTE
    assert '"answer"' in PROMPT_AGENTE
    assert '"important_fact"' in PROMPT_AGENTE
    assert "answer in Brazilian Portuguese" in PROMPT_AGENTE
    assert "Voce e o AGENTE" not in PROMPT_AGENTE


def test_prompt_projects_state_and_tool_rules_in_english_without_translating_request():
    estado = AgentState(config={})
    estado.definir_objetivo("Encontre a função normalize", "project_read", modo="analyze")
    prompt = montar_prompt_agente(
        "Encontre a função normalize",
        goal_state=estado.goal_state,
        catalogo_tools=gerar_catalogo_tools(config={}),
        evidencias=[], actions=[], config={}, system_prompt=PROMPT_AGENTE,
    )
    assert "ORIGINAL USER REQUEST" in prompt
    assert "Encontre a função normalize" in prompt
    assert "TOOL CATALOG" in prompt
    assert "fresh_code_read" in prompt
    assert "read_only" in prompt
    assert "CATALOGO DE FERRAMENTAS" not in prompt


def test_english_final_json_is_canonical_but_legacy_portuguese_still_works():
    estado = AgentState(config={})
    estado.definir_objetivo("oi", "chat", modo="chat")
    novo, erro = _normalizar_conclusao({
        "final": {
            "answer": "Olá",
            "evidence_ids": [],
            "verification": "not applicable",
            "limitations": [],
        }
    }, estado, "chat")
    assert erro is None
    assert novo["resposta"] == "Olá"

    legado, erro = _normalizar_conclusao({
        "final": {
            "resposta": "Olá de novo",
            "evidence_ids": [],
            "verificacao": "não aplicável",
            "limitacoes": [],
        }
    }, estado, "chat")
    assert erro is None
    assert legado["resposta"] == "Olá de novo"


def test_tool_catalog_descriptions_are_english():
    catalogo = gerar_catalogo_tools(config={})
    read_range = next(item for item in catalogo if item["name"] == "read_range")
    assert read_range["description"].startswith("Read a small")
    assert read_range["output_schema"].startswith("Standard envelope")
