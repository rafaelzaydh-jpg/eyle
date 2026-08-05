#!/usr/bin/env python3
"""Fallback em camadas para conclusoes vazias, invalidas ou sem utilidade."""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from llm.executar import ErroLLM, executar_recuperacao_textual
from engine.utility_gate import validate_response_utility


_MAX_EVIDENCE_CHARS = 18000


def _citation(item):
    filename = str(item.get("arquivo") or "").replace("\\", "/")
    start = item.get("linha_inicio")
    end = item.get("linha_fim")
    if not filename or not isinstance(start, int) or not isinstance(end, int):
        return ""
    return f"{filename}:{start}-{end}" if start != end else f"{filename}:{start}"


def _raw_content(item):
    raw = item.get("conteudo_raw")
    if isinstance(raw, str) and raw.strip():
        return raw
    numbered = str(item.get("conteudo") or "")
    lines = []
    for line in numbered.splitlines():
        lines.append(re.sub(r"^\s*\d+\s*[|:]\s?", "", line))
    return "\n".join(lines)


def _evidence_prompt(objective, evidence, *, compact=False, prior_answer="", cause=""):
    blocks = []
    used = 0
    for item in evidence or []:
        if not isinstance(item, dict) or item.get("estado") not in (None, "fresh"):
            continue
        citation = _citation(item)
        content = _raw_content(item)
        if not citation or not content.strip():
            continue
        block = f"\n--- {citation} ---\n{content.strip()}\n"
        remaining = _MAX_EVIDENCE_CHARS - used
        if remaining <= 0:
            break
        block = block[:remaining]
        blocks.append(block)
        used += len(block)

    instruction = (
        "Write 2 to 4 direct sentences. State what the code is, what it does, "
        "and one relevant observation or recommendation. Use only the evidence."
        if compact else
        "Write a useful final analysis in plain text. Explain the observed architecture or behavior, "
        "then give a relevant inference, risk, or recommendation when supported."
    )
    return (
        f"USER REQUEST:\n{objective}\n\nRECOVERY CAUSE:\n{cause or 'unusable primary response'}\n\n"
        f"PREVIOUS ANSWER (do not repeat a receipt-only answer):\n{str(prior_answer or '')[:1200]}\n\n"
        f"INSTRUCTION:\n{instruction}\n\nFRESH EVIDENCE:\n{''.join(blocks)}"
    )


def _node_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _node_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _node_name(node.func)
    return ""


def _literal(node, source):
    try:
        value = ast.literal_eval(node)
    except Exception:
        value = None
    if isinstance(value, (str, int, float, bool)):
        return repr(value) if isinstance(value, str) else str(value)
    segment = ast.get_source_segment(source, node)
    return str(segment or "").strip()


