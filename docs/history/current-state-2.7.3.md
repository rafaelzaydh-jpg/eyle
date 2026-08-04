# Estado Atual — Eyle

**Versão da aplicação:** 2.7.3  
**Schema de configuração:** 2.7.3  
**Revisão:** 53.0-speed-cycle-hardening  
**Data:** 2026-08-04

A revisão 53 complementa o fechamento da auditoria feito na revisão 52 e elimina
as lacunas restantes que podiam gerar resposta errada, espera inútil ou ciclo:

- rejeição de múltiplas decisões JSON válidas na mesma resposta;
- cache saneado contra envelopes estruturados de erro e gravação somente após
  validar o orçamento de tokens;
- detector de ciclos curtos por fingerprint de resultado + estado material;
- reserva da fila com teto mesmo sob conflito permanente;
- retrieval reutilizado no ciclo do Analista e early exit para lacunas/buscas repetidas;
- backoff exponencial nos retries do Executor recusados pelo Verify;
- falha de `chmod` do token web registrada em telemetria, sem silêncio.

Continuam ativos os controles da revisão 52: grounding determinístico, watchdog
de processo, consumidores paralelos, deadline/orçamento LLM, rate limiting entre
processos, cache SQLite, telemetria P50/P95/P99, validação de tools/config e
fallbacks observáveis.

**Validação local:** `compileall` aprovado e **202/202 testes executáveis** aprovados;
**1 teste web foi ignorado** porque Flask não está instalado neste ambiente.

**Validações externas ainda necessárias:** benchmark real com o endpoint/modelo LLM
do usuário e execução da suíte web em ambiente com `Flask==3.0.3`. Não há falha
conhecida na suíte executável, mas velocidade real depende do backend e hardware.

Para o histórico anterior, consulte `Atual_Vers#U00e3o.md`.
