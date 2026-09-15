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
- Docker Compose para o banco local

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

### 2. Iniciar o PostgreSQL

```bash
docker compose up -d postgres
docker compose ps
```

O banco ficará disponível em `localhost:5432`, utilizando por padrão:

- banco: `click_nfe`
- usuário: `click_nfe`
- senha: `click_nfe`

Essas credenciais são exclusivas do ambiente local e podem ser alteradas no `.env`.

### 3. Preparar o ambiente Python

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

### 4. Executar a API

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

### 5. Executar os testes

```bash
python -m pytest -q
```

## Migrations

O Flask-Migrate está inicializado e a revisão `8c964dc2a0e2` representa o schema
inicial completo do Click NFe em um banco novo. Ela cria 28 tabelas e não contém
as estruturas legadas de escopos, prepostos ou configurações Casco.

Para aplicar a baseline no PostgreSQL do Docker:

```bash
docker compose up -d postgres
docker compose ps
flask --app wsgi.py db upgrade
flask --app wsgi.py db current
```

O resultado esperado de `db current` é:

```text
8c964dc2a0e2 (head)
```

### Criar o acesso administrativo local

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
| `DATABASE_URL` | Conexão SQLAlchemy com PostgreSQL | banco do `compose.yaml` |
| `SECRET_KEY` | Assinatura dos tokens JWT | obrigatória |
| `CORS_ORIGINS` | Origens permitidas, separadas por vírgula | `http://localhost:3000` |
| `JWT_ACCESS_EXPIRES_SECONDS` | Validade do access token | `3600` |
| `JWT_REFRESH_EXPIRES_SECONDS` | Validade do refresh token | `604800` |
| `NFE_XSD_PATH` | Caminho alternativo para o XSD da NF-e | schema incluído na aplicação |
| `DEV_ADMIN_*` | Valores opcionais para o bootstrap administrativo local | consultar `.env.example` |

As variáveis `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` e `DB_PASSWORD` continuam aceitas como fallback quando `DATABASE_URL` não for fornecida.

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
