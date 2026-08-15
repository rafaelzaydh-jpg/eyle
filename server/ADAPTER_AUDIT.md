# Eyle DeepSeek Adapter Rev2.5.2 — Auditoria

**Destino:** Eyle 2.7.5 Rev2.5.2 ECC + DeepSeek stable Chat Completions.

## Mudanças

1. **Objective grammar derivada do schema** — `objective.disposition`, `objective.state`, `children`, `constraints`, `status` e `outcome` são ensinados à DeepSeek sem hardcode de domínio.
2. **Memory grammar preservada** — remember/revise/relate/archive/supersede/retire_relation, aliases e supports continuam completos.
3. **JSON Object upstream** — o adapter continua usando o mecanismo oficialmente disponível no endpoint estável e valida localmente o schema canônico Draft 2020-12.
4. **Diagnóstico discriminado de Objective** — repair recebe caminhos específicos em Objective State, evitando umbrella errors de `oneOf`.
5. **Repair preserva semântica** — não deve apagar uma atualização genuína de Objective ou Memory apenas para tornar o JSON válido.
6. **Schema fixture canônico** — `tests/fixtures/eyle_rev252_ecc_schema.json` vem diretamente da release Eyle usada para construir o artefato.

## Verificação

- 11 testes do adapter passam na árvore de construção.
- O schema fixture é comparado bit-a-bit com `schema_for_profile("ecc")` antes do empacotamento final.