def _python_observations(item):
    source = _raw_content(item)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    imports = []
    functions = []
    classes = []
    assignments = []
    scalar_assignments = []
    calls = []
    routes = []
    env_vars = []
    run_details = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
            for decorator in node.decorator_list:
                name = _node_name(decorator)
                if name.endswith(".route") and isinstance(decorator, ast.Call) and decorator.args:
                    routes.append(_literal(decorator.args[0], source))
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Assign):
            target_names = [_node_name(target) for target in node.targets]
            if isinstance(node.value, ast.Call):
                call_name = _node_name(node.value.func)
                for target in target_names:
                    if target and call_name:
                        assignments.append((target, call_name))
            else:
                value = _literal(node.value, source)
                for target in target_names:
                    if target:
                        scalar_assignments.append((target, value))
        elif isinstance(node, ast.AnnAssign):
            target = _node_name(node.target)
            if target and node.value is not None:
                if isinstance(node.value, ast.Call):
                    call_name = _node_name(node.value.func)
                    if call_name:
                        assignments.append((target, call_name))
                else:
                    scalar_assignments.append((target, _literal(node.value, source)))
        elif isinstance(node, ast.Call):
            name = _node_name(node.func)
            if name:
                calls.append(name)
            if name in ("os.getenv", "os.environ.get") and node.args:
                env = _literal(node.args[0], source).strip("'\"")
                if env:
                    env_vars.append(env)
            if name.endswith(".run"):
                kwargs = {kw.arg: _literal(kw.value, source) for kw in node.keywords if kw.arg}
                run_details = (name, kwargs)

    observations = []
    if imports:
        observations.append(("imports", list(dict.fromkeys(imports))[:6]))
    if assignments:
        observations.append(("assignments", list(dict.fromkeys(assignments))[:4]))
    if scalar_assignments:
        observations.append(("scalar_assignments", list(dict.fromkeys(scalar_assignments))[:24]))
    if functions:
        observations.append(("functions", list(dict.fromkeys(functions))[:6]))
    if classes:
        observations.append(("classes", list(dict.fromkeys(classes))[:4]))
    if routes:
        observations.append(("routes", list(dict.fromkeys(routes))[:5]))
    if env_vars:
        observations.append(("env_vars", list(dict.fromkeys(env_vars))[:5]))
    if run_details:
        observations.append(("run", run_details))
    elif calls:
        filtered = [name for name in dict.fromkeys(calls) if name not in {"print"}]
        if filtered:
            observations.append(("calls", filtered[:6]))
    return observations


def _join_code(values):
    return ", ".join(f"`{value}`" for value in values if value)


