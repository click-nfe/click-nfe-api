# CNAEs dos escopos

Os CNAEs do cliente usam os campos textuais existentes e o formato canônico:

    0000-0/00 - Descrição do CNAE

O campo `sobreEmpresa.cnaePrincipal` contém um CNAE. O campo
`sobreEmpresa.cnaeSecundario` contém zero ou mais CNAEs, um por linha.

## Correção dos dados existentes

O comando consulta a BrasilAPI pelo CNPJ de cada escopo. Sem `--apply`, ele
somente mostra o plano e não altera o banco.

Um escopo:

    flask scope-cnae repair --scope-id UUID
    flask scope-cnae repair --scope-id UUID --apply

Vários escopos:

    flask scope-cnae repair --scope-id UUID_1 --scope-id UUID_2
    flask scope-cnae repair --scope-id UUID_1 --scope-id UUID_2 --apply

Todos os escopos de uma organização:

    flask scope-cnae repair --all --organization-id UUID_ORGANIZACAO
    flask scope-cnae repair --all --organization-id UUID_ORGANIZACAO --apply

O relatório contém o valor anterior, o novo valor e os estados `UPDATED`,
`ALREADY_CURRENT`, `INVALID_CNPJ` ou `LOOKUP_FAILED`.

Quando aplicado, o comando atualiza o `draft`, o `published_snapshot` atual e
o cadastro materializado do cliente. As entradas de `ScopeVersion` não são
alteradas, pois constituem o histórico publicado.

A consulta é feita uma única vez por CNPJ durante cada execução. `--all` exige
explicitamente `--organization-id` para impedir uma atualização global
acidental.

Essa mudança não adiciona colunas nem exige migration de banco.
