#!/usr/bin/env python3
"""
main.py
-------
Fluxo principal da Eyle:

  Projeto de 30k-100k+ tokens
            |
            v
  Memória externa sob demanda (memory/agent_memory)
            |
            v
  Eyle Agent -> tools -> evidence -> validation
            |
            v
  Persiste conversa e confirmação pendente

Comandos:
    python main.py perguntar "sua pergunta aqui"
    python main.py benchmark [--baseline-model MODELO]  # Atualizacao 47
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
    from eyle.runtime.service import carregar_projeto as carregar
    return carregar()



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
            f"task_id={conclusao_agente.get('task_id')} | "
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
    from eyle.devtools.benchmark import rodar_benchmark

    output = args.output or os.path.join(BASE_DIR, "context", "benchmark_latest.json")
    relatorio = rodar_benchmark(
        carregar_config(), baseline_model=args.baseline_model, output_path=output,
        case_ids=args.cases,
    )
    for execucao in relatorio["runs"]:
        metricas = execucao["metricas"]
        gate = "APROVADO" if metricas["gate_aprovado"] else "REPROVADO"
        total = metricas.get("total_casos", 10)
        print(
            f"[benchmark] {execucao['papel']} | {execucao['modelo']} | gate={gate} "
            f"({metricas.get('gate_scope', 'full')}) | "
            f"leitura={metricas['tarefas_com_uso_correto_de_leitura']}/{total} | "
            f"factual={metricas['respostas_factuais_corretas']}/{total} | "
            f"escrita={metricas['checks_escrita_aprovados']}/{metricas.get('checks_escrita_total', 0)} | "
            f"P50={metricas['latencia_p50_ms']}ms | P95={metricas['latencia_p95_ms']}ms | "
            f"P99={metricas['latencia_p99_ms']}ms"
        )
    print(f"[benchmark] Relatorio: {output}")


def cmd_compare_coverage(args):
    from eyle.devtools.coverage_compare import compare_release_coverage_files

    result = compare_release_coverage_files(
        args.baseline, args.candidate, output_path=args.output,
    )
    status = "APROVADO" if result.get("ok") else "REGRESSÃO"
    print(
        f"[coverage] {status} | baseline_cases={result.get('baseline_cases', 0)} | "
        f"candidate_cases={result.get('candidate_cases', 0)} | "
        f"regressions={len(result.get('regressions') or [])}"
    )
    for item in result.get("regressions") or []:
        print(
            f"[coverage][REGRESSÃO] {item.get('role')}:{item.get('case_id')} | "
            f"{', '.join(item.get('reasons') or [item.get('reason') or 'unknown'])}"
        )
    if args.output:
        print(f"[coverage] Relatório: {args.output}")
    if not result.get("ok"):
        raise SystemExit(1)


def cmd_compare_efficiency(args):
    from eyle.devtools.token_efficiency import compare_token_efficiency_files

    result = compare_token_efficiency_files(
        args.baseline, args.candidate, output_path=args.output, tolerance=args.tolerance,
    )
    status = "APROVADO" if result.get("ok") else "REGRESSÃO"
    print(
        f"[efficiency] {status} | baseline_cases={result.get('baseline_cases', 0)} | "
        f"candidate_cases={result.get('candidate_cases', 0)} | "
        f"regressions={len(result.get('regressions') or [])} | "
        f"tolerance={float(result.get('tolerance', 0)):.0%}"
    )
    for item in result.get("regressions") or []:
        print(
            f"[efficiency][REGRESSÃO] {item.get('role')}:{item.get('case_id')} | "
            f"{', '.join(item.get('reasons') or [item.get('reason') or 'unknown'])}"
        )
    if args.output:
        print(f"[efficiency] Relatório: {args.output}")
    if not result.get("ok"):
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
        print(
            f"[main] Backend LLM online: {diagnostico_llm.get('base_url')}"
            f" ({diagnostico_llm.get('latency_ms')}ms){modelo_txt}"
        )
    else:
        if diagnostico_llm.get("reachable"):
            print(
                f"[main][AVISO] Backend LLM respondeu, mas o preflight falhou: "
                f"{diagnostico_llm.get('detail')}"
            )
        else:
            print(
                f"[main][AVISO] Backend LLM indisponivel: {diagnostico_llm.get('detail')}"
            )
        print(
            "[main][AVISO] O painel e o Worker vao iniciar, mas perguntas podem falhar ate "
            "llm.base_url/model estarem corretos e o servidor ficar pronto."
        )

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
        "benchmark", help="Roda a suite completa ou um smoke subset do gate de utilidade",
    )
    p_benchmark.add_argument(
        "--baseline-model", default=None,
        help="Nome exato do Q4 4B carregado no backend (opcional, so compatibilidade)",
    )
    p_benchmark.add_argument(
        "--output", default=None,
        help="Caminho do relatorio JSON (default: context/benchmark_latest.json)",
    )
    p_benchmark.add_argument(
        "--cases", default=None,
        help=(
            "IDs separados por vírgula para smoke test, por exemplo "
            "greeting,analyze_single_file,edit_confirmed"
        ),
    )
    p_benchmark.set_defaults(func=cmd_benchmark)

    p_compare = sub.add_parser(
        "compare-coverage",
        help="Compara preservação de informação entre dois relatórios de benchmark",
    )
    p_compare.add_argument("baseline", help="Relatório JSON da versão base")
    p_compare.add_argument("candidate", help="Relatório JSON da versão candidata")
    p_compare.add_argument("--output", default=None, help="Salva o relatório de comparação em JSON")
    p_compare.set_defaults(func=cmd_compare_coverage)

    p_efficiency = sub.add_parser(
        "compare-efficiency",
        help="Compara chamadas e tokens entre dois relatórios de benchmark",
    )
    p_efficiency.add_argument("baseline", help="Relatório JSON da versão base")
    p_efficiency.add_argument("candidate", help="Relatório JSON da versão candidata")
    p_efficiency.add_argument(
        "--tolerance", type=float, default=0.10,
        help="Tolerância relativa para tokens (default: 0.10)",
    )
    p_efficiency.add_argument("--output", default=None, help="Salva a comparação em JSON")
    p_efficiency.set_defaults(func=cmd_compare_efficiency)

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
