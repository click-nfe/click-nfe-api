# Primeira implantação: Supabase, Cloud Run e Vercel

Este processo usa o repositório `click-nfe/click-nfe-api`, seu Dockerfile e `cloudbuild.yaml`. A build gera uma imagem no Artifact Registry, executa **uma** Cloud Run Job de migration e só publica a API se a Job terminar com sucesso. O gatilho de produção exige aprovação. Não insira senhas, URLs com credenciais ou arquivos `.pfx` no Git, no Cloud Build YAML, nas mensagens da build ou em tickets.

## 1. Escolha o banco e prepare os secrets

No painel Supabase, abra **Connect** e copie a URI PostgreSQL completa. Se a GCP não tiver conectividade IPv6 para a conexão direta do projeto gratuito, escolha **Session pooler (porta 5432)**, que disponibiliza IPv4; não use o pooler **Transaction (6543)** para as migrations. Para migration, a conexão direta é preferível quando há IPv6 funcional. O usuário do Session pooler tem o formato `postgres.<project-ref>`; copie a URI, não monte host e usuário por tentativa. Inclua `sslmode=require` na query string (`?sslmode=require` ou `&sslmode=require`). Senhas com caracteres reservados precisam estar codificadas na URI; a própria URI do painel é o ponto de partida.

O projeto GCP precisa estar vinculado a uma conta de faturamento ativa, inclusive para usar cotas gratuitas. Cloud Run, Cloud Build, Artifact Registry, Secret Manager e tráfego podem gerar cobranças; configure um orçamento e alertas de custo no Cloud Billing antes de ativar a trigger.

No projeto GCP, habilite Cloud Run, Cloud Build, Artifact Registry, Secret Manager e Cloud Logging. Crie um repositório Docker no Artifact Registry na região escolhida. Crie os secrets **`click-nfe-database-url`** (URI completa do Supabase) e **`click-nfe-secret-key`** (chave longa, aleatória e distinta da senha do banco). Esta configuração aponta para a **versão 1** de cada secret; para girá-los, altere as referências versionadas no pipeline e revise o acesso antes da próxima build.

