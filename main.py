#!/usr/bin/env python3
"""
main.py
-------
Fluxo principal da Eyle:

  Current Request + paged Memory View + Runtime observations
            |
            v
       MAIN LLM cognition
       /        |        \
  Explorar  Construir  Concluir
            |
            v
  Runtime mechanical laws + intrinsic Memory Graph learning

Comandos:
    python main.py perguntar "sua pergunta aqui"
    python main.py benchmark [--baseline-model MODELO]
    python main.py status
    python main.py serve [--host HOST] [--port PORT]   # agente persistente (Flask + Worker)
"""
import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from eyle.runtime.config import ConfigError

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def carregar_config():
    from eyle.runtime.service import carregar_config as carregar
    return carregar()


def carregar_projeto():
    # Workspace is a concern of this bundled product shell, not Runtime.
    # Runtime only exposes opaque Host presentation metadata.
    from eyle.runtime.service import carregar_ambiente
    ambiente = carregar_ambiente()
    projeto = ambiente.get("workspace") if isinstance(ambiente, dict) else None
    return projeto if isinstance(projeto, dict) else None



def cmd_perguntar(args):
    # Mesmo caminho usado pelo Worker e pelo painel web.
    from eyle.runtime.service import processar

    projeto = carregar_projeto()
    if projeto is None:
        print("[main] Nenhum workspace encontrado -- respondendo em modo conversa livre.")
    elif projeto.get("auto_discovered"):
        print(f"[main] Workspace aberto diretamente: {projeto.get('caminho_origem')}")
    else:
        print(f"[main] Projeto disponível: {projeto.get('nome') or projeto.get('caminho_origem')}")
    print("[main] Iniciando AgentSession...")

    resultado = processar(args.pergunta)

    if "erro" in resultado:
        print(f"[main] {resultado['erro']}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("RESPOSTA")
    print("=" * 60)
    print(resultado["resposta"])
    print("=" * 60)

    conclusao_agente = resultado.get("details") or {}
    if conclusao_agente:
        code = conclusao_agente.get("failure_code") or "ok"
        print(
            "\n[agente] "
            f"execution_id={conclusao_agente.get('execution_id')} | "
            f"turns={conclusao_agente.get('turns', 0)} | "
            f"tools={conclusao_agente.get('tools_used', [])} | status={conclusao_agente.get('status')} | code={code}"
        )


def cmd_status(args):
    from eyle.runtime import queue, telemetry

    config = carregar_config()
    projeto = carregar_projeto()
    if projeto is None:
        print("[main] Nenhum workspace encontrado.")
    else:
        print(json.dumps(projeto, ensure_ascii=False, indent=2))
        print("\n[main] Workspace: acesso direto ao código-fonte.")

    conversa_path = os.path.join(MEMORY_DIR, "conversa.json")
    if os.path.exists(conversa_path):
        with open(conversa_path, "r", encoding="utf-8") as f:
            conversa = json.load(f)
        print(f"[main] {len(conversa)} mensagens na conversa persistente.")

    worker_cfg = config.get("worker", {})
    fila = queue.estatisticas(
        stale_after_seconds=worker_cfg.get("stale_worker_seconds", 30),
        blocked_after_seconds=worker_cfg.get("head_of_line_blocked_seconds", 60),
    )
    print("\n[main] Fila/Workers:")
    print(json.dumps(fila, ensure_ascii=False, indent=2))
    if config.get("telemetry", {}).get("enabled", True):
        print("\n[main] Telemetria:")
        print(json.dumps(
            telemetry.summary(config.get("telemetry", {}).get("window_seconds", 3600)),
            ensure_ascii=False, indent=2,
        ))
    if config.get("_config_warnings"):
        print("\n[main] Avisos de configuracao:")
        for warning in config["_config_warnings"]:
            print(f"- {warning['code']}: {warning['detail']}")


def cmd_benchmark(args):
    from eyle.devtools.benchmark import run_benchmark

    output = args.output or os.path.join(BASE_DIR, "context", "benchmark_latest.json")
    report = run_benchmark(
        carregar_config(), baseline_model=args.baseline_model, output_path=output,
        case_ids=args.cases,
    )
    for run in report["runs"]:
        metrics = run["metrics"]
        gate = "PASSED" if metrics["gate_passed"] else "FAILED"
        total = metrics["total_cases"]
        print(
            f"[benchmark] {run['role']} | {run['model']} | gate={gate} "
            f"({metrics['gate_scope']}) | "
            f"read={metrics['correct_read_tasks']}/{total} | "
            f"factual={metrics['factual_answers_correct']}/{total} | "
            f"write={metrics['write_checks_passed']}/{metrics['write_checks_total']} | "
            f"P50={metrics['latency_p50_ms']}ms | P95={metrics['latency_p95_ms']}ms | "
            f"P99={metrics['latency_p99_ms']}ms"
        )
    print(f"[benchmark] Report: {output}")


def cmd_compare_coverage(args):
    from eyle.devtools.coverage_compare import compare_release_coverage_files

    result = compare_release_coverage_files(
        args.baseline, args.candidate, output_path=args.output,
    )
    status = "PASSED" if result.get("ok") else "REGRESSION"
    print(
        f"[coverage] {status} | baseline_cases={result.get('baseline_cases', 0)} | "
        f"candidate_cases={result.get('candidate_cases', 0)} | "
        f"regressions={len(result.get('regressions') or [])}"
    )
    for item in result.get("regressions") or []:
        print(
            f"[coverage][REGRESSION] {item.get('role')}:{item.get('case_id')} | "
            f"{', '.join(item.get('reasons') or [item.get('reason') or 'unknown'])}"
        )
    if args.output:
        print(f"[coverage] Report: {args.output}")
    if not result.get("ok"):
        raise SystemExit(1)


def cmd_compare_efficiency(args):
    from eyle.devtools.token_efficiency import compare_token_efficiency_files

    result = compare_token_efficiency_files(
        args.baseline, args.candidate, output_path=args.output, tolerance=args.tolerance,
    )
    status = "PASSED" if result.get("ok") else "REGRESSION"
    print(
        f"[efficiency] {status} | baseline_cases={result.get('baseline_cases', 0)} | "
        f"candidate_cases={result.get('candidate_cases', 0)} | "
        f"regressions={len(result.get('regressions') or [])} | "
        f"tolerance={float(result.get('tolerance', 0)):.0%}"
    )
    for item in result.get("regressions") or []:
        print(
            f"[efficiency][REGRESSION] {item.get('role')}:{item.get('case_id')} | "
            f"{', '.join(item.get('reasons') or [item.get('reason') or 'unknown'])}"
        )
    if args.output:
        print(f"[efficiency] Report: {args.output}")
    if not result.get("ok"):
        raise SystemExit(1)



def _executar_cache_warmup(config):
    from eyle.core.agent import compile_cache_warmup_prompt
    from eyle.runtime.service import HOST
    from llm.executar import warmup_provider_cache

    prompt = compile_cache_warmup_prompt(config, HOST.provider_context(), HOST.registry)
    return warmup_provider_cache(prompt, config)


def cmd_cache_warmup(args):
    config = carregar_config()
    # Explicit CLI invocation opts in for this call without rewriting config.json.
    config = json.loads(json.dumps(config))
    config.setdefault("llm", {})["cache_warmup"] = True
    result = _executar_cache_warmup(config)
    print("[cache-warmup] " + json.dumps(result, ensure_ascii=False))
    if result.get("status") == "failed":
        raise SystemExit(1)

def cmd_serve(args):
    # Sobe o Worker permanente (thread) + o Flask (web/routes.py). O
    # navegador so fala com o Flask a partir daqui; fechar a aba nao
    # interrompe o Worker.
    from eyle.runtime.worker import iniciar_em_thread
    from llm.executar import diagnosticar_backend
    from web.routes import app, obter_api_token, origem_api_token

    config = carregar_config()
    projeto = carregar_projeto()
    if projeto is None:
        print("[main] Aviso: nenhum workspace foi encontrado; conversas livres continuam disponíveis.")

    diagnostico_llm = diagnosticar_backend(config)
    if diagnostico_llm.get("ok"):
        modelos = diagnostico_llm.get("models") or []
        modelo_txt = f" | modelo(s): {', '.join(modelos[:3])}" if modelos else " | nenhum modelo listado"
        protocolo = diagnostico_llm.get("adapter_protocol") or "unknown"
        versao_adapter = diagnostico_llm.get("adapter_version") or "unknown"
        print(
            f"[main] Adapter LLM online: {diagnostico_llm.get('base_url')}"
            f" ({diagnostico_llm.get('latency_ms')}ms){modelo_txt}"
            f" | protocolo: {protocolo} | adapter: {versao_adapter}"
        )
    else:
        if diagnostico_llm.get("reachable"):
            print(
                f"[main][AVISO] Adapter respondeu, mas o preflight LLM falhou: "
                f"{diagnostico_llm.get('detail')}"
            )
        else:
            print(
                f"[main][AVISO] Adapter LLM indisponivel: {diagnostico_llm.get('detail')}"
            )
        print(
            "[main][AVISO] O painel e o Worker vao iniciar, mas perguntas podem falhar ate "
            "llm.base_url/model estarem corretos e o Adapter/upstream ficar pronto."
        )

    if bool((config.get("llm") or {}).get("cache_warmup", False)):
        warmup = _executar_cache_warmup(config)
        print(f"[main] Cache warmup: {json.dumps(warmup, ensure_ascii=False)}")

    token_api = obter_api_token()
    print(f"[main] Iniciando Worker permanente...")
    iniciar_em_thread()
    print(f"[main] Token da API: {token_api}")
    print(f"[main] Origem do token: {origem_api_token()}")
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(
            "[main] ATENCAO: host externo. Restrinja firewall/rede e use um "
            "proxy HTTPS; o servidor Flask nao cifra o token."
        )
    print(f"[main] Painel: http://{args.host}:{args.port}/")
    print(f"[main] API: POST /enviar, GET /conversa, DELETE /mensagem/<id>, GET /jobs/<id>, GET /status")
    app.run(host=args.host, port=args.port, debug=False)


def main():
    parser = argparse.ArgumentParser(description="Eyle - agente unico para analisar, criar e editar projetos")
    sub = parser.add_subparsers(dest="comando", required=True)



    p_perguntar = sub.add_parser("perguntar", help="Conversa ou executa o Agente Eyle conforme o pedido")
    p_perguntar.add_argument("pergunta")
    p_perguntar.set_defaults(func=cmd_perguntar)

    p_status = sub.add_parser("status", help="Mostra workspace, conversa, fila e telemetria")
    p_status.set_defaults(func=cmd_status)

    p_benchmark = sub.add_parser(
        "benchmark", help="Run the full benchmark suite or a smoke subset of the utility gate",
    )
    p_benchmark.add_argument(
        "--baseline-model", default=None,
        help="Exact baseline model name loaded by the backend (optional)",
    )
    p_benchmark.add_argument(
        "--output", default=None,
        help="JSON report path (default: context/benchmark_latest.json)",
    )
    p_benchmark.add_argument(
        "--cases", default=None,
        help=(
            "Comma-separated case IDs for a smoke test, for example "
            "greeting,analyze_single_file,edit_confirmed"
        ),
    )
    p_benchmark.set_defaults(func=cmd_benchmark)

    p_compare = sub.add_parser(
        "compare-coverage",
        help="Compare behavior coverage between two benchmark reports",
    )
    p_compare.add_argument("baseline", help="Baseline benchmark JSON report")
    p_compare.add_argument("candidate", help="Candidate benchmark JSON report")
    p_compare.add_argument("--output", default=None, help="Save the comparison report as JSON")
    p_compare.set_defaults(func=cmd_compare_coverage)

    p_efficiency = sub.add_parser(
        "compare-efficiency",
        help="Compare calls and token usage between two benchmark reports",
    )
    p_efficiency.add_argument("baseline", help="Baseline benchmark JSON report")
    p_efficiency.add_argument("candidate", help="Candidate benchmark JSON report")
    p_efficiency.add_argument(
        "--tolerance", type=float, default=0.10,
        help="Relative token tolerance (default: 0.10)",
    )
    p_efficiency.add_argument("--output", default=None, help="Save the comparison as JSON")
    p_efficiency.set_defaults(func=cmd_compare_efficiency)

    p_warmup = sub.add_parser("cache-warmup", help="Faz um warmup opcional do prefixo estável usando o transporte LLM configurado")
    p_warmup.set_defaults(func=cmd_cache_warmup)

    p_serve = sub.add_parser("serve", help="Sobe o agente persistente (Worker + Flask) -- requer 'pip install flask'")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=5000)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    try:
        carregar_config()
    except ConfigError as erro:
        print(f"[config][erro] {erro}", file=sys.stderr)
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()