def build_deterministic_analysis(objective, evidence):
    """Gera uma analise basica do estado observado sem depender da LLM."""
    fresh = [item for item in evidence or [] if isinstance(item, dict) and item.get("estado") in (None, "fresh")]
    if not fresh:
        return ""

    sentences = []
    files = []
    for item in fresh[:4]:
        filename = str(item.get("arquivo") or "").replace("\\", "/")
        citation = _citation(item)
        if not filename or not citation:
            continue
        files.append(filename)
        suffix = PurePosixPath(filename).suffix.lower()
        observations = _python_observations(item) if suffix == ".py" else []
        obs = dict(observations)

        if obs.get("assignments"):
            pairs = [f"`{target}` com `{call}`" for target, call in obs["assignments"]]
            sentence = f"Em {citation}, o código cria " + " e ".join(pairs[:2])
            if obs.get("imports"):
                sentence += f" após importar {_join_code(obs['imports'][:4])}"
            sentences.append(sentence + ".")
        elif obs.get("scalar_assignments"):
            pairs = obs["scalar_assignments"]
            names = [name for name, _ in pairs]
            if len(names) == 1:
                name, value = pairs[0]
                value_text = f" com o valor `{value}`" if value else ""
                sentences.append(f"Em {citation}, o trecho define `{name}`{value_text} como estado ou configuração observada.")
            elif len(names) <= 5:
                sentences.append(f"Em {citation}, o arquivo define as variáveis {_join_code(names)}.")
            else:
                sentences.append(
                    f"Em {citation}, o arquivo contém {len(names)} atribuições, de `{names[0]}` a `{names[-1]}`."
                )
        elif obs.get("imports") or obs.get("functions") or obs.get("classes"):
            parts = []
            if obs.get("imports"):
                parts.append(f"importa {_join_code(obs['imports'][:5])}")
            if obs.get("functions"):
                parts.append(f"define as funções {_join_code(obs['functions'][:5])}")
            if obs.get("classes"):
                parts.append(f"define as classes {_join_code(obs['classes'][:4])}")
            sentences.append(f"Em {citation}, o arquivo " + " e ".join(parts) + ".")

        if obs.get("routes"):
            sentences.append(
                f"O mesmo trecho registra rota(s) {_join_code(obs['routes'])}, mostrando que o arquivo expõe comportamento HTTP."
            )
        if obs.get("run"):
            run_name, kwargs = obs["run"]
            details = []
            for key in ("host", "port", "debug"):
                if kwargs.get(key):
                    details.append(f"`{key}`={kwargs[key]}")
            suffix_text = f" com {', '.join(details)}" if details else ""
            sentences.append(f"A execução chama `{run_name}`{suffix_text}, portanto esse trecho contém o ponto de inicialização do servidor.")
        elif obs.get("calls") and len(sentences) < 3:
            sentences.append(
                f"O fluxo observado chama {_join_code(obs['calls'][:5])}, o que descreve a responsabilidade operacional principal desse trecho."
            )
        if obs.get("env_vars"):
            sentences.append(
                f"A configuração lê a(s) variável(is) de ambiente {_join_code(obs['env_vars'])}, permitindo alterar valores sem editar o código."
            )
        if len(sentences) >= 4:
            break

    if not sentences:
        # Fallback generico ainda descreve conteudo real, nao apenas a faixa.
        item = fresh[0]
        citation = _citation(item)
        content = _raw_content(item)
        assignment = re.search(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\n#]+)", content, re.MULTILINE)
        definition = re.search(r"^\s*(?:def|class|function)\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.MULTILINE)
        if assignment:
            name = assignment.group(1)
            sentences.append(f"Em {citation}, o trecho define `{name}` e concentra nele uma parte do estado ou da configuração observada.")
        elif definition:
            name = definition.group(1)
            sentences.append(f"Em {citation}, o trecho define `{name}` como um dos componentes executáveis do projeto.")
        else:
            words = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", content) if token not in {"return", "import", "from"}]
            unique = list(dict.fromkeys(words))[:4]
            if unique:
                sentences.append(f"Em {citation}, o trecho trabalha com {_join_code(unique)}, que são os elementos concretos visíveis nessa parte do projeto.")

    # Nao adiciona uma conclusao generica sobre arquivos nao lidos: isso seria
    # uma inferencia sem evidencia e faria o grounding tipado rejeitar justamente
    # o fallback criado para ser seguro.
    return "\n".join(sentences[:5]).strip()


def recover_useful_response(objective, evidence, config, *, cause, prior_answer="", allow_llm=True, task_type="project_read"):
    """Executa retry textual, retry curto e fallback deterministico."""
    attempts = []
    recovery_cfg = ((config or {}).get("agent") or {}).get("response_recovery") or {}
    llm_configured = (
        recovery_cfg.get("llm_enabled") is True
        and isinstance((config or {}).get("llm"), dict)
        and bool((config or {}).get("llm"))
    )
    if allow_llm and llm_configured and evidence:
        for layer, compact in (("unstructured_retry", False), ("evidence_short_generation", True)):
            prompt = _evidence_prompt(
                objective, evidence, compact=compact,
                prior_answer=prior_answer, cause=cause,
            )
            try:
                answer = executar_recuperacao_textual(prompt, config)
            except ErroLLM as error:
                attempts.append({"layer": layer, "ok": False, "error_code": error.error_code or "LLM_FAILURE"})
                continue
            gate = validate_response_utility(
                answer, objective, task_type=task_type, evidence=evidence,
            )
            attempts.append({"layer": layer, "ok": gate.get("ok", False), "utility_gate": gate})
            if gate.get("ok"):
                return {
                    "ok": True,
                    "answer": str(answer).strip(),
                    "layer": layer,
                    "attempts": attempts,
                    "utility_gate": gate,
                }

    answer = build_deterministic_analysis(objective, evidence)
    gate = validate_response_utility(
        answer, objective, task_type=task_type, evidence=evidence,
    )
    attempts.append({"layer": "deterministic_analysis", "ok": gate.get("ok", False), "utility_gate": gate})
    if gate.get("ok"):
        return {
            "ok": True,
            "answer": answer,
            "layer": "deterministic_analysis",
            "attempts": attempts,
            "utility_gate": gate,
        }
    return {
        "ok": False,
        "answer": "",
        "layer": None,
        "attempts": attempts,
        "utility_gate": gate,
        "failure_code": "NO_USEFUL_RESPONSE",
    }