Crie a service account `click-nfe-runtime` e conceda `roles/secretmanager.secretAccessor` **somente nesses dois secrets**. A service account da trigger do Cloud Build precisa de permissões para Cloud Build, gravação no Artifact Registry, administração de Cloud Run (serviço e Jobs) e `roles/iam.serviceAccountUser` na `click-nfe-runtime`. Se usar uma conta de build dedicada, conceda também gravação dos logs no Cloud Logging. Evite chave JSON de service account no GitHub. Use a [configuração oficial de IAM do Cloud Run](https://docs.cloud.google.com/run/docs/continuous-deployment) para sua organização.

## 2. Configure a trigger com aprovação

No Cloud Build, conecte o repositório GitHub `click-nfe/click-nfe-api` e crie uma trigger **Push to branch** para `^main$`, usando **Cloud Build configuration file** `/cloudbuild.yaml`, **Require approval** e a conta de build preparada acima. Defina `_REGION`, `_AR_REPOSITORY` e `_SERVICE` na trigger se forem diferentes dos padrões `southamerica-east1`, `click-nfe` e `click-nfe-api`. A região da trigger deve ser a mesma do serviço. A primeira execução pode ser iniciada manualmente depois da integração do PR e revisão dos secrets.

A Job recebe a mesma imagem da API e executa `flask --app wsgi.py db upgrade`; não use `db stamp` nem `db downgrade` para inicializar o Supabase. Ela roda com uma tarefa, sem retentativas automáticas. Inspecione o resultado na aba **Cloud Run > Jobs > click-nfe-api-migrate > Executions** e confirme a revisão em `alembic_version`. Depois, o pipeline publica o serviço. Se a migration falhar, o passo de deploy não roda. Uma migration já aplicada não é desfeita por rollback da imagem.

O pipeline usa no máximo uma instância, um worker Gunicorn e pool SQLAlchemy de dois slots mais um overflow por instância, para começar conservadoramente no plano gratuito. Ajuste a capacidade só depois de medir conexões e latência. A API pública é necessária porque a Vercel faz requisições HTTP aos Route Handlers do backend; autenticação JWT continua protegendo rotas privadas. Para checar a publicação:

```text
GET https://<URL-DO-CLOUD-RUN>/health
GET https://<URL-DO-CLOUD-RUN>/health/ready
```

Ambas devem devolver HTTP 200; a segunda verifica a conexão com o PostgreSQL. A URL do serviço aparece em **Cloud Run > click-nfe-api**. HTTP 503 em `/health/ready` indica falha de acesso ao banco ou projeto pausado. Examine os logs da Job e do serviço sem imprimir `DATABASE_URL`.

**Ordem do código:** a API main já incorporou o PR de usuários/tags (#22), incluindo a migration `f4b8c2d901ae`. Confirme que a versão esperada está em main antes de aprovar a trigger. A primeira execução em banco vazio aplica todas as revisões disponíveis, incluindo os catálogos versionados.

## 3. Aponte a Vercel para a API

No projeto `web-click-nfe` na Vercel, abra **Settings > Environment Variables** e defina `API_URL=https://<URL-DO-CLOUD-RUN>` no ambiente **Production**, sem barra final ou `/api`. Essa variável é usada no servidor Next.js (`lib/api/server-client.ts`); **não** é `NEXT_PUBLIC_API_URL`. O navegador conversa com `/api/*` da Vercel, e a Vercel chama a API Flask. Depois de salvar, faça um **redeploy** da aplicação: mudanças de variáveis não entram em builds já publicadas.

Para Preview, use outra API/banco ou deixe `API_URL` sem configuração até existir um ambiente de testes. Não aponte previews de branches arbitrárias para o banco de produção. O PR do painel (#29) já foi integrado. Teste login e a tela inicial; inspecione logs da Vercel e `/health/ready` se houver 502.

`CORS_ORIGINS` na API só precisa incluir o domínio Vercel se o navegador chamar Flask **diretamente**; o fluxo atual usa Route Handlers same-origin da Vercel. Se passar a fazer chamadas diretas, configure a origem exata, sem caminho, no Cloud Run.

## Primeiro administrador em produção

Em um banco novo, a migration cria as tabelas, mas não cria uma organização ou usuário. Após o deploy da versão que contém o comando `bootstrap-admin`, faça **uma única** execução da Job abaixo. O comando aceita somente `APP_ENV=production`, recusa o banco se já houver qualquer organização ou usuário e grava os dois registros em uma transação. Não use `dev seed-admin` no banco de produção.

No Cloud Shell do projeto `click-nfe-project`, crie um secret exclusivo para a senha inicial. O prompt não a exibe nem a grava no histórico do shell:

```bash
read -rs -p 'Senha inicial do administrador (mínimo 16 caracteres): ' CLICK_NFE_ADMIN_PASSWORD
printf '\n'
printf '%s' "$CLICK_NFE_ADMIN_PASSWORD" | gcloud secrets create click-nfe-bootstrap-admin-password --data-file=-
unset CLICK_NFE_ADMIN_PASSWORD

gcloud secrets add-iam-policy-binding click-nfe-bootstrap-admin-password \
  --member='serviceAccount:click-nfe-runtime@click-nfe-project.iam.gserviceaccount.com' \
  --role='roles/secretmanager.secretAccessor'
```

Informe os dados reais da sua organização e do administrador. O slug deve conter somente letras minúsculas, números e hífens. Evite vírgulas nos nomes ao usar `--set-env-vars`:

```bash
read -r -p 'Nome da organização: ' CLICK_NFE_ORG_NAME
read -r -p 'Slug da organização: ' CLICK_NFE_ORG_SLUG
read -r -p 'Nome do administrador: ' CLICK_NFE_ADMIN_NAME
read -r -p 'E-mail do administrador: ' CLICK_NFE_ADMIN_EMAIL

CLICK_NFE_IMAGE="$(gcloud run services describe click-nfe-api \
  --region=southamerica-east1 \
  --format='value(spec.template.spec.containers.image)')"
printf 'Imagem da API: %s\n' "$CLICK_NFE_IMAGE"
```

Confirme que a imagem retornada é a do **deploy após o merge do PR de bootstrap**, não uma imagem anterior. Configure a Job sem executá-la no mesmo comando:

```bash
gcloud run jobs deploy click-nfe-api-bootstrap \
  --region=southamerica-east1 \
  --image="$CLICK_NFE_IMAGE" \
  --service-account=click-nfe-runtime@click-nfe-project.iam.gserviceaccount.com \
  --command=flask \
  --args=--app,wsgi.py,bootstrap-admin \
  --tasks=1 \
  --max-retries=0 \
  --task-timeout=600s \
  --set-env-vars="APP_ENV=production,BOOTSTRAP_ORGANIZATION_NAME=${CLICK_NFE_ORG_NAME},BOOTSTRAP_ORGANIZATION_SLUG=${CLICK_NFE_ORG_SLUG},BOOTSTRAP_ADMIN_NAME=${CLICK_NFE_ADMIN_NAME},BOOTSTRAP_ADMIN_EMAIL=${CLICK_NFE_ADMIN_EMAIL}" \
  --set-secrets='DATABASE_URL=click-nfe-database-url:1,SECRET_KEY=click-nfe-secret-key:1,BOOTSTRAP_ADMIN_PASSWORD=click-nfe-bootstrap-admin-password:1'

gcloud run jobs execute click-nfe-api-bootstrap \
  --region=southamerica-east1 --wait

gcloud run jobs logs read click-nfe-api-bootstrap \
  --region=southamerica-east1 --limit=50
```

O log esperado é `Bootstrap concluído: organização e administrador criados.` Confirme no Supabase que `organizations` e `users` contêm um registro cada e teste o login no painel. **Somente após confirmar o acesso**, desabilite a versão da senha de bootstrap e remova a Job de uso único:

```bash
gcloud secrets versions disable 1 --secret=click-nfe-bootstrap-admin-password
gcloud run jobs delete click-nfe-api-bootstrap --region=southamerica-east1
```

Mantenha o secret `click-nfe-secret-key`: ele assina as sessões da API e é diferente da senha temporária de bootstrap. Nunca envie a senha inicial nos logs ou em mensagens.

## 4. Limitação dos cofres locais no Cloud Run

Os uploads de certificado A1 e as credenciais do Portal Único cadastradas pelo painel usam atualmente `local_encrypted_file`. O disco do Cloud Run é temporário: arquivos podem sumir ao reiniciar e não são compartilhados por instâncias. Referências `local:` já salvas no PostgreSQL não levam os respectivos arquivos para a nuvem. Com `APP_ENV=production`, a API **recusa uploads e cadastros locais com HTTP 409**, evitando gerar novos registros que seriam perdidos. **Não transmita NF-e nesse serviço** até implantar e validar armazenamento persistente (Secret Manager ou volume apropriado), migrar/reinserir as credenciais e certificados e testar resolução após reiniciar uma instância. Deixe `NFE_SEFAZ_TRANSMISSION_ENABLED=false` (padrão) nesse primeiro deploy.

O código já consegue *ler* referências `gcp:` de Secret Manager para certificados e Portal Único, mas o cadastro pelo painel ainda grava somente no cofre local. Essa migração dos arquivos/fluxos é um checkpoint separado de infraestrutura fiscal.

## Verificações antes de liberar produção

- Confirmar região, conexão escolhida no Supabase e SSL.
- Revisar permissões de cada service account e versões dos dois secrets.
- Aprovar a build apenas depois de conferir o head das migrations na API.
- Confirmar Job bem-sucedida, `/health` e `/health/ready` em 200.
- Configurar `API_URL` Production na Vercel e redeploy; testar login.
- Manter transmissão SEFAZ desligada até persistir certificados e credenciais.
