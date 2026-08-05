# Eyle Qwen Proxy — porta 8080

Servidor local OpenAI-compatible que recebe as chamadas da Eyle e encaminha para o Qwen pela API do DashScope.

## O que ele faz

- Escuta em `127.0.0.1:8080`.
- Expõe `POST /v1/chat/completions` e `GET /v1/models`.
- Encaminha streaming SSE sem juntar toda a resposta na memória.
- Preserva `content` e `reasoning_content` exatamente como chegam do Qwen.
- Traduz os controles de pensamento da Eyle para `enable_thinking`.
- Desliga pensamento por padrão nas decisões JSON internas do agente.
- Normaliza `response_format` para o formato aceito pelo DashScope.
- Mantém a chave real do DashScope fora da configuração da Eyle.
- Não registra prompts ou respostas nos logs.

## Instalação no Windows

1. Extraia a pasta.
2. Copie `.env.example` para `.env`.
3. Abra `.env` e troque:

```env
DASHSCOPE_API_KEY=sk-coloque-sua-chave-aqui
```

4. Execute:

```powershell
.\iniciar.ps1
```

Ou dê dois cliques em `iniciar.bat`.

## Configuração da Eyle

Use estes valores na configuração de LLM:

```json
{
  "provider": "openai_compatible",
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "qwen3.8-max",
  "api_key": "local-sem-chave",
  "stream_responses": true
}
```

A `api_key` acima é apenas um valor compatível com clientes que exigem uma chave. A chave verdadeira fica somente no `.env` do proxy.

## Teste rápido

Com o servidor aberto:

```powershell
curl.exe http://127.0.0.1:8080/health
```

Teste uma resposta sem instalar o SDK:

```powershell
curl.exe -N http://127.0.0.1:8080/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"qwen3.8-max","messages":[{"role":"user","content":"Quem é você?"}],"stream":true}'
```

Para usar `testar_api.py`, instale também o SDK:

```powershell
.\.venv\Scripts\python.exe -m pip install openai
.\.venv\Scripts\python.exe testar_api.py
```

## Pensamento

Configuração padrão:

```env
DEFAULT_ENABLE_THINKING=true
STRUCTURED_ENABLE_THINKING=false
FORCE_ENABLE_THINKING=false
```

- `DEFAULT_ENABLE_THINKING=true`: respostas comuns podem usar pensamento.
- `STRUCTURED_ENABLE_THINKING=false`: decisões JSON do agente saem direto, sem gastar tempo pensando.
- O proxy também entende `reasoning_effort=none` e `chat_template_kwargs.enable_thinking=false` enviados pela Eyle.
- `FORCE_ENABLE_THINKING=true` ignora tudo e força pensamento em todas as chamadas; não é recomendado para a Eyle.

O proxy não converte `reasoning_content` em resposta comum. Ele apenas repassa os blocos. A Eyle decide quando usar esse conteúdo.

## Segurança

Por padrão, o servidor escuta apenas em `127.0.0.1`, portanto não fica disponível para outros computadores.

Caso altere para `0.0.0.0`, defina obrigatoriamente:

```env
PROXY_API_KEY=uma-chave-local-bem-grande
```

Então configure essa mesma chave na Eyle. Não exponha a porta 8080 diretamente na internet.
