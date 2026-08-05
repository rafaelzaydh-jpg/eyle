#!/usr/bin/env python3
"""
main.py
-------
Fluxo principal da Eyle:

  Projeto de 30k-100k+ tokens
            |
            v
  Memoria externa (memory/, sem limite pratico)
            |
            v
  Eyle Agent -> tools -> evidence -> finalizer -> validation
            |
            v
  Atualiza tarefa persistente e conversa

Comandos:
    python main.py ingest <pasta_do_projeto> [--nome NOME]
    python main.py perguntar "sua pergunta aqui"
    python main.py agente "objetivo da tarefa"          # Atualizacao Agente / Fase 2
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

from ingest import ingerir, indice_esta_atual
from engine.config_schema import ConfigError, carregar_config_validada

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
WORKSPACE_DIR = os.path.join(BASE_DIR, "workspace")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def carregar_config():
    return carregar_config_validada(CONFIG_PATH)


def carregar_projeto():
    caminho = os.path.join(MEMORY_DIR, "projeto.json")
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _nome_projeto_padrao(caminho):
    """
    Se nenhum --nome foi passado: usa o nome da propria pasta apontada.
    Caso especial do workspace/ (container): se dentro dele existe uma
    unica subpasta (a raiz do projeto real que foi colocada la dentro),
    usa o nome dessa subpasta em vez de "workspace".
    """
    caminho = os.path.normpath(caminho)
    if os.path.abspath(caminho) == os.path.abspath(WORKSPACE_DIR):
        subpastas = [
            d for d in os.listdir(caminho)
            if os.path.isdir(os.path.join(caminho, d)) and not d.startswith(".")
        ]
        if len(subpastas) == 1:
            return subpastas[0]
        return "workspace"
    return os.path.basename(caminho)


def cmd_ingest(args):
    caminho = args.caminho or WORKSPACE_DIR
    e_workspace = os.path.abspath(caminho) == os.path.abspath(WORKSPACE_DIR)
    if not os.path.isdir(caminho):
        print(f"[main] Pasta nao encontrada: {caminho}")
        if e_workspace:
            print("[main] Coloque a raiz do seu projeto dentro de workspace/ e rode novamente.")
        sys.exit(1)
    if e_workspace and not [f for f in os.listdir(caminho) if not f.startswith(".")]:
        print("[main] A pasta workspace/ esta vazia.")
        print("[main] Coloque a raiz inteira do projeto que voce quer analisar dentro de workspace/ e rode novamente.")
        sys.exit(1)
    nome = args.nome or _nome_projeto_padrao(caminho)
    config = carregar_config()
    ingerir(
        caminho, nome, MEMORY_DIR,
        chunk_max_tokens=args.chunk_max_tokens,
        chars_per_token=config.get("context", {}).get("chars_per_token", 4),
        config=config,
    )


def cmd_perguntar(args):
    # Mesmo caminho usado pelo Worker e pelo painel web.
    from engine.engine import processar

    projeto = carregar_projeto()
    if projeto is None:
        print("[main] Nenhum projeto indexado ainda -- respondendo em modo conversa livre.")
        print("       (rode 'python main.py ingest <pasta>' quando quiser falar sobre um projeto)")
    else:
        print(f"[main] Corpus total indexado: {projeto['tokens_estimados_totais']} tokens "
              f"({projeto['arquivos']} arquivos, {projeto['chunks']} chunks)")
    print("[main] Roteando mensagem...")

    resultado = processar(args.pergunta)

    if "erro" in resultado:
        print(f"[main] {resultado['erro']}")
        sys.exit(1)

    roteador = resultado.get("roteador", {})
    print(f"[main] Pipeline: {roteador.get('tipo')} ({roteador.get('motivo')})")

    print("\n" + "=" * 60)
    print("RESPOSTA")
    print("=" * 60)
    print(resultado["resposta"])
    print("=" * 60)

    metricas_validacao = {
        nome: resultado.get(nome)
        for nome in ("citation_validity", "coverage", "grounding")
        if resultado.get(nome) is not None
    }
    if metricas_validacao:
        print("\n[validation] " + " | ".join(f"{nome}: {valor}" for nome, valor in metricas_validacao.items()))
        for aviso in resultado.get("avisos", []):
            print(f"[validation][AVISO] {aviso}")
    conclusao_agente = resultado.get("agente_conclusao") or {}
    if conclusao_agente:
        gate = (conclusao_agente.get("completion_gate") or {}).get("code")
        print(
            "\n[agente] "
            f"task_id={conclusao_agente.get('task_id')} | "
            f"leitura={conclusao_agente.get('read_status', 'not_read')} | "
            f"tools={conclusao_agente.get('tools_called', [])} | gate={gate}"
        )
        if conclusao_agente.get("fallback_cause"):
            print(f"[agente] fallback={conclusao_agente['fallback_cause']}")


def cmd_agente(args):
    # A CLI explicita usa o mesmo ponto de entrada que perguntar/Worker, apenas
    # forca o alto nivel "agente" para depuracao deliberada.
    from engine.engine import processar

    config = carregar_config()
    rollout = config.get("agent", {}).get("rollout_mode", "full")
    projeto = carregar_projeto()
    if projeto is None:
        print(
            "[main] Aviso: nenhum projeto indexado ainda -- as tools que tocam o codigo real "
            "(read_file/find_symbol/test_patch_dry_run/run_tests/apply_patch) vao devolver erro."
        )
    print("[main] Executando o Agente...")

    resultado = processar(args.objetivo, forcar_tipo="agente")

    if "erro" in resultado:
        print(f"[main] {resultado['erro']}")
        sys.exit(1)

    roteador = resultado.get("roteador", {})
    print(f"[main] Pipeline: {roteador.get('tipo')} ({roteador.get('motivo')})")
    print(f"[main] Status do agente: {resultado.get('agente_status')}")

    print("\n" + "=" * 60)
    print("RESPOSTA")
    print("=" * 60)
    print(resultado["resposta"])
    print("=" * 60)
    conclusao = resultado.get("agente_conclusao") or {}
    print(
        f"[main] task_id={conclusao.get('task_id')} | "
        f"leitura={conclusao.get('read_status', 'not_read')} | "
        f"tools={conclusao.get('tools_called', [])} | "
        f"gate={(conclusao.get('completion_gate') or {}).get('code')}"
    )
    if conclusao.get("fallback_cause"):
        print(f"[main] fallback={conclusao['fallback_cause']}")


def cmd_status(args):
    from engine import queue, telemetry

    config = carregar_config()
    projeto = carregar_projeto()
    if projeto is None:
        print("[main] Nenhum projeto indexado ainda.")
    else:
        print(json.dumps(projeto, ensure_ascii=False, indent=2))
        atual = indice_esta_atual(projeto, config)
        if atual is True:
            print("\n[main] Indice: atualizado (fingerprint do conteudo confere).")
        elif atual is False:
            print("\n[main] Indice: DESATUALIZADO; rode ingest novamente.")
        else:
            print("\n[main] Indice: estado desconhecido (memoria legada ou fonte indisponivel).")

    hist_path = os.path.join(MEMORY_DIR, "historico.json")
    if os.path.exists(hist_path):
        with open(hist_path, "r", encoding="utf-8") as f:
            hist = json.load(f)
        print(f"\n[main] {len(hist.get('decisoes', []))} decisoes registradas no historico.")

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
    from engine.benchmark import rodar_benchmark

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
            f"escrita={metricas['checks_escrita_aprovados']}/5 | "
            f"P50={metricas['latencia_p50_ms']}ms | P95={metricas['latencia_p95_ms']}ms | "
            f"P99={metricas['latencia_p99_ms']}ms"
        )
    print(f"[benchmark] Relatorio: {output}")


def cmd_serve(args):
    # Sobe o Worker permanente (thread) + o Flask (web/routes.py). O
    # navegador so fala com o Flask a partir daqui; fechar a aba nao
    # interrompe o Worker.
    from engine.worker import iniciar_em_thread
    from llm.executar import diagnosticar_backend
    from web.routes import app, obter_api_token, origem_api_token

    config = carregar_config()
    projeto = carregar_projeto()
    if projeto is None:
        print("[main] Aviso: nenhum projeto indexado ainda. Rode 'python main.py ingest ...' "
              "antes de mandar perguntas pelo navegador.")

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

    p_ingest = sub.add_parser("ingest", help="Indexa uma pasta de projeto na memoria")
    p_ingest.add_argument("caminho", nargs="?", default=None,
                           help="Pasta do projeto a indexar (default: workspace/)")
    p_ingest.add_argument("--nome", default=None)
    p_ingest.add_argument("--chunk-max-tokens", type=int, default=400)
    p_ingest.set_defaults(func=cmd_ingest)

    p_perguntar = sub.add_parser("perguntar", help="Conversa ou executa o Agente Eyle conforme o pedido")
    p_perguntar.add_argument("pergunta")
    p_perguntar.set_defaults(func=cmd_perguntar)

    p_agente = sub.add_parser(
        "agente",
        help="Executa explicitamente uma tarefa no mesmo Agente usado pela CLI e pelo painel",
    )
    p_agente.add_argument("objetivo")
    p_agente.set_defaults(func=cmd_agente)

    p_status = sub.add_parser("status", help="Mostra estatisticas da memoria indexada")
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
            "01_audio_14_linhas,03_dois_arquivos,06_edicao_confirmada"
        ),
    )
    p_benchmark.set_defaults(func=cmd_benchmark)

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
