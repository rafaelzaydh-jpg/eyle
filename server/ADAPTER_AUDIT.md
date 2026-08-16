# Auditoria — Eyle Adapter Universal Rev2.8.1

## Arquitetura confirmada

```text
Eyle -> 127.0.0.1:8080 -> Adapter -> API remota OpenAI-compatible -> modelo
```

- A porta 8080 pertence ao Adapter.
- O Adapter não requer nem inicia LLM local.
- Não existe fallback para 127.0.0.1:8000.
- `UPSTREAM_BASE_URL` é obrigatório e representa a API remota escolhida.
- `UPSTREAM_API_KEY` autentica o Adapter perante o provider remoto.
- `DEFAULT_MODEL`/`MODEL_OVERRIDE` definem o modelo sem acoplar a Eyle ao provider.
- Com modelo explícito, `/v1/models` é servido localmente e não exige que o provider implemente model discovery.
- Com `DEFAULT_MODEL=auto`, o Adapter usa `GET <UPSTREAM_BASE_URL>/models` para descobrir um modelo.
- Particularidades OpenAI-compatible podem ser passadas por `UPSTREAM_EXTRA_HEADERS_JSON` e `UPSTREAM_EXTRA_BODY_JSON`.

## Regressão corrigida

O pacote anterior trazia `UPSTREAM_BASE_URL=http://127.0.0.1:8000/v1` como default. Isso fazia uma instalação sem `.env` corretamente configurado tentar falar com uma LLM local inexistente e produzir `502 model_discovery_failed`.

A correção remove completamente esse default. Configuração ausente agora falha cedo e de forma explícita.

## Validação

`python -m pytest -q`: **20 passed**.

Os testes cobrem, entre outros pontos, schema Rev2.8.1, structured-output translation/fallback, model discovery, `model=auto`, autenticação loopback da Eyle, contabilização de usage, timeout e a ausência de fallback hardcoded para `127.0.0.1:8000`.
