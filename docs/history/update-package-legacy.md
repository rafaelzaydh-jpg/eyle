# Pacote de atualização seguro — Atualização 50.1

Este pacote contém o código completo das Atualizações 10-50.1 e pode ser extraído
sobre uma instalação existente.

Os conteúdos mutáveis de `memory/` e `context/` são deliberadamente excluídos.
Isso preserva índice, conversa, histórico, fila SQLite, tarefas, pendências,
token web, traces e backups. As pastas são criadas automaticamente quando
necessárias; em instalação nova, execute `python ingest.py /caminho/do/projeto`
antes de analisar o projeto.

## Revisão 50.1 — correção do agente sem resposta/formato inválido

Foi corrigido o caso observado em que tarefas como “analise o projeto” demoravam
e terminavam em “formato inválido”:

- o `config.json` do ZIP ainda estava em 4080/700/180; agora usa janela 8192,
  saída 1500 e timeout 600, com `model: auto`;
- decisões do Agente não usam mais o cache de respostas. Um JSON inválido antigo
  não fica sendo repetido para sempre;
- llama-server recebe um schema JSON explícito para o protocolo da Eyle;
- nas decisões estruturadas, a Eyle tenta desligar o modo thinking
  (`reasoning_effort=none` e `enable_thinking=false`), com fallback para builds
  antigas;
- quando `content` vem vazio, a camada aceita `reasoning_content` como último
  recurso para o parser, em vez de fingir que o modelo não respondeu;
- o parser agora encontra o primeiro objeto JSON válido mesmo quando o modelo
  escreve texto ou outro objeto antes dele.

Inicie o servidor com `--ctx-size 10240`; a Eyle usa até 8192 e mantém folga.

## Atualização 50 — compatibilidade básica com llama-server

A camada OpenAI-compatible agora:

- consulta `/v1/models` e usa automaticamente o único modelo carregado quando
  o nome de `config.json` ficou antigo;
- tenta `response_format` e, se o servidor o rejeitar, repete sem esse campo;
- se o template rejeitar a mensagem `system`, incorpora as instruções na
  mensagem `user`;
- memoriza essas capacidades durante a execução, evitando repetir tentativas;
- remove blocos visíveis `<think>`, `<analysis>` ou `<reasoning>` somente nas
  respostas JSON do Agente.

Não foi criado perfil por família de modelo nem tool calling nativo: a Eyle
continua usando seu contrato JSON interno, preservando a arquitetura existente.
