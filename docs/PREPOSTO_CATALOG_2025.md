# Catálogo de prepostos — Relação 2025

Este checkpoint importa somente registros aprovados e completos da planilha de
conciliação. O arquivo de carga é privado porque contém CPFs e não deve ser
adicionado ao Git.

## Recorte aprovado

- 17 prepostos: 7 existentes e 10 novos;
- 23 contatos;
- 25 localidades do Word;
- 43 definições de tarifa, expandidas para 68 combinações de localidade/operação;
- 51 linhas de credenciados, conciliadas em 49 CPFs únicos;
- 78 vínculos entre credenciados, prepostos e localidades.

Ficam fora da carga: Grupo Casco/Curitiba, Sea Commerce/Salvador, Adrica
Comex/Rio de Janeiro, o cadastro de Pecém, o cadastro de Maceió e Pedro
Barbosa/Vila do Conde. Esses registros serão completados posteriormente pela
interface de manutenção.

## Execução

1. Faça backup das tabelas `prepostos`, `preposto_contatos` e
   `preposto_localidades`.
2. Execute `docs/migrations/20260831_preposto_catalog_2025.sql`.
3. Rode o importador sem `--apply`:

```bash
flask preposto-catalog import-2025 \
  /caminho/privado/prepostos_2025_approved.private.json \
  --organization-id <UUID_DA_ORGANIZACAO>
```

Para o extrato usado na conciliação, o dry-run esperado é:

| Entidade | Criados | Atualizados | Removidos/desativados |
|---|---:|---:|---:|
| Prepostos | 10 | 7 | 0 |
| Contatos | 19 | 4 | 4 |
| Localidades | 15 | 10 | 0 |
| Tarifas | 68 | 0 | 0 |
| Credenciados | 49 | 2 | 0 |
| Vínculos | 78 | 0 | 0 |

Os dois updates de credenciados representam CPFs repetidos em mais de uma
seção; uma identidade é preservada com múltiplos vínculos. Os quatro contatos
removidos são registros divergentes substituídos pela fonte Word aprovada.

Se os números divergirem, não use `--apply` antes de conferir a base. Estando
corretos, repita o comando com confirmação:

```bash
flask preposto-catalog import-2025 \
  /caminho/privado/prepostos_2025_approved.private.json \
  --organization-id <UUID_DA_ORGANIZACAO> \
  --apply
```

O comando é idempotente. Uma nova execução sobre a mesma base não cria cópias.
