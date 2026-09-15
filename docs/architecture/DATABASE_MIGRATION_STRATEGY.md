# Estratégia de migration inicial do Click NFe

Status: baseline inicial criada na revisão `8c964dc2a0e2`.

## Contexto

O Click NFe será iniciado em um PostgreSQL novo, sem dados ou schema legado a
preservar. O histórico Alembic do projeto de origem não será reaproveitado.

## Decisão

A primeira migration é a baseline completa do produto, sem operações de
alteração ou exclusão de tabelas antigas. Ela cria 28 tabelas a partir dos
modelos limpos e exige `organization_id` em todos os usuários.

Sequência local:

1. iniciar o PostgreSQL local com `docker compose up -d postgres`;
2. confirmar a saúde do container com `docker compose ps`;
3. aplicar `flask --app wsgi.py db upgrade` somente no banco local;
4. confirmar `8c964dc2a0e2 (head)` com `flask --app wsgi.py db current`;
5. executar a suíte de testes e os smoke tests da API.

O diretório `migrations` já está inicializado e não se deve executar novamente
`flask db init`. Novas alterações de modelo devem gerar revisões incrementais
com `flask db migrate`.

## Estruturas que não podem aparecer

- `organization_settings`
- `scopes`, `scope_versions`, `scope_assignments`, `scope_services`
- `scope_template`, `scope_prepostos`, `service_catalog`
- `prepostos` e demais tabelas iniciadas por `preposto_`

## Restrições

- nenhuma migration ou `upgrade` é executada automaticamente pela aplicação;
- a primeira aplicação ocorre no PostgreSQL local descartável;
- futuros ambientes usam bancos e credenciais próprios;
- o deploy não executará `upgrade` automaticamente até existir rollback validado;
- dados e documentos reais do cliente não entram em fixtures ou migrações.

## Validações realizadas

- ciclo `upgrade -> downgrade base -> upgrade` em banco vazio descartável;
- correspondência entre as 28 tabelas criadas e o metadata do SQLAlchemy;
- geração offline dos SQLs de upgrade e downgrade para o dialeto PostgreSQL;
- ausência das estruturas legadas listadas acima;
- enums persistidos como `VARCHAR + CHECK`, evitando tipos residuais após
  downgrade.

A execução final no PostgreSQL 16 ocorre pelo container definido em
`compose.yaml`.
