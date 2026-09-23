# Click NFe API

API do Click NFe para preparação, validação e futura emissão de NF-e de importação a partir de dados da DUIMP.

## Stack

- Python 3.12
- Flask
- Flask-SQLAlchemy
- Flask-Migrate
- Marshmallow
- PostgreSQL 16
- JWT com access token e refresh token
- Docker Compose para API, migrations e banco local

## Desenvolvimento local

### 1. Preparar as variáveis

No Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

No Linux ou macOS:

```bash
cp .env.example .env
```

Edite o arquivo `.env` e substitua `SECRET_KEY` por uma chave local longa e aleatória.

### 2. Iniciar o ambiente completo

```bash
docker compose up --build -d
docker compose ps
```

O Compose executa o ambiente na seguinte ordem:

1. aguarda o healthcheck do PostgreSQL;
2. executa `flask db upgrade` no serviço descartável `migrate`;
3. inicia a API com Gunicorn e aguarda o banco responder em `/health/ready`.

A API ficará disponível em `localhost:5000`. O banco ficará disponível em
`localhost:5432`, utilizando por padrão:

- banco: `click_nfe`
- usuário: `click_nfe`
- senha: `click_nfe`

Essas credenciais são exclusivas do ambiente local e podem ser alteradas no `.env`.
Dentro da rede Docker, a API acessa o banco pelo hostname `postgres`; o
`DATABASE_URL` com `localhost` continua reservado para a execução Python no host.

### 3. Criar o acesso administrativo local

Depois que o serviço `api` estiver saudável, execute:

```bash
docker compose exec api flask --app wsgi.py dev seed-admin
```

Os dados da organização, nome e e-mail são lidos de `DEV_ADMIN_*` no `.env`.
A senha é solicitada e confirmada sem aparecer no terminal.

### 4. Validar o ambiente

```bash
curl http://localhost:5000/health
curl http://localhost:5000/health/ready
docker compose logs --tail=100 api
```

Respostas esperadas:

```json
{"status":"ok"}
{"database":"ok","status":"ok"}
```

O frontend executado no host deve usar:

```dotenv
API_URL=http://127.0.0.1:5000
```

### 5. Executar os testes no container

```bash
docker compose --profile tools run --build --rm test
```

O alvo `test` instala as dependências de desenvolvimento sem incluí-las na
imagem final da API.

### Desenvolvimento Python fora do container

Se preferir executar somente o PostgreSQL no Docker, use:

```bash
docker compose up -d postgres
```

Em seguida, prepare o ambiente Python no host.

No Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

No Linux ou macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Execute a API:

```bash
flask --app wsgi.py run --debug --port 5000
```

Validação rápida:

```bash
curl http://localhost:5000/health
```

Resposta esperada:

```json
{"status":"ok"}
```

Execute os testes:

```bash
python -m pytest -q
```

## Migrations

O Flask-Migrate está inicializado e a revisão `8c964dc2a0e2` representa o schema
inicial completo do Click NFe em um banco novo. Ela cria 28 tabelas e não contém
as estruturas legadas de escopos, prepostos ou configurações Casco.

No fluxo completo, o serviço `migrate` aplica automaticamente a baseline antes
de iniciar a API. Para executar a migration manualmente:

```bash
docker compose run --rm migrate
docker compose exec api flask --app wsgi.py db current
```

O resultado esperado de `db current` é:

```text
8c964dc2a0e2 (head)
```

### Bootstrap fora do container

Com `APP_ENV=development`, crie ou atualize uma organização e seu primeiro
administrador pelo comando idempotente:

```bash
flask --app wsgi.py dev seed-admin \
  --organization-name "Click NFe Demo" \
  --organization-slug "click-nfe-demo" \
  --name "Administrador Local" \
  --email "admin@clicknfe.local"
```

A senha é solicitada e confirmada sem aparecer no terminal. Ela não deve ser
incluída no Git. Executar novamente o comando atualiza o usuário e permite
trocar sua senha local.

O comando recusa execução quando `APP_ENV` não é `development`. A API também
não expõe cadastro público: organizações e administradores são provisionados
por processo controlado.

Para remover integralmente o schema em um banco local descartável:

```bash
flask --app wsgi.py db downgrade base
```

