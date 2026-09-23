# Credenciais do Portal Único

## Finalidade

A consulta de DUIMP usa o par de chaves gerado no Portal Único para a pessoa
jurídica intermediadora. O certificado A1 utilizado para gerar esse par não é
carregado pela API.

No Google Secret Manager, os secrets esperados são:

- `PORTAL_UNICO_CLIENT_ID`
- `PORTAL_UNICO_CLIENT_SECRET`

O registro corporativo em `external_provider_connections` usa `importer_id`
nulo para compartilhar a conexão por todos os processos da organização. O
banco armazena somente `credentials_ref`, status, healthcheck e configuração
não secreta.

Em produção, a referência deve seguir:

```json
{
  "provider": "portal_unico",
  "environment": "production",
  "auth_type": "api_key",
  "credentials_ref": "gcp:PORTAL_UNICO",
  "config_json": {
    "role_type": "IMPEXP"
  }
}
```

O Client-Id completo e o Client-Secret nunca são retornados nas rotas públicas.

## Cloud Run

Variáveis:

```text
GOOGLE_CLOUD_PROJECT=<id-do-projeto>
PORTAL_UNICO_SECRET_VERSION=1
```

A service account do Cloud Run recebe
`roles/secretmanager.secretAccessor` apenas nos dois secrets. Os valores não
devem ser injetados como variáveis de ambiente nem registrados em logs.

## Docker local

Durante o desenvolvimento, um administrador configura as chaves pela rota:

```http
PUT /organizations/me/integrations/portal-unico
Content-Type: application/json

{
  "client_id": "<Client-Id>",
  "client_secret": "<Client-Secret>"
}
```

A implementação `LocalEncryptedFilePortalCredentialStore` grava um único JSON
criptografado com Fernet no volume `click_nfe_portal_unico_data`. A referência
`local:<organization_id>/<token>.portal.json.enc` fica no banco. A resposta
contém apenas os quatro últimos caracteres do Client-Id e nunca inclui o
Client-Secret ou a referência interna.

Depois do cadastro, execute:

```http
POST /organizations/me/integrations/portal-unico/test
```

O teste autentica no endpoint oficial de produção, atualiza
`last_healthcheck_at` e define o estado público como `connected` ou `error`.
Erros persistidos e retornados são normalizados para impedir que uma resposta
externa ecoe o segredo.

As variáveis locais são:

```text
PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER=local_encrypted_file
PORTAL_UNICO_LOCAL_SECRET_DIR=/app/data/portal-unico
PORTAL_UNICO_LOCAL_SECRET_KEY=<Fernet opcional>
PORTAL_UNICO_TIMEOUT_SECONDS=30
```

Se a chave Fernet ficar vazia, somente no desenvolvimento ela é derivada de
`SECRET_KEY`. Ambas devem permanecer estáveis enquanto os arquivos locais forem
utilizados.

O resolver legado `env:` continua disponível para registros criados
manualmente, mas não é usado pelo formulário organizacional.

Nenhum arquivo de credenciais, chave de acesso ou `.env` deve ser versionado.

## Migração futura e rotação

O contrato de escrita é `PortalCredentialStore` e o de leitura é
`PortalCredentialResolver`. Na implantação no Google Cloud, o writer deverá
criar novas versões dos dois secrets e devolver `gcp:PORTAL_UNICO`; o fluxo de
DUIMP continuará usando a mesma conexão organizacional.

Cada rotação cria uma nova versão nos dois secrets. Depois de validar as duas
versões, altere `PORTAL_UNICO_SECRET_VERSION` para o mesmo número e implante
uma nova revisão do Cloud Run. Não use versões diferentes para Client ID e
Client Secret.
