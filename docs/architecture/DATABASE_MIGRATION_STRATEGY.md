# Estratégia de migration inicial do Click NFe

Status: definida após a remoção do domínio legado.

## Contexto

O Click NFe será iniciado em um PostgreSQL novo, sem dados ou schema legado a
preservar. O histórico Alembic do projeto de origem não será reaproveitado.

## Decisão

A primeira migration deve ser gerada somente depois da integração e validação
dos modelos limpos. Ela será a baseline completa do produto, sem operações de
alteração ou exclusão de tabelas antigas.

Sequência local:

1. iniciar o PostgreSQL local com `docker compose up -d postgres`;
2. executar `flask --app wsgi.py db init`;
3. gerar `flask --app wsgi.py db migrate -m "create initial click nfe schema"`;
4. revisar manualmente o arquivo gerado;
5. confirmar a ausência das tabelas legadas listadas abaixo;
6. aplicar `flask --app wsgi.py db upgrade` somente no banco local;
7. executar a suíte de testes e os smoke tests da API;
8. versionar o diretório `migrations` em uma nova branch.

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

## Critério para o próximo checkpoint

A baseline gerada localmente deve criar o schema completo em um banco vazio e
permitir que a suíte de testes continue aprovada antes de ser publicada.
