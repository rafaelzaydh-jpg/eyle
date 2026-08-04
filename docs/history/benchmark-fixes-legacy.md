# Correcoes do benchmark

## Como o benchmark funciona

Cada caso cria um projeto temporario, executa o agente, registra as ferramentas chamadas e compara o estado do projeto antes e depois. O avaliador mede leitura, factualidade, grounding, JSON, latencia, referencias inventadas, falso sucesso e seguranca de escrita.

## Problemas encontrados e corrigidos

1. **Falso positivo de escrita sem autorizacao**: o proprio `trace.jsonl` e caches do Python entravam no snapshot do projeto. O trace agora fica fora da raiz e artefatos internos sao ignorados.
2. **Leitura sem evidencia**: `read_file` retornava conteudo, mas nao produzia faixa, numeracao e hashes aceitos pelo grounding. Agora retorna evidencia verificavel e continua compativel com o contrato antigo.
3. **`find_symbol` sem grounding fresco**: o simbolo encontrado agora e relido do disco e recebe hashes.
4. **Indice global contaminando os casos**: `search_code` agora respeita um `memory_dir` isolado por projeto; o benchmark cria um indice separado para cada caso.
5. **Confirmacao falsa**: qualquer `__user_response__` podia ser interpretada como confirmacao de escrita. A retomada automatica agora acontece apenas quando existe pendencia real de ferramenta `WRITE`.
6. **Metricas de escrita permissivas**: confirmacao, hashes e dry-run agora so sao aprovados quando houve uma pendencia de escrita real e os dados correspondem a ela.
7. **Agente parava apesar de o arquivo estar no pedido**: depois de uma chance para o modelo se corrigir, o executor faz a leitura obvia do arquivo/simbolo explicitamente citado, em vez de pedir ao usuario uma informacao que ja existe.
8. **Dependencias incompletas no pacote**: adicionados `requirements-dev.txt`, `requirements.lock` e `requirements-dev.lock`.
9. **Teste de isolamento misturava objetivos**: o teste da copia do sandbox usava apenas 128 MiB e podia falhar por thrashing do runtime antes de testar o isolamento. O teto desse teste foi ajustado sem alterar a politica de producao.

## Validacao feita

- Testes focados no agente: **81/81 aprovados**.
- Suite nao-web: **156/156 aprovados**.
- Benchmark real com LLM: nao executado aqui, pois depende do endpoint local configurado no ambiente de destino.
- Testes web: nao executados aqui porque Flask nao esta instalado neste ambiente.


## Segunda rodada — benchmark de 04/08/2026

10. **Hash de arquivo usado como hash de faixa**: quando `read_file` cobria o arquivo inteiro, o modelo podia copiar `content_hash` para um patch menor. O sistema agora deriva automaticamente o hash da faixa exata e o codigo original a partir da evidencia fresca.
11. **Falso `STALE_PATCH` entre sistemas**: hashes de texto agora normalizam CRLF/CR/LF, evitando divergencia artificial entre Windows, WSL, Docker e Linux.
12. **`STALE_PATCH` virava beco sem saida**: a falha agora invalida a evidencia, libera releitura, replaneja e exige nova confirmacao; a confirmacao antiga nunca e reutilizada.
13. **Avaliador literal demais**: os casos de `lower`/"minusculo" e `8`/"eight" agora aceitam equivalentes semanticos controlados, sem transformar o benchmark em avaliacao frouxa.
14. **Simbolo inexistente causava circuit breaker**: `SYMBOL_NOT_FOUND` executado vira uma conclusao negativa grounded, em vez de tres tentativas e pedido desnecessario ao usuario.
15. **Modelo tentava finalizar antes do ciclo de edicao**: o prompt passa a exibir a proxima acao obrigatoria (`dry-run`, `apply_patch`, testes ou releitura) calculada pelo estado do sistema.

## Validacao desta rodada

- Suite nao-web: **161/161 aprovados**.
- Regressao nova dos problemas do benchmark: **5/5 aprovados**.
- Testes web: nao executados porque o ambiente esta sem Flask e sem acesso ao pacote no indice configurado.
- Benchmark real com LLM: depende do endpoint local da instalacao da Eyle e deve ser reexecutado no ambiente de destino.