Não execute o downgrade em um banco que contenha dados que devam ser
preservados.

## Configuração

| Variável | Finalidade | Padrão local |
|---|---|---|
| `DATABASE_URL` | Conexão SQLAlchemy no host ou na nuvem | banco local no host |
| `API_PORT` | Porta publicada pelo Compose | `5000` |
| `SECRET_KEY` | Assinatura dos tokens JWT | obrigatória |
| `CORS_ORIGINS` | Origens permitidas, separadas por vírgula | `http://localhost:3000` |
| `JWT_ACCESS_EXPIRES_SECONDS` | Validade do access token | `3600` |
| `JWT_REFRESH_EXPIRES_SECONDS` | Validade do refresh token | `604800` |
| `BRASIL_API_BASE_URL` | Provedor público usado na consulta pontual de CNPJ | `https://brasilapi.com.br/api` |
| `BRASIL_API_TIMEOUT_SECONDS` | Limite da consulta pública de CNPJ | `8` |
| `VIA_CEP_BASE_URL` | Provedor primário usado na consulta pontual de CEP | `https://viacep.com.br` |
| `CEP_LOOKUP_TIMEOUT_SECONDS` | Limite individual por provedor de CEP | `4` |
| `CEP_CACHE_TTL_SECONDS` | Validade do endereço em cache antes de nova consulta | `2592000` |
| `PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER` | Destino das credenciais cadastradas pela organização | `local_encrypted_file` |
| `PORTAL_UNICO_LOCAL_SECRET_DIR` | Diretório do cofre local do Portal Único | `/app/data/portal-unico` |
| `PORTAL_UNICO_LOCAL_SECRET_KEY` | Chave Fernet exclusiva do cofre do Portal Único | derivada de `SECRET_KEY` no desenvolvimento |
| `PORTAL_UNICO_TIMEOUT_SECONDS` | Limite da autenticação no Portal Único | `30` |
| `NFE_CERTIFICATE_STORAGE_PROVIDER` | Destino dos novos uploads A1 | `local_encrypted_file` |
| `NFE_LOCAL_CERTIFICATE_DIR` | Diretório do cofre local criptografado | `/app/data/certificates` |
| `NFE_LOCAL_CERTIFICATE_KEY` | Chave Fernet exclusiva do cofre local | derivada de `SECRET_KEY` no desenvolvimento |
| `NFE_CERTIFICATE_MAX_BYTES` | Tamanho máximo do arquivo `.pfx`/`.p12` | `2097152` |
| `NFE_XSD_PATH` | Caminho alternativo para o XSD da NF-e | schema incluído na aplicação |
| `DEV_ADMIN_*` | Valores opcionais para o bootstrap administrativo local | consultar `.env.example` |
| `WEB_CONCURRENCY` | Processos Gunicorn | `2` |
| `GUNICORN_THREADS` | Threads por processo | `4` |
| `GUNICORN_TIMEOUT_SECONDS` | Timeout de requisição do Gunicorn | `120` |

As variáveis `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` e `DB_PASSWORD` continuam aceitas como fallback quando `DATABASE_URL` não for fornecida.

A rota autenticada `GET /clients/lookup/cnpj/<cnpj>` faz uma consulta pontual
à BrasilAPI para auxiliar o cadastro. Ela não executa varreduras, não persiste a
resposta do provedor e retorna somente os dados cadastrais usados pelo formulário.

A rota autenticada `GET /fiscal-reference/postal-codes/<cep>` consulta primeiro
o ViaCEP e usa a BrasilAPI como alternativa. O endereço normalizado, inclusive o
código IBGE do município e o código BACEN do Brasil, é armazenado no PostgreSQL.
O cache reduz chamadas externas e um registro expirado pode ser utilizado como
fallback somente quando todos os provedores estiverem indisponíveis.

### Credenciais do Portal Único no ambiente local

As credenciais pertencem à organização, não ao cliente. Um administrador pode
configurar o par de chaves em:

`PUT /organizations/me/integrations/portal-unico`

```json
{
  "client_id": "<Client-Id>",
  "client_secret": "<Client-Secret>"
}
```

A API criptografa o par no volume Docker `click_nfe_portal_unico_data` e grava
no PostgreSQL somente uma referência `local:` e metadados não secretos. O
Client-Secret não é devolvido pela API. Depois do cadastro, valide a autenticação
real com:

`POST /organizations/me/integrations/portal-unico/test`

O estado público pode ser consultado por usuários autenticados em:

`GET /organizations/me/integrations/portal-unico`

Os estados são `not_configured`, `pending_test`, `connected`, `error` e
`inactive`. A consulta de DUIMP só deve ser liberada no frontend quando
`ready_for_duimp=true`.

Para usar uma chave própria no cofre local, gere uma Fernet e mantenha o valor
estável no `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
PORTAL_UNICO_LOCAL_SECRET_KEY=valor-gerado
```

Se essa chave ou a `SECRET_KEY` usada como fallback for alterada, as credenciais
locais existentes não poderão ser descriptografadas. A camada de persistência é
um `PortalCredentialStore`; em produção ela será substituída pelo Google Secret
Manager sem alterar o contrato HTTP nem armazenar o segredo no banco.

### Certificado eCNPJ A1 no ambiente local

A rota autenticada de administrador

`POST /clients/<client_id>/fiscal-certificates/upload`

recebe `multipart/form-data` com `certificate`, `password` e `environment`.
São aceitos arquivos `.pfx` e `.p12` de até 2 MB. Antes da persistência, a API:

1. abre o PKCS#12 com a senha informada;
2. confirma a presença de chave privada RSA e permissão de assinatura;
3. confere a validade e o CNPJ contra o perfil fiscal padrão do cliente;
4. rejeita certificados duplicados pelo fingerprint SHA-256;
5. criptografa certificado e senha separadamente no cofre local;
6. grava no PostgreSQL somente referências e metadados não secretos.

O volume Docker `click_nfe_certificate_data` preserva os arquivos criptografados
entre recriações do container. Ele não deve ser versionado nem disponibilizado
por servidor web. A senha nunca é incluída em resposta ou log.

Por padrão, a chave do cofre local é derivada da `SECRET_KEY`. Para desacoplar
as chaves, gere uma Fernet e mantenha seu valor estável no `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
NFE_LOCAL_CERTIFICATE_KEY=valor-gerado
```

Se essa chave for perdida ou alterada, os certificados locais existentes não
poderão ser descriptografados. Em produção, novos uploads deverão usar uma
implementação de `CertificateUploadStore` para o Google Secret Manager. O
contrato do frontend e o cadastro fiscal permanecem os mesmos; apenas o provider
e as referências internas mudam para `gcp:NOME@VERSAO`.

## Preparação para produção

A imagem executa como usuário sem privilégios, recebe a porta por `PORT`, grava
logs no stdout e não contém dependências de teste. O serviço de migration é
separado da inicialização da API; em produção ele deve ser executado como job da
plataforma antes da nova revisão, evitando que múltiplas instâncias tentem
aplicar migrations simultaneamente.

O `compose.yaml` é exclusivo do desenvolvimento local. Credenciais de produção,
certificados e senhas deverão ser injetados pelo Google Secret Manager, sem
copiar o arquivo `.env` para a imagem.

## Segredos fiscais

Certificados A1, senhas e credenciais do Portal Único não devem ser persistidos diretamente no PostgreSQL. A API armazena somente referências:

- `env:` durante o desenvolvimento local;
- `gcp:` para futura integração com o Google Secret Manager.

## Isolamento entre organizações

Todo usuário operacional do Click NFe pertence obrigatoriamente a uma única
organização. Não há superadministrador global nesta fase do produto.

- não existe cadastro público de organizações ou usuários;
- no desenvolvimento, o primeiro `admin` é criado pelo comando `dev seed-admin`;
- usuários adicionais são criados por um `admin` da própria organização;
- recursos de clientes e do fluxo fiscal são sempre consultados com o
  `organization_id` do usuário autenticado;
- organizações inativas não podem autenticar nem renovar sessões;
- recursos de outra organização são tratados como não encontrados, sem revelar
  sua existência.

## Domínio atual

O backend preserva organizações, usuários, clientes, referências fiscais e o fluxo completo de preparação da NF-e por DUIMP. Funcionalidades herdadas de escopos comerciais, prepostos e o dashboard antigo da Casco não fazem parte do Click NFe.
