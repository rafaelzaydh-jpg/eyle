# Eyle DeepSeek Adapter Rev2.5.2

Adapter dedicado à Eyle Rev2.5.2 ECC para o endpoint estável OpenAI-compatible da DeepSeek.

## Contrato estruturado

A Eyle envia seu contrato canônico como `response_format.type=json_schema` para o adapter. O adapter **não encaminha `json_schema` à DeepSeek**. Ele converte a chamada para `response_format={"type":"json_object"}`, injeta uma gramática JSON compacta derivada do schema recebido e valida a resposta localmente com JSON Schema Draft 2020-12.

Rev2.5.2 estende essa gramática com o sidecar de **Objective State** além da Memory Graph:

```json
{"objective":{"disposition":"unchanged","state":null}}
```

ou:

```json
{
  "objective": {
    "disposition": "updated",
    "state": {
      "summary": "Satisfy the compound request",
      "status": "active",
      "children": [],
      "constraints": []
    }
  }
}
```

A gramática Objective e a gramática Memory (`remember`, `revise`, `relate`, `archive`, `supersede`, `retire_relation`, aliases e supports) são derivadas do **schema canônico recebido**, não de uma lista paralela simplificada.

## Repair

Quando a resposta não satisfaz o schema, o adapter discrimina primeiro o ramo ECC real por `type`, depois valida Objective e Memory em seus ramos específicos. Assim os repairs recebem erros acionáveis como:

```text
$.objective.state.status: required property missing
$.memory.operations[0].scope: required property missing
```

em vez de mensagens genéricas de `oneOf`.

O repair é limitado por `STRUCTURED_REPAIR_ATTEMPTS`, preserva a decisão ECC e é instruído a não apagar uma atualização genuína de Objective/Memory apenas para passar na validação.

## Testes

```bash
python -m pytest -q
```

A suíte usa `tests/fixtures/eyle_rev252_ecc_schema.json`, snapshot do **schema ECC canônico completo** gerado de `llm/structured.py` da Eyle Rev2.5.2.
